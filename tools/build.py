#!/usr/bin/env python3
"""Builds the n8n workflow files in ../workflows from one place.

Every workflow calls the UberLLM API with n8n's own HTTP Request node and one
Header Auth credential, so it needs no community node. Prices in the notes are
read from the public models list when this runs:

    python3 tools/build.py            # reads https://api.uberllm.dev/api/v1/public/models
    python3 tools/build.py --offline  # keeps the prices from the last build (tools/prices.json)
"""
import json
import sys
import urllib.request
import uuid
from datetime import date, timezone, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'workflows'
PRICES = ROOT / 'tools' / 'prices.json'
API = 'https://api.uberllm.dev'
FEE = 'No UberLLM fee on requests. Adding credit by card costs 5.5% + $0.50.'

# The one credential every HTTP node uses. Open the workflow, pick (or create)
# a Header Auth credential with Name "Authorization" and Value "Bearer <key>".
CRED = {'httpHeaderAuth': {'id': 'uberllmApiKey01', 'name': 'UberLLM API key'}}

MODELS_USED = [
    'fal-ai/veo3.1/fast', 'fal-ai/veo3.1/fast/image-to-video', 'fal-ai/veo3.1', 'fal-ai/veo3.1/image-to-video',
    'fal-ai/veo3.1/lite',
    'fal-ai/kling-video/v3/standard/image-to-video', 'fal-ai/kling-video/v3/pro/image-to-video',
    'fal-ai/minimax/hailuo-02/standard/image-to-video', 'venice/z-image-turbo',
    'google/nano-banana-2-edit', 'deepseek/deepseek-v3.2', 'google/gemini-2.5-flash',
    'kling/kling-o3-standard-image-to-video', 'fal-ai/kling-video/o3/standard/image-to-video', 'venice/kokoro-tts',
]


def load_prices(offline: bool) -> dict:
    if offline:
        return json.loads(PRICES.read_text())
    req = urllib.request.Request(f'{API}/api/v1/public/models', headers={'User-Agent': 'uberllm-n8n-templates'})
    with urllib.request.urlopen(req, timeout=30) as r:
        models = {m['id']: m for m in json.load(r)['models']}
    out = {'checked': datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'models': {}}
    for mid in MODELS_USED:
        m = models.get(mid)
        if not m:
            continue
        out['models'][mid] = {
            'name': m.get('display_name') or mid,
            'unit': m.get('media_unit'),
            'usd': m.get('media_price_now_usd'),
            'rates': m.get('media_rates'),
            'price_now': m.get('price_now'),
        }
    PRICES.write_text(json.dumps(out, indent=2) + '\n')
    return out


def per_second(prices: dict, mid: str, resolution=None, audio=False):
    m = prices['models'].get(mid)
    if not m:
        return None
    for r in m.get('rates') or []:
        if r['audio'] == audio and (resolution is None or (r.get('resolution') or '').lower() == resolution.lower()):
            return r['usd_per_second']
    return m['usd'] if m.get('unit') == 'second' and not audio else None


def usd(x: float) -> str:
    s = f'{x:.4f}'.rstrip('0')
    return '$' + (s + '0' if s.endswith('.') else s)


# ---------------------------------------------------------------- node helpers

class WF:
    def __init__(self, slug: str, name: str):
        self.slug, self.name = slug, name
        self.nodes, self.conn = [], {}

    def _id(self, name: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f'uberllm-n8n/{self.slug}/{name}'))

    def add(self, name, type_, version, pos, params, **extra):
        node = {'parameters': params, 'id': self._id(name), 'name': name, 'type': type_,
                'typeVersion': version, 'position': list(pos)}
        node.update(extra)
        self.nodes.append(node)
        return name

    def link(self, a, b, out=0, inp=0):
        main = self.conn.setdefault(a, {'main': []})['main']
        while len(main) <= out:
            main.append([])
        main[out].append({'node': b, 'type': 'main', 'index': inp})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    def note(self, name, pos, w, h, text, color=None):
        p = {'content': text, 'height': h, 'width': w}
        if color:
            p['color'] = color
        return self.add(name, 'n8n-nodes-base.stickyNote', 1, pos, p)

    def write(self, description: str):
        doc = {
            'name': self.name,
            'nodes': self.nodes,
            'connections': self.conn,
            'pinData': {},
            'settings': {'executionOrder': 'v1'},
            'meta': {'templateCredsSetupCompleted': False, 'description': description},
            'tags': [],
        }
        (OUT / f'{self.slug}.json').write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n')


def manual(wf, pos=(0, 0)):
    return wf.add('Run workflow', 'n8n-nodes-base.manualTrigger', 1, pos, {})


def settings(wf, name, pos, fields):
    """fields: list of (name, value, type)."""
    return wf.add(name, 'n8n-nodes-base.set', 3.4, pos, {
        'mode': 'manual',
        'assignments': {'assignments': [
            {'id': wf._id(f'{name}/{k}'), 'name': k, 'value': v, 'type': t} for k, v, t in fields]},
        'includeOtherFields': False,
        'options': {},
    })


def http(wf, name, pos, method, url, body_expr=None, headers=None, options=None, retry=True):
    p = {'method': method, 'url': url, 'authentication': 'genericCredentialType',
         'genericAuthType': 'httpHeaderAuth', 'sendHeaders': True,
         # Names the app on the request's line in Dashboard -> Activity.
         'headerParameters': {'parameters': [{'name': 'X-UberLLM-Source', 'value': 'n8n'}]
                              + [{'name': k, 'value': v} for k, v in (headers or [])]}}
    if body_expr is not None:
        p['sendBody'] = True
        p['specifyBody'] = 'json'
        p['jsonBody'] = body_expr
    p['options'] = options or {}
    extra = {'credentials': CRED}
    if retry:
        extra.update(retryOnFail=True, maxTries=3, waitBetweenTries=5000)
    return wf.add(name, 'n8n-nodes-base.httpRequest', 4.2, pos, p, **extra)


def if_node(wf, name, pos, left, op, right=None):
    cond = {'id': wf._id(f'{name}/c'), 'leftValue': left, 'operator': op}
    if right is not None:
        cond['rightValue'] = right
    return wf.add(name, 'n8n-nodes-base.if', 2.2, pos, {
        'conditions': {
            'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
            'conditions': [cond],
            'combinator': 'and',
        },
        'options': {},
    })


def code(wf, name, pos, js, mode=None):
    p = {'jsCode': js}
    if mode:
        p['mode'] = mode
    return wf.add(name, 'n8n-nodes-base.code', 2, pos, p)


