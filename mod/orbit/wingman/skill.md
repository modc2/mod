# wingman

Turn a set of photos into Tinder/Hinge/Bumble-ready portraits: measure each
one, pick the best N in order, crop face-aware to the app's card ratio, polish
gently, strip all metadata, zip in slot order. Fourteen MCP tools, a REST API and
a console on one port (`:50830`). Nothing retouched. Everything runs on the box
except `wingman_read`, which sends a stripped copy of each photo to orbit/venice
for the things a measurement cannot reach.

API `:50830` (`/api/wingman`) · console `/wingman` · MCP `POST /mcp`

## When to reach for it

- "which of these photos should I use" / "what order"
- "make these fit Tinder / Hinge / Bumble" — the crop, not a filter
- "is this photo good enough" — for the parts a program can check
- "what's wrong with my profile photos" — `gaps` is the answer, and it is
  usually "no full-body shot" or "nothing sharp enough to lead"
- "strip the location data before I upload"
- "why is my profile not working" when the photos are technically fine — that
  is `wingman_read`, and it needs asking first (see below)

Not for: retouching, background removal, AI headshots, anything that changes
the face. The measured half cannot see expression, outfit or setting and says
so (`not_measured`); `wingman_read` is the half that can, at the cost of
sending the photo.

## The order that matters

1. **`wingman_add`** — `dir=` a folder on this box, `path=` one file, `url=`,
   or `data=` base64 / `files=[{name,data}]`. Omit `set` and one is created;
   keep the returned id, it is the only handle. Exact duplicates are skipped.
2. **`wingman_audit`** — read `verdict` per photo first, then `issues`. Each
   issue has `severity` (bad / warn / info), a `cost`, and a sentence. `score`
   = 100 − costs. `lead_ok` is the photo that can go first.
3. **`wingman_lineup`** — `slots` in order with `why`; `left_out` with why;
   `gaps` with what the set is missing. Relay `gaps` verbatim: they are the
   part of the answer the person can act on, and the fix is a camera.
4. **`wingman_export`** — preset `tinder|hinge|bumble|square|story`; returns a
   zip path. Or **`wingman_render`** for one photo, one preset, or a custom
   `ratio=`/`size=`.

## `wingman_read` — ask before you call it

The only tool here that sends anything anywhere. It shows a 768 px,
metadata-free copy of each photo to a vision model on orbit/venice and returns
expression, whether the eyes are open and on the camera, selfie vs mirror
selfie, whether a stranger can tell which person is you, setting, outfit,
what is cluttering the frame — and across the set, what repeats plus Hinge
prompt openings drawn from what the photos actually show.

- **Tell the person it sends, and get a yes.** The original never moves and no
  EXIF/GPS goes with it, but a photo of their face reaches a model.
- `wingman_venice` first: `can_read` is false when there is no Venice key on
  file, and the fix is `m wingman/venice_key <key>` on the box.
- Answers come back as `read`/`read_flags` with `source: read` and no `cost`.
  They never change `score`. Relay them as a model's opinion, not a number.
- After a read, `wingman_audit` carries `read` per photo and `wingman_lineup`
  adds the repetition gaps — both from cache, neither sends.
- `sent.json` counts every send including refused ones. `enabled=0` or
  `WINGMAN_VENICE=off` switches the whole path off.

## Reading a result

- `role`: headshot / portrait / full / far / scene (no face) / group.
- `face_fraction`: face height ÷ frame height. Lead wants 0.12–0.55.
- `detector`: `ultraface-rfb-320` is real; `skin-heuristic` means the ONNX
  model was unavailable and every box is a guess — say so.
- `crop_how`: "face 30% of crop height (auto)" is the normal case; "largest
  crop of the aspect" means no face anchored it.
- `upscale` > 1.5 (and a warning) means the source was too small for the card.
- `polish`: the exact list applied. `polish=none` for a straight crop.
- `stripped`: always exif, gps, icc, xmp, thumbnail.

## Traps

- Two near-identical photos count as one; the lower-scoring is `left_out`
  with the twin's name. This is deliberate.
- A group shot never leads and appears at most once, after slot 3.
  `allow_group=false` bans it.
- Scores are technical soundness, not attractiveness. A 100 is a sharp,
  well-lit, well-framed solo face; it says nothing about the smile.
- Card ratios are observations as of 2026; `ratio=` overrides them.
- `GET /sets` (the list) is loopback/token only. A set id is the key to the
  photos — do not paste it anywhere it will be indexed.
- HEIC needs `pillow-heif`; without it the error says to export JPEG first.
- `wingman_read` is the only tool that leaves the box. Everything else — audit,
  lineup, render, export — reads the cache and stays local, so a read once done
  is free forever after.

## Shell

```
m wingman/add dir=~/Pictures/me
m wingman/audit <set>
m wingman/lineup <set> n=6
m wingman/export <set> preset=hinge
m wingman/venice                 # is the read path live?
m wingman/read <set>             # the vision pass — this one sends
m wingman/serve
```
