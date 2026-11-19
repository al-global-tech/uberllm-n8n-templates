#!/usr/bin/env node
// ad-creative.mjs: ad pictures, clips and voice-overs from one product photo, through the UberLLM API.
// No dependencies. Needs Node 18 or newer and UBERLLM_API_KEY in the environment.
//
//   node ad-creative.mjs ugc <photo>          person holding the product (9:16 still) -> 5 s clip -> voice-over line
//   node ad-creative.mjs variations <photo>   one still per line of a list (backgrounds, angles, hooks)
//   node ad-creative.mjs sheet <rows.csv>     one still + one clip per row of a CSV file or link
//   node ad-creative.mjs spin <photo>         studio still -> slow turntable clip
//   node ad-creative.mjs --self-check         checks the parsing code, calls nothing
//
// <photo> is a file on disk (PNG, JPEG or WebP, up to 10 MB) or a public https:// link.
// Add --dry-run to any recipe to see what it would cost; nothing is made and nothing is charged.
// Every run writes the files and a run.json (model, seconds, cost, size of each step) into --out.
import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync, statSync } from 'node:fs';
import { basename, extname, join } from 'node:path';

const API = (process.env.UBERLLM_BASE_URL || 'https://api.uberllm.dev').replace(/\/+$/, '');
const KEY = process.env.UBERLLM_API_KEY;

// Models that were finishing jobs reliably when this was written (2026-11-19). Change them with
// --image-model / --video-model / --voice-model; `uberllm models` or GET /v1/models?type=video lists what is live.
const DEFAULTS = {
  imageModel: 'google/nano-banana-2-edit',
  videoModel: 'kling/kling-o3-standard-image-to-video',
  // Tried in order only when the first has no free provider right now.
  videoBackups: ['fal-ai/kling-video/o3/standard/image-to-video'],
  voiceModel: 'venice/kokoro-tts',
  voice: 'af_heart',
};
const KEEP = 'Keep the product exactly as it is in the photo: same shape, colours, label and text.';
const PROMPTS = {
  ugcScene:
    `A generic adult holds this product in one hand at chest height in a bright, tidy kitchen, natural window light, phone-camera look, vertical framing with free space above for a headline. ${KEEP}`,
  ugcMotion: 'The person lifts the product slightly toward the camera and turns it a little to show it, with a small natural smile. Handheld phone feel, no cuts.',
  spinScene: `The product stands alone, centred, on a seamless light-grey studio background with a soft shadow under it and even studio light. ${KEEP}`,
  spinMotion: 'The product turns slowly and smoothly on a turntable, one continuous rotation. The camera stays still. Studio light, nothing else moves.',
  variations: [
    'on a sunlit wooden kitchen counter next to a bowl of lemons, morning light',
    'on a gym bench beside a folded towel, cool daylight',
    'on a desk next to a laptop and a notebook, soft office light',
    'held up by a hand against a clear blue sky, outdoors',
  ],
};

// ---------------------------------------------------------------- small tools

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { out._.push(a); continue; }
    const [k, inline] = a.slice(2).split(/=(.*)/s);
    const next = argv[i + 1];
    const v = inline !== undefined ? inline : next !== undefined && !next.startsWith('--') ? argv[++i] : true;
    out[k] = k in out ? [].concat(out[k], v) : v; // a flag given twice becomes a list
  }
  return out;
}

