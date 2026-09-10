# plinyworld — forked from elder-plinius.github.io

Upstream: <https://github.com/elder-plinius/elder-plinius.github.io>
(the site that renders at elder-plinius.github.io). The pinned upstream commit
is in `upstream/COMMIT`; `m pliny/update` re-pulls it.

## What the app actually is

A red-team **proof-of-concept for a clipboard-hijacking ("pastejacking")
attack**, disguised as an innocuous "Poetic Echoes" poetry page. Every nav item
and poem link carries a hidden click handler that, in the original
`upstream/triggers.js`, **silently overwrites the visitor's clipboard** with a
payload string plus one of fifteen **typosquatted phishing URLs**
(`paypa1.com`, `g00gle.com`, `am4zon.com`, `micr0soft.com`, `y0utube.com`).
Paste anywhere and you propagate the attacker's link without ever having seen
it. It is a teaching artifact about how a benign-looking page can weaponize the
clipboard API.

## What plinyville changed (and why)

plinyville hosts this as an **exhibit, not a live weapon**. Serving an exact
copy would put a working clipboard-phishing payload on the public internet,
firing at every unwitting visitor — that is the one thing this fork will not do.

- `upstream/index.html`, `upstream/triggers.js` — the pinned upstream files,
  preserved verbatim for study. **`triggers.js` is never wired into the served
  page.**
- `triggers.defanged.js` — the script that actually runs. Clicking a trigger
  writes **nothing** to the clipboard and navigates nowhere; it instead reveals,
  inline, exactly what the live version *would* have copied. The mechanism stays
  legible; no visitor leaves with a live phishing link on their clipboard.
- The served page (assembled by `mod.py`) swaps the `<script>` to the defanged
  file and prepends a banner marking it as a defanged demonstration.

If you want to study the raw payload, read `upstream/triggers.js` — don't run it
against anyone.
