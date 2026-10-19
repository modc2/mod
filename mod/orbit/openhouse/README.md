# openhouse

Rent-to-own housing, on-chain. The protocol takes 0-5% (owner-set, capped in the contract); the other 95-100% of every payment stays with the property, split between the renter's equity and the owner's income. This node runs in **simulation** - no real money or deed is involved.

- **Ports:** API `50132`, app `50131` (basePath `/openhouse`, gateway `modc2.com/openhouse`).
- **Layout:** this root holds only `config.json` and the Docker files. All code lives in [`src/`](src/).
- **Docs:** start at [`src/README.md`](src/README.md), then [`src/docs/`](src/docs/).

## Quick start

```bash
# from /root/mod
python3 -c "import mod as m; m.mod('openhouse')().serve_app()"   # pm2: openhouse.api + openhouse.app
curl localhost:50132/health
cd mod/orbit/openhouse/src && python3 -m pytest tests -q          # 149 tests, ~80 s
```