/** CSV text -> list of rows keyed by the header line. Handles quotes, commas and line breaks inside quotes. */
function parseCsv(text) {
  const rows = [];
  let row = [], cell = '', quoted = false;
  const src = text.replace(/^﻿/, '');
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (quoted) {
      if (c === '"' && src[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') quoted = false; else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && src[i + 1] === '\n') i++;
      row.push(cell); cell = '';
      if (row.some((x) => x.trim())) rows.push(row);
      row = [];
    } else cell += c;
  }
  row.push(cell);
  if (row.some((x) => x.trim())) rows.push(row);
  if (!rows.length) return [];
  const head = rows[0].map((h) => h.trim().toLowerCase().replace(/\s+/g, '_'));
  return rows.slice(1).map((r) => Object.fromEntries(head.map((h, i) => [h, (r[i] ?? '').trim()])));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const money = (n) => (n == null ? 'not known yet' : `$${Number(n).toFixed(4).replace(/0+$/, '').replace(/\.$/, '.00')}`);
const list = (v) => (v == null || v === true ? [] : [].concat(v));
const say = (...a) => console.error(...a); // progress goes to stderr; stdout carries the final JSON only

class ApiError extends Error {
  constructor(status, body) {
    const e = body && typeof body === 'object' ? body.error : null;
    super(`${status}: ${(e && (e.message || e.code)) || (typeof body === 'string' ? body.slice(0, 300) : JSON.stringify(body).slice(0, 300))}`);
    this.status = status;
    this.code = e && e.code;
  }
}

async function call(method, path, { json, form, headers = {}, timeoutMs = 240_000, raw = false } = {}) {
  const res = await fetch(path.startsWith('http') ? path : `${API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${KEY}`,
      'X-UberLLM-Source': 'ad-creative',
      ...(json ? { 'Content-Type': 'application/json' } : {}),
      ...headers,
    },
    body: json ? JSON.stringify(json) : form,
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (raw && res.ok) return res;
  const text = await res.text();
  let body = text;
  try { body = JSON.parse(text); } catch { /* not JSON */ }
  if (!res.ok) throw new ApiError(res.status, body);
  return { status: res.status, body, headers: res.headers };
}

/** Width, height and length of a file when ffprobe is installed; {} when it is not. */
function probe(file) {
  const r = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'stream=codec_type,width,height:format=duration', '-of', 'json', file], { encoding: 'utf8' });
  if (r.status !== 0) return {};
  try {
    const j = JSON.parse(r.stdout);
    const v = (j.streams || []).find((s) => s.width);
    const d = Number(j.format && j.format.duration);
    const picture = /\.(png|jpe?g|webp)$/i.test(file);
    return { ...(v ? { width: v.width, height: v.height } : {}), ...(!picture && d > 0 ? { seconds: Math.round(d * 100) / 100 } : {}) };
  } catch { return {}; }
}

async function save(url, file) {
  const res = await call('GET', url, { raw: true, timeoutMs: 300_000 });
  writeFileSync(file, Buffer.from(await res.arrayBuffer()));
  return { file, bytes: statSync(file).size, ...probe(file) };
}

const TYPES = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' };
/** Uploads a picture to your account (kept 24 hours) and returns its id, which any request can use as a source picture. */
async function upload(file) {
  const form = new FormData();
  form.append('file', new Blob([readFileSync(file)], { type: TYPES[extname(file).toLowerCase()] }), basename(file));
  const { body } = await call('POST', '/v1/files', { form });
  return body.id;
}
/** A local photo is uploaded once and used by id; a link is used as it is. */
async function source(photo, dry) {
  if (/^https:\/\//i.test(photo)) return photo;
  if (/^http:\/\//i.test(photo)) throw new Error('The photo link must start with https://');
  if (!existsSync(photo)) throw new Error(`No such file: ${photo}`);
  const type = TYPES[extname(photo).toLowerCase()];
  if (!type) throw new Error('The photo must be a PNG, JPEG or WebP file.');
  if (statSync(photo).size > 10 * 1024 * 1024) throw new Error('The photo is over 10 MB. Make it smaller first.');
  if (dry) return `data:${type};base64,${readFileSync(photo).toString('base64')}`; // a price check uploads nothing
  return upload(photo);
}
/** The still as the clip's first frame. A result link needs your key, so a video model cannot open it:
 *  the saved still is uploaded and the clip starts from the upload. */
const firstFrame = (o, s, fallback) => (o.dry ? fallback : upload(s.file));

// ---------------------------------------------------------------- the three calls

/** One edited picture. A failed try is not charged, so it is simply sent again (up to 3 times). */
async function still(o, image, prompt, aspect, file, label) {
  const req = { model: o.imageModel, prompt, image, n: 1, ...(aspect ? { aspect_ratio: aspect } : {}), ...(o.maxImageCost ? { max_cost: o.maxImageCost } : {}) };
  if (o.dry) {
    const { body } = await call('POST', '/v1/images/edits', { json: { ...req, validate: true } });
    return { step: label, model: o.imageModel, dry_run: true, cost_usd: body.cost_usd, max_cost_usd: body.max_cost_usd };
  }
  const t0 = Date.now();
  let last;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const { body } = await call('POST', '/v1/images/edits', { json: req });
      const url = body.data[0].url;
      // The link is kept about 15 minutes: download now.
      const saved = await save(url, file);
      say(`  ${label}: ${saved.file} (${saved.width ? `${saved.width}x${saved.height}, ` : ''}${money(body.usage && body.usage.cost_usd)})`);
      return { step: label, model: o.imageModel, took_seconds: Math.round((Date.now() - t0) / 1000), cost_usd: body.usage ? body.usage.cost_usd : null, attempts: attempt, url, ...saved };
    } catch (e) {
      last = e;
      const again = !(e instanceof ApiError) || e.status >= 500 || e.status === 429;
      if (!again || attempt === 3) break;
      say(`  ${label}: try ${attempt} did not finish (${e.message}); not charged, trying again`);
      await sleep(3000 * attempt);
    }
  }
  throw new Error(`${label} was not made: ${last.message}`);
}

