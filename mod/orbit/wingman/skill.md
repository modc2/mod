# wingman

Turn a set of photos into Tinder/Hinge/Bumble-ready portraits: measure each
one, pick the best N in order, crop face-aware to the app's card ratio, polish
gently, strip all metadata, zip in slot order. Twelve MCP tools, a REST API and
a console on one port (`:50830`). Local only. Nothing retouched.

API `:50830` (`/api/wingman`) · console `/wingman` · MCP `POST /mcp`

## When to reach for it

- "which of these photos should I use" / "what order"
- "make these fit Tinder / Hinge / Bumble" — the crop, not a filter
- "is this photo good enough" — for the parts a program can check
- "what's wrong with my profile photos" — `gaps` is the answer, and it is
  usually "no full-body shot" or "nothing sharp enough to lead"
- "strip the location data before I upload"

Not for: retouching, background removal, AI headshots, anything that changes
the face. Not for judging expression, outfit or setting — it cannot see them
and says so (`not_measured`).

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

## Shell

```
m wingman/add dir=~/Pictures/me
m wingman/audit <set>
m wingman/lineup <set> n=6
m wingman/export <set> preset=hinge
m wingman/serve
```
