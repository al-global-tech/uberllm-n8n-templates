---
name: ad-creative
description: Make ad creatives from one product photo with the UberLLM API - a still of a person holding the product, a 9:16 clip made from it and a voice-over line; a batch of static variations from a list; one image and one clip per row of a CSV or Google Sheet; or a studio spin video. Use when the user asks for ad creatives, UGC-style product ads, static ad variations, Meta, Instagram or TikTok ad images or videos, or a product spin video, and has (or can give) a product photo.
license: MIT
---

# Ad creatives from one product photo

`scripts/ad-creative.mjs` does the work. It needs Node 18 or newer and nothing else, and
calls `https://api.uberllm.dev` with the key in `UBERLLM_API_KEY`.

## Before you run it

1. **Key.** The script reads `UBERLLM_API_KEY` from the environment. If it is not set, ask
   the user to get a key at https://uberllm.dev/dashboard/keys (credit from $5) and to set
   it themselves. Never print the key, never write it into a file, never put it in a command.
2. **Photo.** A path on this computer or a public `https://` link. A plain background works
   best. Use only photos the user has the rights to.
3. **Price first, then ask.** Run the same command with `--dry-run`. It makes nothing and
   charges nothing, and prints what each step costs now. Tell the user the total and wait
   for a yes before the real run. Every real run spends the user's credit.

## The four recipes

```sh
# 1. A person holding the product (9:16 still), a clip from that still, a spoken line
node scripts/ad-creative.mjs ugc product.jpg \
  --scene "A hand holds the product in a bright kitchen, phone-camera look" \
  --motion "The hand lifts the product toward the camera and turns it a little" \
  --line "Cold water all day, wherever the day takes you." --merge

# 2. Static variations: one picture per line of a list
node scripts/ad-creative.mjs variations product.jpg --aspect 4:5 \
  --variation "on a kitchen counter in morning light" \
  --variation "on a gym bench next to a towel"
#    or: --list variations.txt   (one per line)

# 3. One still and one clip per row of a CSV file or a Google Sheet published as CSV
node scripts/ad-creative.mjs sheet rows.csv          # or an https link to the CSV
#    columns: name, image_url, scene, motion, aspect_ratio (optional)

# 4. A studio still of the product alone and a turntable-style clip
node scripts/ad-creative.mjs spin product.jpg
```

Shared options: `--out <folder>` (default `ad-creative-out`), `--aspect 9:16|4:5|1:1|16:9`,
`--seconds 3..15` (default 5), `--max-cost <usd>` for one clip (default 1.5),
`--image-model`, `--video-model`, `--voice-model`, `--voice`. `--merge` (recipe 1) joins
clip and voice with `ffmpeg` if it is installed; without it the script prints the command.
`node scripts/ad-creative.mjs --help` lists everything; `--self-check` tests its parsers.

## What a run gives you

- Every file is saved in the output folder at once, and `run.json` there records each
  step: model, seconds it took, size, and what it was billed.
- Recipe 3 remembers finished rows in `done.json` in the output folder, so running it again
  (by hand or from cron) only makes new rows. A row that fails is tried again next time.
- A picture that times out is tried up to three times; a try that fails is not charged.
  A clip that ends failed, canceled or expired is not charged.

What it cost on a test run (2026-11-19, a plain water bottle): recipe 1 took 2 min 18 s
and $0.41; four variations took 3 min 13 s and $0.17; two sheet rows took 13 min and
$0.99; the spin took 3 min 11 s and $0.41. Prices and times change: trust `--dry-run`.

## Things to tell the user

- **The clip has the shape of its still.** For a 9:16 or 4:5 clip the still is made in
  that shape first. The video models used here take no aspect ratio (sending one is refused).
- **The bill can differ from the price shown at the start** when a job moves to another
  provider. `--max-cost` is the ceiling: a clip that would cost more is refused or cancelled
  at no charge.
- **Check every result against the real product.** Models can change a label, a colour or
  a shape, and they draw the sides the photo does not show.
- **People in these pictures are AI-generated.** Label them as AI where the platform or
  the law asks for it, and never present them as real customers, as a testimonial or as an
  expert.
- **No health claims, no before/after.** Do not write scenes or lines that promise a
  result, and follow the ad platform's rules for the product's category.
- **The models have makers with their own terms** for commercial use. The user should
  read the terms of the models they use before an ad runs.
- The API does not join clips, put captions on a video or lip-sync a face. Joining is
  `ffmpeg`. A timed caption file comes from the transcription endpoint:
  `curl https://api.uberllm.dev/v1/audio/transcriptions -H "Authorization: Bearer $UBERLLM_API_KEY" -F file=@voice.mp3 -F model=venice/whisper-large-v3 -F response_format=srt`

## If something fails

- `401`: the key is missing or wrong. `402`: no credit; the user adds some at
  https://uberllm.dev/dashboard/billing.
- `400 max_cost_too_low`: the clip can cost more than `--max-cost`; nothing was charged.
  Show the user the price and ask before raising it.
- "no provider" or "provider could not complete": not charged. Try again, or pass another
  `--video-model` from `GET https://api.uberllm.dev/v1/models?type=video`.
- A refusal that names content rules: change the scene or the line; do not try to get
  around it.

API reference: https://uberllm.dev/docs/media ·
workflows guide: https://uberllm.dev/docs/ad-creative-workflows