/** One clip that starts from a picture: start the job, ask every 8 s, download the file. */
async function clip(o, image, prompt, file, label) {
  const req = {
    model: o.videoModel, prompt, image_url: image, duration_seconds: o.seconds, audio: false,
    ...(o.videoBackups.length ? { models: o.videoBackups } : {}),
    ...(o.maxCost ? { max_cost: o.maxCost } : {}),
    metadata: { tool: 'ad-creative', recipe: o.recipe },
  };
  if (o.dry) {
    const { body } = await call('POST', '/v1/video/generations', { json: { ...req, validate: true } });
    return { step: label, model: o.videoModel, dry_run: true, cost_usd: body.cost_usd, max_cost_usd: body.max_cost_usd };
  }
  const t0 = Date.now();
  // The key makes a retry after a dropped connection return the same job, not a second one.
  const { body: job } = await call('POST', '/v1/video/generations', { json: req, headers: { 'Idempotency-Key': randomUUID() } });
  say(`  ${label}: job ${job.id} started on ${job.model}, priced at ${money(job.estimated_cost_usd ?? job.max_cost_usd)} now${o.maxCost ? `; your ceiling is ${money(o.maxCost)}` : ''} (the bill can differ if the job moves to another provider)`);
  let done;
  for (;;) {
    await sleep(8000);
    if (Date.now() - t0 > o.waitMinutes * 60_000) {
      await call('DELETE', job.cancel_url).catch(() => {});
      throw new Error(`${label}: still not ready after ${o.waitMinutes} minutes; asked to stop job ${job.id}. A job that is stopped or fails costs nothing.`);
    }
    let r;
    try { r = await call('GET', job.poll_url, { timeoutMs: 30_000 }); } catch (e) { if (e instanceof ApiError && e.status < 500 && e.status !== 429) throw e; continue; }
    if (r.status === 200) { done = r.body; break; } // 202 while it runs
  }
  if (done.status !== 'succeeded') {
    throw new Error(`${label}: job ${job.id} ended as ${done.status}${done.error && done.error.message ? `: ${done.error.message}` : ''}. It costs nothing.`);
  }
  const took = Math.round((Date.now() - t0) / 1000);
  // The file is kept about 3 hours: download now.
  const saved = await save(done.results[0].url, file);
  // The exact charge arrives a little after the file, usually within 30 seconds.
  for (let i = 0; i < 12 && done.usage && done.usage.cost_pending; i++) {
    await sleep(5000);
    done = (await call('GET', job.poll_url, { timeoutMs: 30_000 })).body;
  }
  const cost = done.usage && done.usage.cost_usd != null ? done.usage.cost_usd : null;
  say(`  ${label}: ${saved.file} (${saved.width ? `${saved.width}x${saved.height}, ` : ''}${saved.seconds ? `${saved.seconds} s, ` : ''}${money(cost)})`);
  return { step: label, model: done.model || job.model, job_id: job.id, took_seconds: took, quoted_usd: job.estimated_cost_usd, max_cost_usd: job.max_cost_usd, cost_usd: cost, ...saved };
}

