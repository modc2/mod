# secretshare

Split a secret (password, seed phrase, key file) into **N pieces** so that any **K**
of them bring it back and fewer than K reveal nothing. This is Shamir's Secret Sharing over GF(256).

- **Local-first:** the console splits and unlocks inside the browser tab. The API
  processes requests in memory and stores nothing. No store, chain or network deps.
- **One format everywhere:** `ss1.<set>.<k>.<x>.<payload>`. `shamir/` (python) and
  `app/shamir.js` (browser/node) are byte-compatible, and the tests check both directions.
- **Fails loudly:** a 4-byte sha256 checksum is split along with the secret, so wrong,
  tampered or mixed-up pieces raise an error instead of returning garbage.

```
m secretshare/split "correct horse battery" n=5 k=3     # or path=./seed.txt out=./pieces/
m secretshare/combine "ss1.… ss1.… ss1.…"               # or path=./pieces/
m secretshare/inspect ss1.…
m secretshare/test
m secretshare/serve        # pm2 `secretshare`, :51080 → http://localhost:51080/secretshare/
m secretshare/status | kill
```

HTTP (same process as the console, bare or under `/secretshare/api`):
`GET /health` · `GET /info` · `POST /split {secret,n,k,b64?}` · `POST /combine {shares}` · `POST /inspect {share}`.

Layout: `shamir/` engine · `api/api.py` FastAPI (serves the API and the console) · `app/` console · `test/`.