def wait_job(wf, start, x, y, label='video', max_polls=60, seconds=15, max_minutes=None, on_fail=None):
    """Wait -> Get job -> ready? / still running? loop. Returns the ready node.

    max_minutes: stop waiting by the job's age instead of the number of checks (use inside a loop,
    where the check count runs across every item). on_fail: a node to go to when the job is not
    made, instead of stopping the whole workflow."""
    w = wf.add(f'Wait for {label}', 'n8n-nodes-base.wait', 1.1, (x, y),
               {'amount': seconds, 'unit': 'seconds'}, webhookId=wf._id(f'wait-{label}'))
    g = http(wf, f'Check {label} job', (x + 220, y), 'GET',
             f"={{{{ '{API}/v1/video/generations/' + $json.id }}}}")
    ready = if_node(wf, f'{label.capitalize()} ready?', (x + 440, y), '={{ $json.status }}',
                    {'type': 'string', 'operation': 'equals'}, 'succeeded')
    guard = (f"(Date.now() / 1000 - $json.created) < {max_minutes * 60}" if max_minutes
             else f"$runIndex < {max_polls}")
    running = if_node(wf, f'Still making {label}?', (x + 660, y + 160),
                      f"={{{{ ['queued', 'running'].includes($json.status) && {guard} }}}}",
                      {'type': 'boolean', 'operation': 'true', 'singleValue': True})
    wf.link(start, w)
    wf.chain(w, g, ready)
    wf.link(ready, running, out=1)
    wf.link(running, w, out=0)
    if on_fail:
        wf.link(running, on_fail, out=1)
        return ready
    stop = wf.add(f'{label.capitalize()} not made', 'n8n-nodes-base.stopAndError', 1, (x + 880, y + 260), {
        'errorMessage': ("={{ 'The " + label + " job ' + $json.id + ' ended as ' + $json.status + ': ' + "
                         "($json.error && $json.error.message ? $json.error.message : 'it took too long to wait for') + "
                         "'. A failed or expired job costs nothing.' }}"),
    })
    wf.link(running, stop, out=1)
    return ready


def result_and_download(wf, ready, x, y, label='video', extra_fields=(), out_name='Video'):
    """Result fields, then the file as binary `data` (the download keeps the fields)."""
    fields = [
        ('job_id', '={{ $json.id }}', 'string'),
        ('model', '={{ $json.model }}', 'string'),
        ('url', '={{ $json.results[0].url }}', 'string'),
        ('cost_usd', '={{ $json.usage && $json.usage.cost_usd != null ? $json.usage.cost_usd : null }}', 'number'),
        ('cost_pending', '={{ !!($json.usage && $json.usage.cost_pending) }}', 'boolean'),
    ] + list(extra_fields)
    res = settings(wf, f'{label.capitalize()} result', (x, y), fields)
    dl = http(wf, out_name, (x + 220, y), 'GET', '={{ $json.url }}',
              options={'response': {'response': {'responseFormat': 'file', 'outputPropertyName': 'data'}},
                       'timeout': 300000})
    wf.link(ready, res, out=0)
    wf.link(res, dl)
    return dl


def chat_body(model_expr, system, user_expr, json_mode=True, max_tokens=900, image_expr=None):
    user = user_expr if image_expr is None else (
        f"[{{ type: 'text', text: {user_expr} }}, {{ type: 'image_url', image_url: {{ url: {image_expr} }} }}]")
    body = (f"={{{{ JSON.stringify({{ model: {model_expr}, max_tokens: {max_tokens}, temperature: 0.8, "
            f"messages: [{{ role: 'system', content: {json.dumps(system)} }}, {{ role: 'user', content: {user} }}]"
            + (", response_format: { type: 'json_object' }" if json_mode else '') + ' }) }}')
    return body


PARSE_JSON = r"""
// The model was asked for JSON. Read it even if it wrapped it in a code fence.
const text = $json.choices?.[0]?.message?.content ?? '';
const match = text.match(/\{[\s\S]*\}/);
if (!match) throw new Error('The chat model did not answer with JSON: ' + text.slice(0, 200));
return { json: JSON.parse(match[0]) };
""".strip()


SETUP = (
    "### Setup (2 minutes)\n"
    "1. Get a key at https://uberllm.dev (sign in with your email, add credit).\n"
    "2. Open any **UberLLM** HTTP node → Credential → **Create new** → *Header Auth*:\n"
    "   - Name: `Authorization`\n"
    "   - Value: `Bearer YOUR_UBERLLM_KEY`\n"
    "   Every UberLLM node in this workflow uses this one credential.\n"
    "3. Edit the **Settings** node, then click **Execute workflow**.\n\n"
)

PROMISES = (
    "### What you pay\n"
    "- The start reply shows the price now (`estimated_cost_usd`). The bill is what the provider charged, "
    "which can differ if the job moves to another provider.\n"
    "- Your ceiling is `max_cost` in **Settings**: a job that would cost more is refused or cancelled at no charge.\n"
    "- A video that fails or runs out of time costs nothing: the whole hold comes back.\n"
    "- Credit you buy does not expire while your account is open.\n"
    f"- {FEE}\n"
)

FILES = (
    "### The file\n"
    "The last node has the video as binary `data` (send it to Drive, S3, YouTube…) and its link in `url`. "
    "The link needs your key and is kept about 3 hours. To have UberLLM put the file in your own bucket, "
    "add `output_upload_url` (a presigned PUT link) to the start request.\n"
)


def video_price_line(prices, mid, res, audio, seconds):
    r = per_second(prices, mid, res, audio)
    if r is None:
        return ''
    return (f"{usd(r)} per second" + (f" at {res}" if res else '') + (' with sound' if audio else ' without sound')
            + f" → {usd(r * seconds)} for {seconds} s (price on {prices['checked']}; live prices: https://uberllm.dev/fal-api-pricing)")


# ------------------------------------------------------------------ templates

