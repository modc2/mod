# 3m

A mod orbit module.

## Path

`/root/mod/mod/orbit/3m`

## Entry points

- `forward(**kwargs)` — default entry point; returns `info()`.
- `info()` — returns `{ name, description, path, files }`.
- `readme()` — returns the contents of this file.

## Usage

```python
import mod as m
mod = m.mod('3m')
mod.info()
mod.readme()
```