/** One spoken line as an mp3. */
async function voiceOver(o, text, file, label) {
  const req = { model: o.voiceModel, input: text, voice: o.voice, response_format: 'mp3' };
  if (o.dry) {
    const { body } = await call('POST', '/v1/audio/speech', { json: { ...req, validate: true } });
    return { step: label, model: o.voiceModel, dry_run: true, cost_usd: body.cost_usd, max_cost_usd: body.max_cost_usd };
  }
  const t0 = Date.now();
  const res = await call('POST', '/v1/audio/speech', { json: req, raw: true });
  writeFileSync(file, Buffer.from(await res.arrayBuffer()));
  const cost = Number(res.headers.get('x-uberllm-cost-usd'));
  const saved = { file, bytes: statSync(file).size, ...probe(file) };
  say(`  ${label}: ${file} (${saved.seconds ? `${saved.seconds} s, ` : ''}${money(Number.isFinite(cost) ? cost : null)})`);
  return { step: label, model: o.voiceModel, took_seconds: Math.round((Date.now() - t0) / 1000), cost_usd: Number.isFinite(cost) ? cost : null, ...saved };
}

/** The ffmpeg command that puts the voice on the clip (UberLLM does not join files). */
const mergeCommand = (clipFile, voiceFile, outFile) =>
  ['ffmpeg', '-y', '-i', clipFile, '-i', voiceFile, '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-af', 'apad', '-shortest', outFile];

// ---------------------------------------------------------------- recipes

async function ugc(o, photo) {
  const image = await source(photo, o.dry);
  const steps = [];
  const s = await still(o, image, o.scene || PROMPTS.ugcScene, o.aspect || '9:16', join(o.out, 'still.png'), 'still');
  steps.push(s);
  // The clip starts from the still, so it has the still's shape (9:16 here).
  steps.push(await clip(o, await firstFrame(o, s, image), o.motion || PROMPTS.ugcMotion, join(o.out, 'clip.mp4'), 'clip'));
  const line = o.line || 'Cold water all day, wherever the day takes you.';
  steps.push(await voiceOver(o, line, join(o.out, 'voice.mp3'), 'voice'));
  const cmd = mergeCommand(join(o.out, 'clip.mp4'), join(o.out, 'voice.mp3'), join(o.out, 'ad.mp4'));
  let merged;
  if (o.merge && !o.dry) {
    const r = spawnSync(cmd[0], cmd.slice(1), { encoding: 'utf8' });
    if (r.status === 0) merged = { file: join(o.out, 'ad.mp4'), bytes: statSync(join(o.out, 'ad.mp4')).size, ...probe(join(o.out, 'ad.mp4')) };
    else say(`  merge: ffmpeg did not run (${r.error ? r.error.message : (r.stderr || '').trim().split('\n').pop()}). Run it yourself:`);
  }
  return { steps, voice_line: line, merge_command: cmd.join(' '), ...(merged ? { merged } : {}) };
}

