# fonts

Two bitmap-derived faces, latin subset, served by `api.py` at
`/dns/fonts/<face>.woff2` and wired into `console.html` at runtime (the path
prefix is only known then — directly the console is `/dns`, behind the gateway
it is `/dns` on another host, and `/console` serves it from the root).

| file | family | used for |
| --- | --- | --- |
| `press-start-2p-latin.woff2` | Press Start 2P | the chrome: brand, tabs, buttons, column and card labels |
| `vt323-latin.woff2` | VT323 | the data: values, tables, JSON, prose |

Both are licensed under the SIL Open Font License 1.1 (Press Start 2P by
CodeMan38, VT323 by Peter Hull), fetched from the Google Fonts CDN and vendored
here on purpose: a console for the protocol's *name* layer should not need a
third party's name to resolve before it can draw itself, and a visitor whose
network blocks that CDN still gets the pixels.
