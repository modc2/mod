import mod as m
import json

class Mod:
    description = "LLM-powered search to find the best orbit module for a task"

    def __init__(self, **kwargs):
        self._catalog_cache = None

    def forward(self, query, n=3, **kwargs):
        """Search for the best module matching a natural language query.

        Args:
            query: Natural language description of what you need
            n: Number of top results to return (default 3)
        """
        catalog = self.catalog()
        catalog_text = '\n'.join(f'- {name}: {desc}' for name, desc in catalog.items())

        prompt = f"""You are a module search engine for a Python framework with {len(catalog)} modules.

Given the user's query, find the {n} best matching modules from the catalog below.

USER QUERY: {query}

MODULE CATALOG:
{catalog_text}

Return ONLY valid JSON (no markdown fences) in this exact format:
[{{"name": "module_name", "reason": "one sentence why this is a good match"}}]

Return exactly {n} results, ranked best first. If fewer than {n} modules are relevant, return only the relevant ones."""

        router = m.mod('model.openrouter')()
        response = router.forward(prompt, free=True)

        try:
            results = json.loads(response)
        except json.JSONDecodeError:
            start = response.find('[')
            end = response.rfind(']') + 1
            if start >= 0 and end > start:
                results = json.loads(response[start:end])
            else:
                return {'query': query, 'raw': response}

        for r in results:
            r['description'] = catalog.get(r['name'], '')

        return {'query': query, 'results': results}

    def catalog(self, refresh=False):
        """Return dict of {module_name: description} for all modules."""
        if self._catalog_cache and not refresh:
            return self._catalog_cache

        catalog = {}
        for name in m.mods():
            try:
                mod_cls = m.mod(name)
                desc = getattr(mod_cls, 'description', '') or ''
                if callable(desc):
                    desc = desc()
                catalog[name] = str(desc).strip()[:200]
            except Exception:
                catalog[name] = ''

        self._catalog_cache = catalog
        return catalog
