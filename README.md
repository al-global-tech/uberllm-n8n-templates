# UberLLM n8n templates: AI video and ad creatives

Nine [n8n](https://n8n.io) workflows that make AI videos and ad creatives with one
[UberLLM](https://uberllm.dev) API key: Veo 3.1, Kling 3.0, Hailuo, Nano Banana and
chat models through the same account and balance.

They use n8n's built-in **HTTP Request** node and one **Header Auth** credential, so they
run on n8n Cloud and self-hosted n8n with nothing to install. Each one starts the video,
waits for it, and gives you the file as binary `data`.

Every workflow here was imported into n8n 2.45.6 and run end to end against the live API:
the five video templates on 2026-10-30, and the four ad workflows, the UGC product ad and
the Shorts pipeline on 2026-11-19 (some with short, low-resolution settings to keep the
test cheap).

## Ad creatives from one product photo

Four recipes for teams that make ad creatives (Meta, Instagram, TikTok). Each is an n8n
workflow and one command of a script with no dependencies,
[`skills/ad-creative/scripts/ad-creative.mjs`](skills/ad-creative/scripts/ad-creative.mjs).

| Recipe | n8n workflow | Script | Test run on 2026-11-19 (script, default settings) |
|---|---|---|---|
| A person holding the product (9:16 still), a 5 s clip from it, a spoken line | [`ad-ugc-still-clip-voice.json`](workflows/ad-ugc-still-clip-voice.json) | `ugc product.jpg` | 2 min 18 s, $0.41: still 768x1376 ($0.042), clip 716x1284 5 s ($0.3675), voice 3.3 s ($0.0001) |
| Static variations, one picture per line of a list | [`ad-static-variations.json`](workflows/ad-static-variations.json) | `variations product.jpg` | 4 pictures at 4:5 (928x1152): 3 min 13 s, $0.168 |
| One still and one clip per row of a CSV or Google Sheet, on a schedule | [`ad-sheet-rows-to-ads.json`](workflows/ad-sheet-rows-to-ads.json) | `sheet rows.csv` | 2 rows: 13 min 13 s, $0.99 (each clip priced at $0.3675, billed $0.4531 after moving provider) |
| Studio still of the product alone and a turntable-style clip | [`ad-product-spin-video.json`](workflows/ad-product-spin-video.json) | `spin product.jpg` | 3 min 11 s, $0.41: still 1024x1024, clip 960x960 5 s |

Models: `google/nano-banana-2-edit` (still), `kling/kling-o3-standard-image-to-video`
(clip, with `fal-ai/kling-video/o3/standard/image-to-video` as backup) and
`venice/kokoro-tts` (voice). Change them in **Settings** or with `--image-model`,
`--video-model`, `--voice-model`. The sample product is [`samples/bottle.jpg`](samples/bottle.jpg)
and the sample sheet is [`samples/ad-rows.csv`](samples/ad-rows.csv).

```sh
export UBERLLM_API_KEY=ull_...        # from https://uberllm.dev/dashboard/keys
curl -O https://raw.githubusercontent.com/PSA-Team-source/uberllm-n8n-templates/main/skills/ad-creative/scripts/ad-creative.mjs
node ad-creative.mjs ugc product.jpg --dry-run   # prices the run; makes nothing, charges nothing
node ad-creative.mjs ugc product.jpg --merge     # --merge joins clip and voice with ffmpeg
```

Good to know:

- **A clip has the shape of its still.** These image-to-video models take no aspect ratio,
  so the still is made at 9:16, 4:5 or 1:1 first.
- **Result links are short-lived** (pictures about 15 minutes, video about 3 hours) and
  need your key. The workflows and the script download every file at once, and upload the
  still as a file (`POST /v1/files`) before they start the clip.
- **Joining is yours.** The API does not join clips, put captions on a video or lip-sync.
  A timed caption file (`srt`, `vtt`) comes from `POST /v1/audio/transcriptions` with the
  voice track. Clip plus voice is one command:
  `ffmpeg -i clip.mp4 -i voice.mp3 -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -af apad -shortest ad.mp4`
- **A picture request can time out** (504, not charged). The workflows and the script try
  up to three times.
- **The sheet workflow** remembers finished rows when it runs from its schedule (active
  workflow). A manual test run does not save that memory, so it makes the same rows again.
  A Google Sheet works once it is published as CSV (File → Share → Publish to web → CSV).
- **Before you run ads:** people in these pictures are AI-generated. Label them where the
  platform or the law asks, and never present them as real customers or as a testimonial.
  Make no health claims and no before/after pictures, and read the terms of the models you
  use. More: [uberllm.dev/ai-ad-creative-api](https://uberllm.dev/ai-ad-creative-api).

### As a skill for Claude Code and other coding agents

```sh
npx skills add PSA-Team-source/uberllm-n8n-templates --skill ad-creative
```

or copy [`skills/ad-creative`](skills/ad-creative) into `~/.claude/skills/` (Claude Code) or
your agent's skills folder. Then ask for "ad creatives from this product photo". The skill
prices a run first and asks before it spends.

## The video templates

| File | What it does | Default run |
|---|---|---|
| [`veo-3-1-fast-text-to-video.json`](workflows/veo-3-1-fast-text-to-video.json) | A prompt becomes a Veo 3.1 Fast clip | 8 s, 720p, with sound |
| [`kling-image-to-video.json`](workflows/kling-image-to-video.json) | A picture becomes a Kling 3.0 clip (the picture is the first frame) | 5 s, no sound |
| [`faceless-shorts-pipeline.json`](workflows/faceless-shorts-pipeline.json) | A chat model writes a script, an image model draws each scene, Hailuo animates each one | 3 scenes of 6 s |
| [`ugc-product-ad.json`](workflows/ugc-product-ad.json) | From one product photo: Gemini plans the ad, Nano Banana puts the product in a creator's hand, Kling animates it | 5 s with sound |
| [`gemini-veo-replacement.json`](workflows/gemini-veo-replacement.json) | The popular "Gemini → Generate a video" workflow, moved to UberLLM after Google shut down the Veo 3.1 preview models in the Gemini API | 8 s, 720p, with sound |

What a run costs today, at each template's default settings, is on
[uberllm.dev/docs/n8n-video](https://uberllm.dev/docs/n8n-video) (read from the live price
list). Per-second prices for every video model: [uberllm.dev/fal-api-pricing](https://uberllm.dev/fal-api-pricing).

## Use one

1. In n8n: **⋯** (top right) → **Import from URL…** and paste the raw link, e.g.
   `https://raw.githubusercontent.com/PSA-Team-source/uberllm-n8n-templates/main/workflows/kling-image-to-video.json`
   (or download the file and use **Import from File…**).
2. Get a key at [uberllm.dev](https://uberllm.dev) (sign in with your email, add credit).
3. Open any UberLLM node → **Credential** → **Create new** → **Header Auth**:
   - **Name**: `Authorization`
   - **Value**: `Bearer ull_...`
4. Edit the **Settings** node and click **Execute workflow**.

## Coming from the Gemini Veo node

Google turned off `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview` and
`veo-3.1-lite-generate-preview` in the Gemini API on 2026-10-22
([notice](https://ai.google.dev/gemini-api/docs/veo)). `gemini-veo-replacement.json`
follows the same steps as the popular Gemini-based templates on n8n.io (a
configuration node, a prompt-writing call, the video call; see
[6557](https://n8n.io/workflows/6557), [11617](https://n8n.io/workflows/11617),
[13149](https://n8n.io/workflows/13149)). To move your own workflow, delete the Gemini
**Generate a video** node, paste the nodes from **Generate Video with Veo** to **Video**,
and swap the model name:

| Gemini model | UberLLM model |
|---|---|
| `veo-3.1-generate-preview` | `fal-ai/veo3.1` |
| `veo-3.1-fast-generate-preview` | `fal-ai/veo3.1/fast` |
| `veo-3.1-lite-generate-preview` | `fal-ai/veo3.1/lite` |
| Veo 3.1 from a picture | `fal-ai/veo3.1/image-to-video` (add `image_url`) |
| Veo 3.1 Fast from a picture | `fal-ai/veo3.1/fast/image-to-video` (add `image_url`) |

The **Video** node gives what the Gemini node gave: the file as binary `data` and a `url`.
That link needs your key and is kept about 3 hours, so a step that posts by link
(Instagram, Blotato) needs the file uploaded first, or add `output_upload_url` (a
presigned PUT link) to the start request and UberLLM uploads the file there.

## How they work

- **Start**: `POST https://api.uberllm.dev/v1/video/generations` answers at once with the
  job `id` and `max_cost_usd`. It sends an `Idempotency-Key` made from the n8n execution id,
  so a retry after a dropped connection gets the same job back instead of a second one.
- **Wait**: every 15 s the workflow asks `GET /v1/video/generations/{id}` until the job has
  `succeeded`. A job that ends `failed` or `expired` stops the workflow with the reason.
  After 60 checks it stops waiting (the job still finishes and shows in Dashboard → Activity).
- **Download**: the result link is fetched with the same credential into binary `data`.
- Every request sends `X-UberLLM-Source: n8n` and video jobs carry `metadata.template`, so
  your Activity list shows which workflow made each job.

API reference: [uberllm.dev/docs/media](https://uberllm.dev/docs/media).

## What you pay

- The start reply shows the price now (`estimated_cost_usd`). The bill is what the provider
  charged, which can differ if the job moves to another provider (on 2026-11-19 two test
  clips priced at $0.3675 were billed $0.4531).
- Your ceiling is `max_cost` in each **Settings** node: a job that would cost more is refused
  or cancelled at no charge.
- A video that fails or runs out of time costs nothing: the whole hold comes back.
- Credit you buy does not expire while your account is open.
- No UberLLM fee on requests. Adding credit by card costs 5.5% + $0.50.

## Build the files

The workflow files are generated so all nine stay consistent:

```sh
python3 tools/build.py            # reads live prices for the notes from the public models list
python3 tools/build.py --offline  # reuses tools/prices.json
python3 tools/check.py            # structure check: unique names, links, credentials, no keys in files
```

## Other UberLLM integrations

- n8n community node: [n8n-nodes-uberllm](https://github.com/PSA-Team-source/n8n-nodes-uberllm)
- ComfyUI nodes: [comfyui-uberllm](https://github.com/PSA-Team-source/comfyui-uberllm)

MIT license. UberLLM is not affiliated with Uber.