async function variations(o, photo) {
  const fromFile = o.list ? readFileSync(o.list, 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')) : [];
  const items = [...fromFile, ...list(o.variation)];
  const todo = items.length ? items : PROMPTS.variations;
  if (todo.length > 100) throw new Error('At most 100 variations per run.');
  const image = await source(photo, o.dry);
  const steps = [], failed = [];
  // Two at a time: quick, and gentle on the rate limit.
  let next = 0;
  const worker = async () => {
    for (;;) {
      const i = next++;
      if (i >= todo.length) return;
      const n = String(i + 1).padStart(2, '0');
      try {
        const s = await still(o, image, `Show this product ${todo[i]}. ${KEEP}`, o.aspect || '4:5', join(o.out, `variation-${n}.png`), `variation ${n}`);
        steps.push({ ...s, variation: todo[i] });
      } catch (e) {
        failed.push({ variation: todo[i], error: e.message });
        say(`  variation ${n}: ${e.message}`);
      }
    }
  };
  await Promise.all(Array.from({ length: o.dry ? 1 : 2 }, worker));
  steps.sort((a, b) => a.step.localeCompare(b.step));
  return { steps, failed };
}

async function sheet(o, where) {
  const text = /^https:\/\//i.test(where) ? await (await fetch(where, { signal: AbortSignal.timeout(30_000) })).text() : readFileSync(where, 'utf8');
  const rows = parseCsv(text).filter((r) => r.image_url);
  if (!rows.length) throw new Error('No rows found. The first line must name the columns: name,image_url,scene,motion (aspect_ratio is optional).');
  // Rows already made are remembered in <out>/done.json, so a daily run only makes the new ones.
  const doneFile = join(o.out, 'done.json');
  const done = existsSync(doneFile) ? JSON.parse(readFileSync(doneFile, 'utf8')) : {};
  const keyOf = (r) => [r.name, r.image_url, r.scene, r.motion, r.aspect_ratio].join('|');
  const todo = rows.filter((r) => !done[keyOf(r)]).slice(0, o.maxRows);
  say(`  ${rows.length} rows, ${rows.length - rows.filter((r) => !done[keyOf(r)]).length} made before, making ${todo.length} now (at most ${o.maxRows} per run)`);
  const steps = [], failed = [];
  for (const r of todo) {
    const slug = (r.name || 'row').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'row';
    try {
      const image = await source(r.image_url, o.dry);
      const s = await still(o, image, r.scene ? `${r.scene} ${KEEP}` : PROMPTS.spinScene, r.aspect_ratio || o.aspect || '9:16', join(o.out, `${slug}.png`), `${slug} still`);
      const c = await clip(o, await firstFrame(o, s, image), r.motion || PROMPTS.spinMotion, join(o.out, `${slug}.mp4`), `${slug} clip`);
      steps.push({ ...s, row: r.name }, { ...c, row: r.name });
      if (!o.dry) { done[keyOf(r)] = new Date().toISOString(); writeFileSync(doneFile, JSON.stringify(done, null, 2)); }
    } catch (e) {
      failed.push({ row: r.name, error: e.message }); // not marked done: the next run tries it again
      say(`  ${slug}: ${e.message}`);
    }
  }
  return { steps, failed, rows_total: rows.length, rows_made_now: todo.length - failed.length };
}

async function spin(o, photo) {
  const image = await source(photo, o.dry);
  const s = await still(o, image, o.scene || PROMPTS.spinScene, o.aspect || '1:1', join(o.out, 'studio.png'), 'studio still');
  const c = await clip(o, await firstFrame(o, s, image), o.motion || PROMPTS.spinMotion, join(o.out, 'spin.mp4'), 'spin clip');
  return { steps: [s, c] };
}

// ---------------------------------------------------------------- self-check and main

function selfCheck() {
  const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`self-check failed: ${what}\n  got  ${JSON.stringify(a)}\n  want ${JSON.stringify(b)}`); };
  eq(parseCsv('﻿Name,Image URL,scene\r\n"Bottle, green",https://x/y.jpg,"on a ""desk""\nby a window"\n\n,,\n'), [{ name: 'Bottle, green', image_url: 'https://x/y.jpg', scene: 'on a "desk"\nby a window' }], 'csv quotes, commas, line breaks, blank rows');
  eq(parseCsv('a,b\n1'), [{ a: '1', b: '' }], 'csv short row');
  eq(parseArgs(['ugc', 'p.jpg', '--variation', 'a', '--variation=b c', '--dry-run', '--seconds', '5']), { _: ['ugc', 'p.jpg'], variation: ['a', 'b c'], 'dry-run': true, seconds: '5' }, 'args');
  eq(mergeCommand('c.mp4', 'v.mp3', 'o.mp4').slice(-4), ['-af', 'apad', '-shortest', 'o.mp4'], 'merge keeps the clip length');
  console.log('ad-creative self-check ok');
}

