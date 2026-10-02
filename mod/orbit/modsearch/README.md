# modsearch

Find a module by what you mean, not the words it happens to use.

- **Local-first**: `all-MiniLM-L6-v2` (CPU, ~80 MB, cached weights) fused with BM25. No key, no cloud, no LLM call.
- **Degrades, never dies**: if the encoder can't load, it answers on BM25 and says `mode: "lexical"`.
- **Cached**: vectors keyed by `sha256(model + text)` in `~/.mod/modsearch/vectors.jsonl`, so re-ranking a fleet costs one query encode (~40 ms).
- **Reusable**: POST your own docs. The service never decides who sees what.

```
m modsearch/search "rent a gpu"            # in-process, no server
m modsearch/serve                          # 127.0.0.1:51090, pm2 `modsearch`
curl -XPOST 127.0.0.1:51090/search -d '{"query":"split a secret","docs":[{"id":"a","text":"..."}]}'
```

Scoring is `0.72·cosine + 0.28·bm25(norm) + name bonus`. A doc is kept only if its cosine clears `max(0.2, 0.6·best)`, or it is a strong word or name hit.

Used by: the build hub's **◇ agent** search (`/build/_api/hub/search`).
