# skill

Skill registry for orbit modules.

## Methods

- `list_skills()` — returns `[{name, hint}]` for all modules that have a `skill.md` file (default action)
- `get_skill(name)` — returns `{name, hint, skill}` for the named module (`skill` is the full `skill.md` text); raises `ValueError` if not found
- `search(query)` — returns `[{name, hint, match_in}]` for modules whose name, hint, or full skill body matches the query (case-insensitive); `match_in` is `"name"`, `"hint"`, or `"body"` indicating which field first matched; hint comes from the YAML frontmatter `description:` field if present, otherwise the first non-heading line of the skill

## forward() kwargs

- `name=<module>` — fetch that module's skill
- `query=<keyword>` — search across all skills by keyword

## Examples

```
m skill/list_skills
m skill/get_skill name=polymarket
m skill/search query=compute
m skill query=gpu
```
