# hub (core module)

The module catalog. It walks the repo (`orbit/` + `core/`) and answers one
question: **what modules exist, and what is each one?**

- **CLI:** `m hub/modules`, `m hub/names orbit`, `m hub/doc claude`,
  `m hub/desc claude`, `m hub/dir claude`, `m hub/search auth`, `m hub/info`

```python
import mod as m
hub = m.mod('hub')()
hub.modules('core')          # [{name, group, description, readme, skill}, ...]
hub.doc('claude')            # {module, description, readme, skill}
```

A module's description comes from its `config.json` (`<mod>/config.json`, or
`<mod>/<name>/config.json` for modules that nest their package). Set `MOD_REPO`
to point the catalog at a different tree.

Used by [`docs`](../docs), which lists `hub` in its `deps` and re-exports
`m docs/modules` / `m docs/doc` on top of it.