def t_text_to_video(prices):
    wf = WF('veo-3-1-fast-text-to-video', 'UberLLM: text to video with Veo 3.1 Fast')
    m = 'fal-ai/veo3.1/fast'
    wf.note('About', (-560, -260), 520, 760,
            "## Text to video with Veo 3.1 Fast (UberLLM)\n"
            "Writes a video from a prompt with Google's Veo 3.1 Fast, waits for it, and hands you the file.\n\n"
            + SETUP +
            f"### Price\n{video_price_line(prices, m, '720p', True, 8)}\n"
            f"Without sound: {video_price_line(prices, m, '720p', False, 8)}\n\n" + PROMISES + "\n" + FILES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('prompt', 'A slow dolly shot through a neon-lit night market in the rain, steam rising from food stalls, cinematic', 'string'),
        ('model', m, 'string'),
        ('duration_seconds', 8, 'number'),
        ('resolution', '720p', 'string'),
        ('aspect_ratio', '16:9', 'string'),
        ('audio', True, 'boolean'),
        ('max_cost', 1.5, 'number'),
    ])
    start = http(wf, 'Start video', (440, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $json.model, prompt: $json.prompt, duration_seconds: $json.duration_seconds, "
                 "resolution: $json.resolution, aspect_ratio: $json.aspect_ratio, audio: $json.audio, max_cost: $json.max_cost, metadata: { template: 'veo-3-1-fast-text-to-video' } }) }}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-video')])
    wf.chain(t, s, start)
    ready = wait_job(wf, start, 660, 0)
    result_and_download(wf, ready, 1320, -200)
    wf.write('Make a video from a text prompt with Veo 3.1 Fast through the UberLLM API: start the job, wait for it, download the file.')


def t_image_to_video(prices):
    wf = WF('kling-image-to-video', 'UberLLM: image to video with Kling 3.0')
    m = 'fal-ai/kling-video/v3/standard/image-to-video'
    pro = 'fal-ai/kling-video/v3/pro/image-to-video'
    wf.note('About', (-560, -260), 520, 800,
            "## Image to video with Kling 3.0 (UberLLM)\n"
            "Turns a picture into a video clip: the picture is the first frame and the prompt says what moves.\n\n"
            + SETUP +
            "`image_url` must be a public `https://` link (or a `data:` URI). Your own file from an earlier "
            "UberLLM image works too.\n\n"
            f"### Price\nKling 3.0 Standard: {video_price_line(prices, m, None, False, 5)}\n"
            f"Kling 3.0 Pro (`{pro}`): {video_price_line(prices, pro, None, False, 5)}\n\n"
            + PROMISES + "\n" + FILES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('image_url', 'https://raw.githubusercontent.com/PSA-Team-source/uberllm-n8n-templates/main/samples/scene.jpg', 'string'),
        ('prompt', 'The camera slowly pushes in while rain runs down the window and the lamp light flickers', 'string'),
        ('model', m, 'string'),
        ('duration_seconds', 5, 'number'),
        ('audio', False, 'boolean'),
        ('max_cost', 1, 'number'),
    ])
    start = http(wf, 'Start video', (440, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $json.model, prompt: $json.prompt, image_url: $json.image_url, "
                 "duration_seconds: $json.duration_seconds, audio: $json.audio, max_cost: $json.max_cost, metadata: { template: 'kling-image-to-video' } }) }}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-video')])
    wf.chain(t, s, start)
    ready = wait_job(wf, start, 660, 0)
    result_and_download(wf, ready, 1320, -200)
    wf.write('Turn a picture into a video with Kling 3.0 through the UberLLM API: start the job, wait for it, download the file.')


SHORTS_SYSTEM = (
    "You write faceless short-form videos (YouTube Shorts, TikTok, Reels). Answer with JSON only: "
    '{"title": string, "caption": string, "hashtags": string, "scenes": [{"narration": string, '
    '"image_prompt": string, "motion_prompt": string}]}. Each scene is one vertical shot of about 6 seconds. '
    "image_prompt describes one still frame in detail (subject, setting, light, style, vertical 9:16, no text in the picture). "
    "motion_prompt says how the camera and subject move in that shot. narration is one short spoken line."
)

SPLIT_SCENES = r"""
// One item per scene, at most the number of scenes asked for in Settings.
const text = $json.choices?.[0]?.message?.content ?? '';
const match = text.match(/\{[\s\S]*\}/);
if (!match) throw new Error('The chat model did not answer with JSON: ' + text.slice(0, 200));
const script = JSON.parse(match[0]);
const want = Number($('Settings').first().json.scenes) || 3;
const scenes = (script.scenes || []).slice(0, want);
if (!scenes.length) throw new Error('The script has no scenes.');
return scenes.map((s, i) => ({ json: { scene: i + 1, ...s } }));
""".strip()

SHORTS_PACKAGE = r"""
// One item: the script, every clip's link and cost, and each clip as binary scene_1, scene_2, ...
const script = JSON.parse($('Write script').first().json.choices[0].message.content.match(/\{[\s\S]*\}/)[0]);
const clips = [];
const binary = {};
for (const item of $input.all()) {
  const n = Number(item.json.scene);
  clips.push({ scene: n, narration: item.json.narration, url: item.json.url, cost_usd: item.json.cost_usd, job_id: item.json.job_id });
  if (item.binary && item.binary.data) binary[`scene_${n}`] = item.binary.data;
}
clips.sort((a, b) => a.scene - b.scene);
const total = clips.reduce((s, c) => s + (Number(c.cost_usd) || 0), 0);
return [{ json: { title: script.title, caption: script.caption, hashtags: script.hashtags, clips, video_cost_usd: Math.round(total * 10000) / 10000 }, binary }];
""".strip()


def t_shorts(prices):
    wf = WF('faceless-shorts-pipeline', 'UberLLM: faceless Shorts pipeline (script → images → video clips)')
    chat, img, vid = 'deepseek/deepseek-v3.2', 'venice/z-image-turbo', 'fal-ai/minimax/hailuo-02/standard/image-to-video'
    ip = prices['models'].get(img, {}).get('usd')
    vr = per_second(prices, vid, '768p', False)
    per_scene = (ip or 0) + (vr or 0) * 6
    wf.note('About', (-600, -300), 560, 980,
            "## Faceless Shorts pipeline (UberLLM)\n"
            "One key does all three steps:\n"
            f"1. **Write script**: a cheap chat model (`{chat}`) writes the title, caption, hashtags and the scenes.\n"
            f"2. **Make scene image**: `{img}` draws each scene as a vertical picture.\n"
            f"3. **Start scene video**: `{vid}` animates each picture into a ~6 s clip.\n"
            "Scenes run one after another, so a run with 3 scenes takes a few minutes.\n\n"
            + SETUP +
            "### Price per scene\n"
            + (f"Image {usd(ip)} + 6 s clip at 768P {usd(vr * 6)} = about {usd(per_scene)} per scene; "
               f"the script costs a fraction of a cent (prices on {prices['checked']}).\n\n" if ip and vr else '')
            + "### Output\nOne item: `title`, `caption`, `hashtags`, `clips[]` (narration, link, cost) and each clip as "
            "binary `scene_1`, `scene_2`… Join them with your editor or an ffmpeg step, add the narration with a speech model "
            "(`POST /v1/audio/speech`, same key), and post.\n\n" + PROMISES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('topic', '3 surprising facts about octopuses', 'string'),
        ('scenes', 3, 'number'),
        ('chat_model', chat, 'string'),
        ('image_model', img, 'string'),
        ('video_model', vid, 'string'),
        ('duration_seconds', 6, 'number'),
        ('resolution', '768P', 'string'),
        ('max_cost_per_clip', 0.75, 'number'),
    ])
    w = http(wf, 'Write script', (440, 0), 'POST', f'{API}/v1/chat/completions',
             chat_body('$json.chat_model', SHORTS_SYSTEM,
                       "'Topic: ' + $json.topic + '. Write exactly ' + $json.scenes + ' scenes.'", max_tokens=1500),
             options={'timeout': 120000})
    sp = code(wf, 'Split scenes', (660, 0), SPLIT_SCENES)
    loop = wf.add('One scene at a time', 'n8n-nodes-base.splitInBatches', 3, (880, 0), {'options': {}})
    im = http(wf, 'Make scene image', (1100, 100), 'POST', f'{API}/v1/images/generations',
              "={{ JSON.stringify({ model: $('Settings').first().json.image_model, prompt: $json.image_prompt, n: 1, size: '1024x1536' }) }}",
              options={'timeout': 120000})
    sv = http(wf, 'Start scene video', (1760, 100), 'POST', f'{API}/v1/video/generations',
              "={{ JSON.stringify({ model: $('Settings').first().json.video_model, prompt: $('One scene at a time').item.json.motion_prompt, "
              "image_url: $json.id, duration_seconds: $('Settings').first().json.duration_seconds, "
              "resolution: $('Settings').first().json.resolution, max_cost: $('Settings').first().json.max_cost_per_clip, "
              "metadata: { template: 'faceless-shorts-pipeline', scene: String($('One scene at a time').item.json.scene) } }) }}",
              headers=[('Idempotency-Key', "={{ $execution.id }}-scene-{{ $('One scene at a time').item.json.scene }}")])
    # The picture's link needs your key, so a video model cannot open it: download it and upload it as a file.
    sf = download(wf, 'Scene image file', (1320, 100), '={{ $json.data[0].url }}')
    up = upload_binary(wf, 'Upload scene image', (1540, 100))
    wf.chain(t, s, w, sp, loop)
    wf.link(loop, im, out=1)
    wf.chain(im, sf, up, sv)
    ready = wait_job(wf, sv, 1980, 100, label='clip')
    mg = result_and_download(wf, ready, 2640, -100, label='clip', out_name='Clip', extra_fields=[
        ('scene', '={{ Number($json.metadata.scene) }}', 'number'),
        ('narration', "={{ $('Split scenes').all().find(s => String(s.json.scene) === $json.metadata.scene).json.narration }}", 'string'),
    ])
    wf.link(mg, loop)
    pk = code(wf, 'Shorts package', (1100, -200), SHORTS_PACKAGE)
    wf.link(loop, pk, out=0)
    wf.write('Faceless Shorts with one UberLLM key: a cheap chat model writes the script, an image model draws each scene, a video model animates each one.')


UGC_SYSTEM = (
    "You plan short UGC-style product ads (a real person filming on a phone). Look at the product photo and answer "
    'with JSON only: {"scene_prompt": string, "motion_prompt": string, "hook": string, "caption": string}. '
    "scene_prompt: an edit instruction that puts this exact product (keep its label, shape and colours) in the hand of a "
    "creator who fits the audience, filming a selfie-style vertical shot in a real home setting, natural light, phone-camera look. "
    "motion_prompt: how the person and camera move in a 5-second clip (e.g. lifts the product to the camera and smiles). "
    "hook: the first spoken line. caption: the post caption."
)


def t_ugc(prices):
    wf = WF('ugc-product-ad', 'UberLLM: UGC-style product ad from one product photo')
    vis, ed, vid = 'google/gemini-2.5-flash', 'google/nano-banana-2-edit', 'fal-ai/kling-video/v3/standard/image-to-video'
    ep = prices['models'].get(ed, {}).get('usd')
    vr = per_second(prices, vid, None, True)
    wf.note('About', (-600, -300), 560, 980,
            "## UGC-style product ad (UberLLM)\n"
            "From one product photo to a vertical ad clip of a person holding your product:\n"
            f"1. **Plan the ad**: a vision model (`{vis}`) looks at the photo and writes the scene, the motion, a hook and a caption.\n"
            f"2. **Make UGC scene**: `{ed}` puts your product in a creator's hand (the label stays as it is).\n"
            f"3. **Start ad video**: `{vid}` turns that picture into a 5 s clip with sound.\n\n"
            + SETUP +
            "`product_image_url` must be a public `https://` link to the product photo (a plain background works best). "
            "If you change `vision_model`, pick one that reads pictures from a link (`google/gemini-2.5-flash` does).\n\n"
            "### Price\n"
            + (f"Scene picture {usd(ep)} + 5 s clip with sound {usd(vr * 5)} = about {usd(ep + vr * 5)} per ad "
               f"(prices on {prices['checked']}). The planning call costs a fraction of a cent.\n\n" if ep and vr else '')
            + PROMISES + "\n" + FILES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('product_image_url', 'https://raw.githubusercontent.com/PSA-Team-source/uberllm-n8n-templates/main/samples/product.jpg', 'string'),
        ('product_name', 'Amber glass serum bottle', 'string'),
        ('audience', 'women 25-40 who care about skincare', 'string'),
        ('vision_model', vis, 'string'),
        ('image_model', ed, 'string'),
        ('video_model', vid, 'string'),
        ('duration_seconds', 5, 'number'),
        ('audio', True, 'boolean'),
        ('max_cost', 1.5, 'number'),
    ])
    plan = http(wf, 'Plan the ad', (440, 0), 'POST', f'{API}/v1/chat/completions',
                chat_body('$json.vision_model', UGC_SYSTEM,
                          "'Product: ' + $json.product_name + '. Audience: ' + $json.audience + '.'",
                          image_expr='$json.product_image_url', max_tokens=2000),
                options={'timeout': 120000})
    parse = code(wf, 'Read the plan', (660, 0), PARSE_JSON, mode='runOnceForEachItem')
    scene = http(wf, 'Make UGC scene', (880, 0), 'POST', f'{API}/v1/images/edits',
                 "={{ JSON.stringify({ model: $('Settings').item.json.image_model, prompt: $json.scene_prompt, "
                 "image_url: $('Settings').item.json.product_image_url, aspect_ratio: '9:16', n: 1 }) }}",
                 options={'timeout': 180000})
    sf = download(wf, 'Scene file', (1100, 0), '={{ $json.data[0].url }}')
    up = upload_binary(wf, 'Upload scene', (1320, 0))
    start = http(wf, 'Start ad video', (1540, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $('Settings').item.json.video_model, prompt: $('Read the plan').item.json.motion_prompt, "
                 "image_url: $json.id, duration_seconds: $('Settings').item.json.duration_seconds, "
                 "audio: $('Settings').item.json.audio, max_cost: $('Settings').item.json.max_cost, metadata: { template: 'ugc-product-ad' } }) }}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-ad')])
    wf.chain(t, s, plan, parse, scene, sf, up, start)
    ready = wait_job(wf, start, 1760, 0)
    result_and_download(wf, ready, 2420, -200, out_name='Ad video', extra_fields=[
        ('hook', "={{ $('Read the plan').first().json.hook }}", 'string'),
        ('caption', "={{ $('Read the plan').first().json.caption }}", 'string'),
        ('scene_image_url', "={{ $('Make UGC scene').first().json.data[0].url }}", 'string'),
    ])
    wf.write('UGC-style product ad from one product photo with one UberLLM key: a vision model plans the ad, an image model puts the product in a creator\'s hand, Kling animates it.')


VEO_PROMPT_SYSTEM = (
    "You write prompts for Google's Veo video model. Given a topic, write one detailed prompt for a short vertical video: "
    "subject, setting, camera movement, lighting, mood and sound. Answer with the prompt only, no preamble."
)


def t_gemini_replacement(prices):
    wf = WF('gemini-veo-replacement', 'Generate AI videos with Veo 3.1 after the Gemini API shutdown (UberLLM drop-in)')
    fast, full, lite = 'fal-ai/veo3.1/fast', 'fal-ai/veo3.1', 'fal-ai/veo3.1/lite'
    i2v, full_i2v = 'fal-ai/veo3.1/fast/image-to-video', 'fal-ai/veo3.1/image-to-video'
    chat = 'deepseek/deepseek-v3.2'
    wf.note('About', (-640, -320), 600, 1180,
            "## Veo 3.1 after the Gemini API shutdown\n"
            "Google turned off the Veo 3.1 preview models in the Gemini API on 22 October 2026 "
            "(https://ai.google.dev/gemini-api/docs/veo), so workflows built on the **Google Gemini → Generate a video** "
            "node stopped working. This is the same workflow on UberLLM: same steps, same output, one key.\n\n"
            "### Swap the model names\n"
            f"| Gemini model | UberLLM model |\n|---|---|\n"
            f"| `veo-3.1-generate-preview` | `{full}` |\n"
            f"| `veo-3.1-fast-generate-preview` | `{fast}` |\n"
            f"| `veo-3.1-lite-generate-preview` | `{lite}` |\n"
            f"| Veo 3.1, from a picture | `{full_i2v}` (add `image_url`) |\n"
            f"| Veo 3.1 Fast, from a picture | `{i2v}` (add `image_url`) |\n\n"
            "### Move your own workflow\n"
            "Delete the Gemini **Generate a video** node and paste the nodes from **Generate Video with Veo** to **Video** "
            "in its place. **Video** gives what the Gemini node gave: the file as binary `data`, and `url`. "
            "The UberLLM link needs your key, so a step that posts by link (Instagram, Blotato) needs the file "
            "uploaded first (Drive, S3), or add `output_upload_url` to the start request.\n\n"
            + SETUP +
            "### Price\n"
            f"Veo 3.1 Fast: {video_price_line(prices, fast, '720p', True, 8)}\n"
            + f"Veo 3.1: {video_price_line(prices, full, '720p', True, 8)}\n"
            + f"Veo 3.1 Lite: {video_price_line(prices, lite, '720p', True, 8)}\n"
            + "\n" + PROMISES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Workflow Configuration', (220, 0), [
        ('videoTopic', 'Create an engaging short video about daily motivation', 'string'),
        ('aspectRatio', '9:16', 'string'),
        ('durationSeconds', 8, 'number'),
        ('resolution', '720p', 'string'),
        ('audio', True, 'boolean'),
        ('promptModel', chat, 'string'),
        ('videoModel', fast, 'string'),
        ('maxCost', 1.5, 'number'),
    ])
    p = http(wf, 'Generate Video Prompt', (440, 0), 'POST', f'{API}/v1/chat/completions',
             chat_body('$json.promptModel', VEO_PROMPT_SYSTEM, "'Topic: ' + $json.videoTopic", json_mode=False, max_tokens=400),
             options={'timeout': 120000})
    start = http(wf, 'Generate Video with Veo', (660, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $('Workflow Configuration').item.json.videoModel, "
                 "prompt: $json.choices[0].message.content.trim(), "
                 "aspect_ratio: $('Workflow Configuration').item.json.aspectRatio, "
                 "duration_seconds: $('Workflow Configuration').item.json.durationSeconds, "
                 "resolution: $('Workflow Configuration').item.json.resolution, "
                 "audio: $('Workflow Configuration').item.json.audio, "
                 "max_cost: $('Workflow Configuration').item.json.maxCost, metadata: { template: 'gemini-veo-replacement' } }) }}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-veo')])
    wf.chain(t, s, p, start)
    ready = wait_job(wf, start, 880, 0)
    result_and_download(wf, ready, 1540, -200, out_name='Video')
    wf.write('Drop-in replacement for the Google Gemini "Generate a video" (Veo) node after Google shut down the Veo 3.1 preview models in the Gemini API on 2026-10-22.')


# ------------------------------------------------------------- ad recipes
# Four workflows for teams that make ad creatives from a product photo. All four were run end
# to end on 2026-11-19 (see README). They use the models that were finishing jobs reliably then.

AD_EDIT = 'google/nano-banana-2-edit'
AD_VIDEO = 'kling/kling-o3-standard-image-to-video'
AD_VIDEO_BACKUP = 'fal-ai/kling-video/o3/standard/image-to-video'
AD_VOICE = 'venice/kokoro-tts'
RAW = 'https://raw.githubusercontent.com/PSA-Team-source/uberllm-n8n-templates/main'
KEEP = 'Keep the product exactly as it is in the photo: same shape, colours, label and text.'
AD_PAGE = 'https://uberllm.dev/ai-ad-creative-api'

AD_RULES = (
    "### Before you run ads\n"
    "- People in these pictures and clips are AI-generated. Label them as AI where the platform or the law asks, "
    "and never present one as a real customer or a testimonial.\n"
    "- No health claims and no before/after pairs: Meta's ad rules apply whatever tool made the creative.\n"
    "- Check the model maker's terms for commercial use, and use only product photos you have the rights to.\n"
    f"Links to Meta's rules and each maker's terms: {AD_PAGE}\n"
)

AD_FILES = (
    "### Files are short-lived\n"
    "Picture links are kept about 15 minutes and video links about 3 hours, and they need your key. "
    "The workflow downloads every result at once, so the last node holds the files as binary data: "
    "send them to Drive, S3 or your ad tool from there.\n"
)

AD_FRAME = (
    "### Why the still is uploaded\n"
    "A result link needs your key, so a video model cannot open it. **Upload still** puts the picture in "
    "your UberLLM files (kept 24 hours) and the clip starts from that upload.\n"
)


def ad_price(prices, stills=0, clip_seconds=0, voice=False):
    """What one run costs at the listed prices, in words; '' when a price is missing."""
    ep = prices['models'].get(AD_EDIT, {}).get('usd')
    vr = per_second(prices, AD_VIDEO, None, False)
    if (stills and not ep) or (clip_seconds and not vr):
        return ''
    parts = []
    if stills:
        parts.append(f"{stills} still{'s' if stills > 1 else ''} at {usd(ep)} each")
    if clip_seconds:
        parts.append(f"a {clip_seconds} s clip at {usd(vr)} per second")
    total = (ep or 0) * stills + (vr or 0) * clip_seconds
    return (f"### Price\nListed on {prices['checked']}: " + ' + '.join(parts) + f" = about {usd(total)}"
            + (', plus the voice line (well under a cent)' if voice else '')
            + f". Live prices: {AD_PAGE}\n\n")


def upload_binary(wf, name, pos, field='data'):
    """POST /v1/files with the incoming binary; answers {id, url, ...}."""
    p = {'method': 'POST', 'url': f'{API}/v1/files', 'authentication': 'genericCredentialType',
         'genericAuthType': 'httpHeaderAuth', 'sendHeaders': True,
         'headerParameters': {'parameters': [{'name': 'X-UberLLM-Source', 'value': 'n8n'}]},
         'sendBody': True, 'contentType': 'multipart-form-data',
         'bodyParameters': {'parameters': [{'parameterType': 'formBinaryData', 'name': 'file', 'inputDataFieldName': field}]},
         'options': {'timeout': 120000}}
    return wf.add(name, 'n8n-nodes-base.httpRequest', 4.2, pos, p, credentials=CRED,
                  retryOnFail=True, maxTries=3, waitBetweenTries=5000)


def download(wf, name, pos, url_expr, field='data'):
    return http(wf, name, pos, 'GET', url_expr,
                options={'response': {'response': {'responseFormat': 'file', 'outputPropertyName': field}},
                         'timeout': 300000})


def carry_on(wf, name):
    """This node's failure does not stop the run: the item goes on with an `error` field."""
    for n in wf.nodes:
        if n['name'] == name:
            n['onError'] = 'continueRegularOutput'


AD_PACKAGE = r"""
// One item with everything: the still, the clip and the voice-over as binary, and the ffmpeg
// command that joins clip and voice (UberLLM does not join files).
const clip = $('Ad clip').first();
const s = $('Settings').first().json;
return [{
  json: {
    clip_url: clip.json.url,
    clip_cost_usd: clip.json.cost_usd,
    clip_cost_pending: clip.json.cost_pending,
    still_cost_usd: $('Make still').first().json.usage?.cost_usd ?? null,
    voice_line: s.voice_line,
    merge_command: 'ffmpeg -i clip.mp4 -i voice.mp3 -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -af apad -shortest ad.mp4',
  },
  binary: {
    still: $('Still file').first().binary.data,
    clip: clip.binary.data,
    voice: $input.first().binary.voice,
  },
}];
""".strip()


def t_ad_ugc(prices):
    slug = 'ad-ugc-still-clip-voice'
    wf = WF(slug, 'UberLLM: product photo → person holding it → 9:16 clip → voice-over')
    wf.note('About', (-640, -320), 600, 1240,
            "## Product photo → person holding it → 9:16 clip → voice-over (UberLLM)\n"
            "The most copied AI ad recipe, on one API key:\n"
            f"1. **Make still**: `{AD_EDIT}` puts your product in a person's hand, as a 9:16 picture.\n"
            f"2. **Start video**: `{AD_VIDEO}` animates the still into a 5 s clip (the still is the first frame, "
            "so the clip is 9:16 too).\n"
            f"3. **Voice-over**: `{AD_VOICE}` reads your line as an mp3.\n"
            "4. **Ad package** holds `still`, `clip` and `voice` as binary data, and the command that joins them.\n\n"
            + SETUP +
            "`product_image_url` must be a public `https://` link to the product photo. A plain background works best.\n\n"
            "### Join clip and voice\n"
            "UberLLM does not join files. On any computer with ffmpeg:\n"
            "`ffmpeg -i clip.mp4 -i voice.mp3 -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -af apad -shortest ad.mp4`\n"
            "(the clip keeps its length; the voice is padded with silence). Captions and avatars are not part of this workflow.\n\n"
            + ad_price(prices, stills=1, clip_seconds=5, voice=True) + PROMISES + "\n" + AD_FILES + "\n" + AD_FRAME)
    wf.note('Before you run ads', (1320, 420), 620, 330, AD_RULES, color=3)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('product_image_url', f'{RAW}/samples/bottle.jpg', 'string'),
        ('scene_prompt', "A generic adult holds this product in one hand at chest height in a bright, tidy kitchen, natural window light, "
                         f"phone-camera look, vertical framing with free space above for a headline. {KEEP}", 'string'),
        ('motion_prompt', 'The person lifts the product slightly toward the camera and turns it a little to show it. Handheld phone feel, no cuts.', 'string'),
        ('voice_line', 'Cold water all day, wherever the day takes you.', 'string'),
        ('voice', 'af_heart', 'string'),
        ('image_model', AD_EDIT, 'string'),
        ('video_model', AD_VIDEO, 'string'),
        ('backup_video_model', AD_VIDEO_BACKUP, 'string'),
        ('voice_model', AD_VOICE, 'string'),
        ('duration_seconds', 5, 'number'),
        ('max_cost', 1.5, 'number'),
    ])
    still = http(wf, 'Make still', (440, 0), 'POST', f'{API}/v1/images/edits',
                 "={{ JSON.stringify({ model: $json.image_model, prompt: $json.scene_prompt, image_url: $json.product_image_url, "
                 "aspect_ratio: '9:16', n: 1 }) }}", options={'timeout': 180000})
    sf = download(wf, 'Still file', (660, 0), '={{ $json.data[0].url }}')
    up = upload_binary(wf, 'Upload still', (880, 0))
    start = http(wf, 'Start video', (1100, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $('Settings').first().json.video_model, prompt: $('Settings').first().json.motion_prompt, "
                 "image_url: $json.id, duration_seconds: $('Settings').first().json.duration_seconds, audio: false, "
                 "models: [$('Settings').first().json.backup_video_model].filter(Boolean), "
                 f"max_cost: $('Settings').first().json.max_cost, metadata: {{ template: '{slug}' }} }}) }}}}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-ad')])
    wf.chain(t, s, still, sf, up, start)
    ready = wait_job(wf, start, 1320, 0)
    clip = result_and_download(wf, ready, 1980, -200, out_name='Ad clip')
    voice = http(wf, 'Voice-over', (2420, -200), 'POST', f'{API}/v1/audio/speech',
                 "={{ JSON.stringify({ model: $('Settings').first().json.voice_model, input: $('Settings').first().json.voice_line, "
                 "voice: $('Settings').first().json.voice, response_format: 'mp3' }) }}",
                 options={'response': {'response': {'responseFormat': 'file', 'outputPropertyName': 'voice'}}, 'timeout': 120000})
    pk = code(wf, 'Ad package', (2640, -200), AD_PACKAGE)
    wf.chain(clip, voice, pk)
    wf.write('From one product photo: a 9:16 still of a person holding the product, a 5 s clip that starts from it, and a voice-over line, '
             'with one UberLLM key. The note has the ffmpeg command that joins clip and voice.')


SPLIT_VARIATIONS = r"""
// One item per line of `variations` in Settings (blank lines and lines starting with # are skipped).
const s = $('Settings').first().json;
const lines = String(s.variations || '').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
if (!lines.length) throw new Error('Settings → variations is empty: write one variation per line.');
if (lines.length > 100) throw new Error('At most 100 variations per run.');
return lines.map((variation, i) => ({ json: {
  n: i + 1,
  variation,
  prompt: `Show this product ${variation}. Keep the product exactly as it is in the photo: same shape, colours, label and text.`,
} }));
""".strip()

VARIATIONS_DONE = r"""
// The pictures that were made (each item: its variation, cost and the file as binary `data`),
// then one last item that says how many were made and which were not. A picture that is not
// made costs nothing.
const made = $input.all().map((item) => ({ json: { ...item.json, file_name: `variation-${String(item.json.n).padStart(2, '0')}.png` }, binary: item.binary }));
const asked = $('One item per variation').all().map((i) => i.json);
const madeNs = new Set(made.map((m) => m.json.n));
const total = made.reduce((s, m) => s + (Number(m.json.cost_usd) || 0), 0);
return [...made, { json: {
  summary: true,
  made: made.length,
  not_made: asked.filter((a) => !madeNs.has(a.n)).map((a) => a.variation),
  total_cost_usd: Math.round(total * 10000) / 10000,
} }];
""".strip()


def t_ad_variations(prices):
    slug = 'ad-static-variations'
    wf = WF(slug, 'UberLLM: one product photo → many static ad variations')
    wf.note('About', (-640, -320), 600, 1000,
            "## One product photo → many static ad variations (UberLLM)\n"
            "Write a list (backgrounds, angles, settings, hooks) and get one picture per line, with the product kept as it is.\n"
            f"1. **One item per variation** splits `variations` in Settings, one per line.\n"
            f"2. **Make variation**: `{AD_EDIT}` makes each picture in the shape you set (4:5 for the Meta feed, 1:1, 9:16…).\n"
            "3. **Variation file** downloads each one at once. **Variations** lists what was made; **Not made (no charge)** lists the rest, with the reason.\n"
            "A variation that cannot be made does not stop the others, and costs nothing.\n\n"
            + SETUP +
            "`product_image_url` must be a public `https://` link to the product photo. Up to 100 lines per run.\n\n"
            + ad_price(prices, stills=1).replace('= about', 'per variation = about') +
            "### What you pay\n"
            "- A picture that is not made is not charged.\n"
            "- `max_cost_per_image` in Settings refuses a picture that could cost more, before anything is charged.\n"
            "- Credit you buy does not expire while your account is open.\n"
            f"- {FEE}\n\n" + AD_FILES)
    wf.note('Before you run ads', (1100, 320), 620, 330, AD_RULES, color=3)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('product_image_url', f'{RAW}/samples/bottle.jpg', 'string'),
        ('variations', 'on a sunlit wooden kitchen counter next to a bowl of lemons, morning light\n'
                       'on a gym bench beside a folded towel, cool daylight\n'
                       'on a desk next to a laptop and a notebook, soft office light\n'
                       'held up by a hand against a clear blue sky, outdoors', 'string'),
        ('aspect_ratio', '4:5', 'string'),
        ('image_model', AD_EDIT, 'string'),
        ('max_cost_per_image', 0.2, 'number'),
    ])
    sp = code(wf, 'One item per variation', (440, 0), SPLIT_VARIATIONS)
    mk = http(wf, 'Make variation', (660, 0), 'POST', f'{API}/v1/images/edits',
              "={{ JSON.stringify({ model: $('Settings').first().json.image_model, prompt: $json.prompt, "
              "image_url: $('Settings').first().json.product_image_url, aspect_ratio: $('Settings').first().json.aspect_ratio, "
              "n: 1, max_cost: $('Settings').first().json.max_cost_per_image }) }}", options={'timeout': 180000})
    carry_on(wf, mk)
    ok = if_node(wf, 'Made?', (880, 0), '={{ !!($json.data && $json.data[0] && $json.data[0].url) }}',
                 {'type': 'boolean', 'operation': 'true', 'singleValue': True})
    res = settings(wf, 'Variation result', (1100, -100), [
        ('n', "={{ $('One item per variation').item.json.n }}", 'number'),
        ('variation', "={{ $('One item per variation').item.json.variation }}", 'string'),
        ('url', '={{ $json.data[0].url }}', 'string'),
        ('cost_usd', '={{ $json.usage && $json.usage.cost_usd != null ? $json.usage.cost_usd : null }}', 'number'),
    ])
    dl = download(wf, 'Variation file', (1320, -100), '={{ $json.url }}')
    fin = code(wf, 'Variations', (1540, -100), VARIATIONS_DONE)
    no = settings(wf, 'Not made (no charge)', (1100, 120), [
        ('n', "={{ $('One item per variation').item.json.n }}", 'number'),
        ('variation', "={{ $('One item per variation').item.json.variation }}", 'string'),
        ('error', "={{ ($json.error && ($json.error.message || $json.error)) || $json.message || 'The picture was not made.' }}", 'string'),
    ])
    wf.chain(t, s, sp, mk, ok)
    wf.link(ok, res, out=0)
    wf.link(ok, no, out=1)
    wf.chain(res, dl, fin)
    wf.write('One product photo and a list become one static ad picture per line (backgrounds, angles, hooks), in the shape you set, with one UberLLM key.')


ROWS_TO_MAKE = r"""
// Reads the CSV text, skips rows this workflow already made (remembered in the workflow's static
// data, which n8n saves for active, scheduled runs; a manual test run starts fresh each time)
// and passes on at most max_rows_per_run rows.
const s = $('Settings').first().json;
const text = String($json.data ?? '').replace(/^﻿/, '');
const rows = [];
let row = [], cell = '', quoted = false;
for (let i = 0; i < text.length; i++) {
  const c = text[i];
  if (quoted) {
    if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') quoted = false; else cell += c;
  } else if (c === '"') quoted = true;
  else if (c === ',') { row.push(cell); cell = ''; }
  else if (c === '\n' || c === '\r') {
    if (c === '\r' && text[i + 1] === '\n') i++;
    row.push(cell); cell = '';
    if (row.some((x) => x.trim())) rows.push(row);
    row = [];
  } else cell += c;
}
row.push(cell);
if (row.some((x) => x.trim())) rows.push(row);
if (rows.length < 2) throw new Error('No rows found. The first line must name the columns: name,image_url,scene,motion,aspect_ratio');
const head = rows[0].map((h) => h.trim().toLowerCase().replace(/\s+/g, '_'));
if (!head.includes('image_url')) throw new Error('The sheet needs an image_url column (a public https:// link to the product photo).');
const done = ($getWorkflowStaticData('global').done ??= {});
const out = [];
for (const r of rows.slice(1)) {
  const o = Object.fromEntries(head.map((h, i) => [h, (r[i] ?? '').trim()]));
  if (!o.image_url) continue;
  const key = [o.name, o.image_url, o.scene, o.motion, o.aspect_ratio].join('|');
  if (done[key]) continue;
  out.push({ json: {
    key,
    name: o.name || `row ${out.length + 1}`,
    image_url: o.image_url,
    scene_prompt: (o.scene || 'The product stands alone on a seamless light-grey studio background with a soft shadow.') + ' Keep the product exactly as it is in the photo: same shape, colours, label and text.',
    motion_prompt: o.motion || 'The camera pushes in slowly toward the product. Nothing else moves.',
    aspect_ratio: o.aspect_ratio || '9:16',
  } });
  if (out.length >= (Number(s.max_rows_per_run) || 5)) break;
}
return out;
""".strip()

ROW_FAILED = r"""
// This row was not made (nothing was charged for the step that failed). It is not remembered
// as done, so the next run tries it again; the other rows carry on.
const row = $('One row at a time').first().json;
const j = $json;
const why = j.error?.message || (typeof j.error === 'string' ? j.error : null) || j.message
  || (j.status ? `the video job ended as ${j.status}` : 'the step did not answer');
return [{ json: { name: row.name, made: false, error: String(why) } }];
""".strip()

ROW_DONE = r"""
// Remember the row so the next scheduled run skips it, and pass on its still and clip.
const row = $('One row at a time').first().json;
$getWorkflowStaticData('global').done ??= {};
$getWorkflowStaticData('global').done[row.key] = new Date().toISOString();
const clip = $input.first();
return [{
  json: { name: row.name, made: true, aspect_ratio: row.aspect_ratio, clip_url: clip.json.url, clip_cost_usd: clip.json.cost_usd,
          clip_cost_pending: clip.json.cost_pending, still_cost_usd: $('Make row still').first().json.usage?.cost_usd ?? null },
  binary: { still: $('Row still file').first().binary.data, clip: clip.binary.data },
}];
""".strip()

ROWS_SUMMARY = r"""
// Every row of this run (made ones carry `still` and `clip` as binary), then one summary item.
const items = $input.all();
const made = items.filter((i) => i.json.made);
return [...items, { json: {
  summary: true,
  made: made.length,
  not_made: items.filter((i) => i.json.made === false).map((i) => ({ name: i.json.name, error: i.json.error })),
} }];
""".strip()


def t_ad_sheet(prices):
    slug = 'ad-sheet-rows-to-ads'
    wf = WF(slug, 'UberLLM: Google Sheet or CSV rows → one image + video ad per row, on a schedule')
    wf.note('About', (-680, -360), 640, 1320,
            "## Sheet rows → one image + video ad per row, on a schedule (UberLLM)\n"
            "Each row of a sheet becomes a still and a clip. Add rows whenever you like; the daily run makes only the new ones.\n"
            "1. **Read rows** fetches the sheet as CSV. **Rows to make** skips rows made before and takes at most `max_rows_per_run`.\n"
            f"2. **Make row still**: `{AD_EDIT}` puts the product in the row's scene, in the row's shape.\n"
            f"3. **Start row video**: `{AD_VIDEO}` animates the still (the clip takes the still's shape).\n"
            "4. **Rows made** lists every row with its `still` and `clip` as binary data.\n"
            "A row that fails does not stop the others, costs nothing for the failed step, and is tried again on the next run.\n\n"
            "### Your sheet\n"
            "Columns (first line): `name, image_url, scene, motion, aspect_ratio`. Only `image_url` is required "
            "(a public `https://` link to the product photo).\n"
            "- **Google Sheets**: File → Share → Publish to web → pick the tab → *Comma-separated values (.csv)* → Publish, "
            "and paste that link into `csv_url`. No Google credential is needed. Anyone with the link can read a published sheet.\n"
            "- Or any `https://` link to a CSV file.\n\n"
            + SETUP +
            "To run it every day, switch the workflow to **Active**. The schedule is in **Every day**.\n\n"
            + ad_price(prices, stills=1, clip_seconds=5).replace('= about', 'per row = about')
            + PROMISES + "\n" + AD_FILES + "\n" + AD_FRAME)
    wf.note('Before you run ads', (1100, 620), 620, 330, AD_RULES, color=3)
    t = manual(wf, (0, 0))
    sch = wf.add('Every day', 'n8n-nodes-base.scheduleTrigger', 1.2, (0, 200),
                 {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 9, 'triggerAtMinute': 0}]}})
    s = settings(wf, 'Settings', (220, 0), [
        ('csv_url', f'{RAW}/samples/ad-rows.csv', 'string'),
        ('max_rows_per_run', 5, 'number'),
        ('image_model', AD_EDIT, 'string'),
        ('video_model', AD_VIDEO, 'string'),
        ('backup_video_model', AD_VIDEO_BACKUP, 'string'),
        ('duration_seconds', 5, 'number'),
        ('max_cost_per_clip', 1.5, 'number'),
    ])
    wf.link(sch, s)
    rd = wf.add('Read rows', 'n8n-nodes-base.httpRequest', 4.2, (440, 0), {
        'method': 'GET', 'url': '={{ $json.csv_url }}',
        'options': {'response': {'response': {'responseFormat': 'text', 'outputPropertyName': 'data'}}, 'timeout': 30000},
    }, retryOnFail=True, maxTries=3, waitBetweenTries=5000)
    rows = code(wf, 'Rows to make', (660, 0), ROWS_TO_MAKE)
    loop = wf.add('One row at a time', 'n8n-nodes-base.splitInBatches', 3, (880, 0), {'options': {}})
    still = http(wf, 'Make row still', (1100, 100), 'POST', f'{API}/v1/images/edits',
                 "={{ JSON.stringify({ model: $('Settings').first().json.image_model, prompt: $json.scene_prompt, "
                 "image_url: $json.image_url, aspect_ratio: $json.aspect_ratio, n: 1 }) }}", options={'timeout': 180000})
    carry_on(wf, still)
    made = if_node(wf, 'Still made?', (1320, 100), '={{ !!($json.data && $json.data[0] && $json.data[0].url) }}',
                   {'type': 'boolean', 'operation': 'true', 'singleValue': True})
    sf = download(wf, 'Row still file', (1540, 0), '={{ $json.data[0].url }}')
    up = upload_binary(wf, 'Upload row still', (1760, 0))
    start = http(wf, 'Start row video', (1980, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $('Settings').first().json.video_model, prompt: $('One row at a time').first().json.motion_prompt, "
                 "image_url: $json.id, duration_seconds: $('Settings').first().json.duration_seconds, audio: false, "
                 "models: [$('Settings').first().json.backup_video_model].filter(Boolean), "
                 f"max_cost: $('Settings').first().json.max_cost_per_clip, metadata: {{ template: '{slug}' }} }}) }}}}",
                 headers=[('Idempotency-Key', "={{ $execution.id }}-row-{{ $('One row at a time').context.currentRunIndex }}")])
    carry_on(wf, start)
    started = if_node(wf, 'Job started?', (2200, 0), '={{ !!$json.id }}',
                      {'type': 'boolean', 'operation': 'true', 'singleValue': True})
    failed = code(wf, 'Row not made', (2420, 420), ROW_FAILED)
    wf.chain(t, s, rd, rows, loop)
    wf.link(loop, still, out=1)
    wf.link(still, made)
    wf.link(made, sf, out=0)
    wf.link(made, failed, out=1)
    wf.chain(sf, up, start, started)
    wf.link(started, failed, out=1)
    ready = wait_job(wf, started, 2420, 0, label='clip', max_minutes=20, on_fail=failed)
    clip = result_and_download(wf, ready, 3080, -200, label='clip', out_name='Row clip')
    done = code(wf, 'Row made', (3520, -200), ROW_DONE)
    wf.link(clip, done)
    wf.link(done, loop)
    wf.link(failed, loop)
    summ = code(wf, 'Rows made', (1100, -200), ROWS_SUMMARY)
    wf.link(loop, summ, out=0)
    wf.write('Each row of a Google Sheet (published as CSV) or any CSV link becomes a product still and a clip, on a daily schedule, '
             'with one UberLLM key. Rows made before are skipped; a row that fails does not stop the others.')


def t_ad_spin(prices):
    slug = 'ad-product-spin-video'
    wf = WF(slug, 'UberLLM: product photo → studio spin video')
    wf.note('About', (-640, -320), 600, 1040,
            "## Product photo → studio spin video (UberLLM)\n"
            "A clean turntable-style clip for a store page or an ad, from one photo:\n"
            f"1. **Make studio still**: `{AD_EDIT}` puts the product alone on a clean studio background (1:1).\n"
            f"2. **Start video**: `{AD_VIDEO}` turns it slowly, camera still, 5 s.\n"
            "3. **Spin video** holds the clip as binary `data`.\n\n"
            "The clip takes the shape of the still: set `aspect_ratio` to `1:1`, `4:5` or `9:16`. "
            "The model draws the sides it cannot see in your photo, so check the result against the real product. "
            "A turn shows best on a product with a label or an uneven shape.\n\n"
            + SETUP +
            "`product_image_url` must be a public `https://` link to the product photo.\n\n"
            + ad_price(prices, stills=1, clip_seconds=5) + PROMISES + "\n" + AD_FILES + "\n" + AD_FRAME)
    wf.note('Before you run ads', (1320, 420), 620, 330, AD_RULES, color=3)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('product_image_url', f'{RAW}/samples/bottle.jpg', 'string'),
        ('studio_prompt', f'The product stands alone, centred, on a seamless light-grey studio background with a soft shadow under it and even studio light. {KEEP}', 'string'),
        ('motion_prompt', 'The product turns slowly and smoothly on a turntable, one continuous rotation. The camera stays still. Studio light, nothing else moves.', 'string'),
        ('aspect_ratio', '1:1', 'string'),
        ('image_model', AD_EDIT, 'string'),
        ('video_model', AD_VIDEO, 'string'),
        ('backup_video_model', AD_VIDEO_BACKUP, 'string'),
        ('duration_seconds', 5, 'number'),
        ('max_cost', 1.5, 'number'),
    ])
    still = http(wf, 'Make studio still', (440, 0), 'POST', f'{API}/v1/images/edits',
                 "={{ JSON.stringify({ model: $json.image_model, prompt: $json.studio_prompt, image_url: $json.product_image_url, "
                 "aspect_ratio: $json.aspect_ratio, n: 1 }) }}", options={'timeout': 180000})
    sf = download(wf, 'Still file', (660, 0), '={{ $json.data[0].url }}')
    up = upload_binary(wf, 'Upload still', (880, 0))
    start = http(wf, 'Start video', (1100, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $('Settings').first().json.video_model, prompt: $('Settings').first().json.motion_prompt, "
                 "image_url: $json.id, duration_seconds: $('Settings').first().json.duration_seconds, audio: false, "
                 "models: [$('Settings').first().json.backup_video_model].filter(Boolean), "
                 f"max_cost: $('Settings').first().json.max_cost, metadata: {{ template: '{slug}' }} }}) }}}}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-spin')])
    wf.chain(t, s, still, sf, up, start)
    ready = wait_job(wf, start, 1320, 0)
    result_and_download(wf, ready, 1980, -200, out_name='Spin video')
    wf.write('A slow studio turntable clip of a product from one photo, with one UberLLM key: a clean studio still, then a 5 s clip that starts from it.')



def main():
    offline = '--offline' in sys.argv
    prices = load_prices(offline)
    OUT.mkdir(exist_ok=True)
    for f in (t_text_to_video, t_image_to_video, t_shorts, t_ugc, t_gemini_replacement,
              t_ad_ugc, t_ad_variations, t_ad_sheet, t_ad_spin):
        f(prices)
    print('built', len(list(OUT.glob('*.json'))), 'workflows; prices checked', prices['checked'])


if __name__ == '__main__':
    main()
