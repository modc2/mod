import mod as m
import json
import time

class Mod:
    description = "LLM-powered search to find the best orbit module for a task"
    _catalog_cache = None
    _catalog_cache_time = None
    _result_cache = {}
    _result_cache_times = {}

    def __init__(self, **kwargs):
        pass

    def forward(self, query, n=3, **kwargs):
        """Search for the best module matching a natural language query.

        Args:
            query: Natural language description of what you need
            n: Number of top results to return (default 3)
        """
        cache_key = (query, int(n))
        cached_time = Mod._result_cache_times.get(cache_key)
        if cached_time is not None and (time.time() - cached_time) < 120:
            return Mod._result_cache[cache_key]

        catalog = self.catalog()
        visible = {name: desc for name, desc in catalog.items() if desc.strip()}

        stopwords = {"the", "and", "for", "with", "that", "this", "from", "what", "how", "best", "need", "want"}
        tokens = [w for w in query.lower().split() if len(w) >= 3 and w not in stopwords]
        if tokens:
            scored = sorted(
                visible.items(),
                key=lambda kv: sum(t in f"{kv[0]} {kv[1]}".lower() for t in tokens),
                reverse=True
            )
            keep = max(int(n) * 10, 30)
            top = scored[:keep]
            if len(top) >= int(n):
                visible = dict(top)

        catalog_text = '\n'.join(f'- {name}: {desc}' for name, desc in visible.items())

        prompt = f"""You are a module search engine for a Python framework with {len(visible)} modules.

Given the user's query, find the {n} best matching modules from the catalog below.

USER QUERY: {query}

MODULE CATALOG:
{catalog_text}

Return ONLY valid JSON (no markdown fences) in this exact format:
[{{"name": "module_name", "reason": "one sentence why this is a good match"}}]

Return exactly {n} results, ranked best first. If fewer than {n} modules are relevant, return only the relevant ones."""

        try:
            router = m.mod('model.openrouter')()
            response = router.forward(prompt, free=True)
        except Exception as e:
            return {'query': query, 'results': [], 'error': str(e)}

        try:
            results = json.loads(response)
        except json.JSONDecodeError:
            start = response.find('[')
            end = response.rfind(']') + 1
            if start >= 0 and end > start:
                results = json.loads(response[start:end])
            else:
                return {'query': query, 'results': [], 'raw': response}

        results = [r for r in results if r.get('name') in visible]
        results = results[:int(n)]

        for r in results:
            r['description'] = catalog.get(r['name'], '')

        result = {'query': query, 'results': results}
        if results:
            Mod._result_cache[cache_key] = result
            Mod._result_cache_times[cache_key] = time.time()
        return result

    def catalog(self, refresh=False):
        """Return dict of {module_name: description} for all modules."""
        cache_expired = Mod._catalog_cache_time is None or (time.time() - Mod._catalog_cache_time > 600)
        if self._catalog_cache and not refresh and not cache_expired:
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

        Mod._catalog_cache = catalog
        Mod._catalog_cache_time = time.time()
        return catalog
