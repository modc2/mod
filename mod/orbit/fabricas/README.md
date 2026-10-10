# fabricas

A custom clothing store where the customer is the designer.

No print-on-demand API, no design uploads to someone else's cloud. The
catalog, the design format, the mockup renderer, the pricing engine and the
order book all live in this directory plus one SQLite file under
`~/.mod/fabricas/`. Python stdlib only — nothing here calls out, so the shop
runs the same on an offline box as behind the gateway.

## What you can make

Five cuts, twelve stock fabrics, seven inks, four type families, seven marks.
A design is a small JSON dict — garment, fabric color, and up to eight text or
mark layers:

```json
{
  "garment": "tee",
  "color": "indigo",
  "name": "night shift",
  "layers": [
    {"type": "text",  "text": "MOD",  "font": "display", "ink": "gold",
     "x": 50, "y": 30, "size": 60, "rotate": 0},
    {"type": "shape", "shape": "bolt", "ink": "white",
     "x": 50, "y": 62, "size": 45, "rotate": 12}
  ]
}
```

Layer `x`/`y`/`size` are **percentages of the garment's print area**, not
pixels — so the same design re-renders correctly remixed onto a hoodie, a
tote or a cap. `GET /catalog` returns everything a client needs, including
the actual SVG geometry, so previews compose from the same paths the server
renderer uses.

## The store

- **Save** — designs are validated, normalized, and stored content-addressed:
  the id is a hash of the clean JSON, so an identical design saved twice is
  the same id.
- **Gallery + remix** — public designs go on a rack anyone can remix from;
  a remix carries `remix_of`, so attribution survives the save.
- **Quote** — deterministic pricing: garment base + per-layer print cost +
  size surcharge, with quantity breaks at 10 (−10%) and 25 (−20%).
- **Order** — the quote is frozen into the order at placement, then the order
  moves down one rail with every hop timestamped:

  ```
  placed → cutting → printing → sewing → ready → shipped
  ```

  No skips, no reversals; cancel is allowed until shipped.

## Run it

```
m fabricas                                # null call → info
m fabricas/catalog                        # cuts, fabric, inks, marks, type
m fabricas/quote design='{...}' qty=10    # price breakdown
m fabricas/save design='{...}'            # persist → short id
m fabricas/render id=ab12cd34ef           # SVG mockup
m fabricas/gallery                        # public designs
m fabricas/order id=ab12cd34ef size=L qty=2
m fabricas/orders author=me
m fabricas/advance id=<order>             # move one stop down the rail
m fabricas/serve                          # console + API on :51050
m fabricas/test                           # offline tests
```

Or standalone: `python3 serve.py --port 51050`, then open
`http://localhost:51050/fabricas/` — the designer console: pick a cut,
pick a fabric, stack layers with live preview and a live quote, save,
order, and remix off the gallery rack.

## Surfaces

| route | verb | what |
|---|---|---|
| `/catalog` | GET | the whole cutting table in one call |
| `/quote` | GET/POST | price breakdown (`design` inline or `id=`) |
| `/render` | GET | SVG mockup (`id=` or `design=` JSON, `width=`) |
| `/save` | POST | validate + persist a design |
| `/design`, `/designs`, `/gallery` | GET | the racks |
| `/remix` | GET | an editable copy with attribution attached |
| `/order` | POST | place an order for a saved design |
| `/orders` | GET | the order book (`author=`, `status=`) |
| `/advance` | POST | move an order one stop, or cancel |
| `/health` | GET | liveness + the shop's books at a glance |

All errors answer 4xx with the reason in the body, never 5xx.

## Layout

```
mod.py       the anchor — every fn the CLI/gateway/API dispatch into
atelier.py   the cutting table: catalog, validation, pricing, SVG renderer (pure, no I/O)
store.py     the books: designs + orders in SQLite (~/.mod/fabricas, FABRICAS_DATA overrides)
serve.py     one port: console at /fabricas/, API at /fabricas/api/* et al.
web/         the designer console, a single static file
tests/       offline pytest — a temp FABRICAS_DATA, never the real books
```
