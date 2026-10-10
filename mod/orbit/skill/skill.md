# skill

Skill registry for orbit modules.

## Methods

- `list_skills()` — returns a list of module names that have a `skill.md` file (default action)
- `get_skill(name)` — returns the `skill.md` content for the named module; raises `ValueError` if not found

## Examples

```
m skill/list_skills
m skill/get_skill name=polymarket
```
