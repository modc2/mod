# dstore

Decentralized storage facade — unified `put` / `get` / `list` / `pin` over the
`filecoin` and `hippius` orbit modules. Indexes objects per Ethereum-address
owner in SQLite.

The user-facing app + FastAPI gateway live at **`mod/core/store/`** (since the
core store namespace is the natural home for storage UX). This module is the
Python brain that the app calls into.

## Run the app

```bash
~/mod/mod/core/store/serve.sh           # launches API (50150) + Next.js app (50151)
# then open http://localhost:50151
```

## Direct CLI / Python

```bash
m dstore/status
m dstore/put /path/to/file backend=both owner=0xabc
m dstore/get bafy...
m dstore/list owner=0xabc
```

```python
import mod as m
d = m.mod('dstore')()
d.put('/path/to/file', backend='both', owner='0xabc')
d.list(owner='0xabc')
```
