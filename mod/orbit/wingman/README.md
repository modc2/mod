# wingman

A folder of photos in, a dating-app-ready lineup out.

```
m wingman/add dir=~/Pictures/me            # a set, from a folder (or path=, url=, data=)
m wingman/audit <set>                       # what each photo is, and what it costs
m wingman/lineup <set> n=6                  # the best six, in order, with the gaps named
m wingman/render <set> preset=hinge         # face-aware crops, polished, EXIF gone
m wingman/export <set> preset=tinder        # the lineup zipped in slot order + report.json
m wingman/serve                             # console, API and MCP on :50830
```

API `:50830` (`/api/wingman`) · console `/wingman` · MCP `POST /mcp` (12 tools)

## What it does

Tinder, Hinge and Bumble each show a photo in a fixed card, and most people
upload whatever the camera roll had: the landscape shot where the face is a
dot, the group where nobody knows which one is you, two near-identical selfies,
and a 12 MB file with the GPS coordinates of their kitchen in it.

This module does the part of that a program can do, and says so about the
part it cannot.

| step | what happens | what you get back |
|---|---|---|
| **add** | photos into a set, kept as they arrived | ids, sizes, whether EXIF carries GPS |
| **audit** | every photo measured | faces, role, sharpness, exposure, issues with a cost each, a score, a one-line verdict |
| **lineup** | the best N, in order | why each slot, why the rest were left out, and `gaps` |
| **render** | face-aware crop to an app's card ratio, gentle polish | the crop box, what was applied, whether it had to upscale |
| **export** | lineup → renders → zip | `01-…jpg` … `06-…jpg` + `report.json` |

## What is measured

| measure | how | thresholds |
|---|---|---|
| faces | UltraFace RFB-320 on onnxruntime, letterboxed to 320×240, NMS 0.3 | confidence ≥ 0.7; below 0.85 is flagged uncertain |
| role | largest face height ÷ frame height | headshot ≥ 28% · portrait ≥ 12% · full ≥ 4% · far below · **group** when a second face is ≥ half the first · **scene** when none |
| sharpness | Laplacian variance on a 640-wide grey copy; the face crop measured again on its own | < 25 blurry (bad) · < 60 soft · face < 20 soft |
| exposure | mean luminance, clipped pixel fractions, std | < 60 dark · > 200 bright · > 8% blown · > 25% crushed · std < 30 flat |
| resolution | short side | < 640 px bad · < 1000 thin |
| duplicates | 64-bit dHash | Hamming ≤ 10 collapses to the higher score |
| privacy | EXIF GPS IFD on intake | flagged; every render is written with no exif, icc, xmp or thumbnail |

Score starts at 100 and each issue subtracts its cost. `bad` issues (blurry,
low-res) disqualify a photo from leading. **lead_ok** means: a solo headshot
or head-and-shoulders, face 12–55% of the frame, nothing bad, none of soft /
dark / bright / blown / face-far / face-tight, score ≥ 60.

If the ONNX model cannot be fetched or onnxruntime is missing, faces fall back
to the largest skin-coloured blob in YCbCr. Every audit and lineup then says
`detector: skin-heuristic`, the boxes are marked uncertain, and the crops
should be looked at before they are trusted.

## The lineup rule

1. Near-duplicates collapse — the higher-scoring one survives, the other is
   listed under `left_out` with the twin's name.
2. **Slot 1** is the best `lead_ok` photo. If none exists, the best solo face
   leads by default and `gaps` says the set has no proper lead — which means a
   camera, not an edit.
3. Then variety: portrait, full-body, headshot, a wider shot, another portrait,
   another full — each the best unused photo of that role with no `bad` issue.
4. Fill with the best remaining clean photos; photos with a `bad` issue only as
   a last resort and labelled so.
5. A group shot never before slot 4 and at most once (`allow_group=false` to ban).
6. Anything under `min_score` (35) is left out with its worst issue named.

`gaps` then says what no edit can add: no full-body shot, three headshots
(the same framing three times reads as one photo), everything a face shot, too
few usable photos for the slots.

## The render

The crop is face-aware: the largest crop of the card's ratio that fits,
shrunk when `zoom=auto` so the face is ~30% of the crop height (never
enlarging the source more than 1.5× to get there), positioned with the face
centred horizontally, the eyes about 40% down and at least 8% headroom, then
clamped to the image. No face → centred, biased up. A group → the union of the
faces.

The polish is global and gentle: autocontrast at 0.5% blended 60%, a gamma
lift only when the frame's mean luminance is under 95 (or a pull when over
165), contrast ×1.12 only on a flat frame, colour ×1.04, unsharp r1.0 55%.
`polish=none` skips all of it. **No skin smoothing, no reshaping, no
background replacement** — this is a crop-and-clean, not a filter, and the
report lists exactly what was applied.

Output is a progressive JPEG at the preset size, quality 90, converted to sRGB
when the source carried a profile, with every metadata field absent.

| preset | ratio | size | note |
|---|---|---|---|
| `tinder` | 4:5 | 1080×1350 | the bottom third sits under the name overlay |
| `hinge` | 4:5 | 1080×1350 | no overlay; what you crop is what shows |
| `bumble` | 3:4 | 1080×1440 | a little taller, keeps more body |
| `square` | 1:1 | 1080×1080 | avatars, grids |
| `story` | 9:16 | 1080×1920 | prompts and stories |

Ratios are observations, not contracts — apps redesign. `ratio=4:5 size=1080x1350`
overrides any of them.

## Not measured

Expression, eye contact, whether the outfit works, whether the setting says
anything about you, whether it looks like you. No number here claims to know
these, and `audit` returns `not_measured` saying so. A high score means the
photo is technically sound; it does not mean it is a good photo of you.

## Privacy

Photos never leave this machine. A set is addressed by a 16-hex unguessable id
and that id is the only thing protecting it — the URL is the key. `GET /sets`
(the list) answers only loopback callers or requests carrying
`x-wingman-token` from `~/.mod/wingman/token`; the console picks the token up
automatically when opened on the box. Set `WINGMAN_BIND=127.0.0.1` to keep the
port off the network entirely, or put it behind the gateway's auth.

## Console

`/wingman` — six tabs: **photos** (drop a folder, see face boxes), **audit**
(every issue, with its cost), **lineup** (the slots, the left-out, the gaps),
**render** (source next to the result, the crop and the polish spelled out),
**export** (the zip), **mcp** (the tools and the guide).

## MCP

```json
{"mcpServers": {"wingman": {"type": "http", "url": "http://localhost:50830/mcp"}}}
```

`wingman_health` · `wingman_guide` · `wingman_sets` · `wingman_new` ·
`wingman_add` · `wingman_audit` · `wingman_faces` · `wingman_lineup` ·
`wingman_render` · `wingman_export` · `wingman_remove` · `wingman_delete`

## State

```
~/.mod/wingman/
  models/version-RFB-320.onnx     the face detector, fetched once
  token                           owner token for listing sets remotely
  sets/<id>/set.json              photos and their intake metadata
  sets/<id>/audit.json            cached measurements (version-stamped)
  sets/<id>/src/                  originals, untouched
  sets/<id>/out/<photo>-<preset>.jpg (+ .json report)
  sets/<id>/thumb/                console thumbnails
  sets/<id>/wingman-<preset>.zip  the export
```

## Requires

Pillow, numpy; onnxruntime for the detector (optional, but the fallback is a
guess); `pillow-heif` for HEIC straight off an iPhone (optional — without it,
export as JPEG first and the error says so).

```
m wingman/test
```