const USAGE = `Usage: node ad-creative.mjs <ugc|variations|sheet|spin> <photo or rows.csv> [options]

  --out <folder>          where the files go (default: ad-creative-out)
  --dry-run               price the run; nothing is made, nothing is charged
  --scene "<text>"        ugc, spin: what the still shows
  --motion "<text>"       ugc, spin: what moves in the clip
  --line "<text>"         ugc: the spoken line
  --voice <name>          ugc: the voice (default ${DEFAULTS.voice})
  --merge                 ugc: join clip and voice with ffmpeg on this computer
  --list <file>           variations: one variation per line
  --variation "<text>"    variations: add one (repeat the flag)
  --aspect <w:h>          shape of the still: 9:16, 4:5, 1:1, 16:9 (a clip takes the shape of its still)
  --seconds <n>           clip length, 3 to 15 (default 5)
  --max-cost <usd>        the most one clip may cost (default 1.5); dearer is refused before any charge
  --max-rows <n>          sheet: rows made per run (default 5)
  --image-model, --video-model, --voice-model <id>

Needs UBERLLM_API_KEY. Get a key at https://uberllm.dev (credit from $5).`;

async function main() {
  const a = parseArgs(process.argv.slice(2));
  if (a['self-check']) return selfCheck();
  const [recipe, input] = a._;
  const run = { ugc, variations, sheet, spin }[recipe];
  if (!run || !input || a.help) { console.error(USAGE); process.exit(run && input ? 0 : 2); }
  if (!KEY) { console.error('Set UBERLLM_API_KEY first (get a key at https://uberllm.dev/dashboard/keys).'); process.exit(2); }
  const seconds = Number(a.seconds || 5);
  if (!Number.isInteger(seconds) || seconds < 3 || seconds > 15) { console.error('--seconds must be a whole number from 3 to 15.'); process.exit(2); }
  const o = {
    recipe, dry: !!a['dry-run'], out: String(a.out || 'ad-creative-out'), merge: !!a.merge,
    scene: a.scene, motion: a.motion, line: a.line, list: a.list, variation: a.variation, aspect: a.aspect,
    seconds, maxCost: Number(a['max-cost'] || 1.5), maxRows: Number(a['max-rows'] || 5), waitMinutes: Number(a['wait-minutes'] || 15),
    imageModel: a['image-model'] || DEFAULTS.imageModel, videoModel: a['video-model'] || DEFAULTS.videoModel,
    videoBackups: a['video-model'] ? [] : DEFAULTS.videoBackups,
    voiceModel: a['voice-model'] || DEFAULTS.voiceModel, voice: a.voice || DEFAULTS.voice,
  };
  mkdirSync(o.out, { recursive: true });
  say(`${recipe}${o.dry ? ' (price check only)' : ''}: ${input}`);
  const t0 = Date.now();
  const result = await run(o, input);
  const known = result.steps.filter((s) => s.cost_usd != null);
  const summary = {
    recipe, input, dry_run: o.dry, took_seconds: Math.round((Date.now() - t0) / 1000),
    total_cost_usd: Math.round(known.reduce((s, x) => s + Number(x.cost_usd), 0) * 1e6) / 1e6,
    ...(known.length < result.steps.length ? { cost_note: 'One or more charges were not reported yet; see Dashboard > Activity.' } : {}),
    ...result,
    reminders: [
      'Result links are short-lived (pictures about 15 minutes, video about 3 hours): the files in the output folder are your copies.',
      'People in these pictures are AI-generated. Label them as AI where the platform or the law asks, and never present them as real customers.',
    ],
  };
  if (!o.dry) writeFileSync(join(o.out, 'run.json'), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
  if (result.failed && result.failed.length && !result.steps.length) process.exit(1);
}

main().catch((e) => { console.error(`ad-creative: ${e.message}`); process.exit(1); });
