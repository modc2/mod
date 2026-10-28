
import json
import time
from pathlib import Path


ORBIT_ROOT = Path(__file__).parent.parent.parent


class Mod:
    description = """
    Module registry — lists orbit modules with their metadata (name, description, version, port)
    read from each module's config.json.

    forward(port_only=True) returns only modules that expose a service port (port is not null),
    useful for service-discovery without client-side filtering.
    """

    _cache = None
    _cache_at = 0.0

    def _load_all(self):
        if Mod._cache is not None and time.time() - Mod._cache_at < 5:
            return Mod._cache
        modules = []
        for config_path in sorted(ORBIT_ROOT.glob("*/config.json")):
            try:
                data = json.loads(config_path.read_text())
                modules.append({
                    "name": data.get("name", config_path.parent.name),
                    "description": data.get("description", ""),
                    "version": data.get("version", ""),
                    "schema": data.get("schema", ""),
                    "port": data.get("port", None),
                    "urls": data.get("urls", None),
                    "icon": data.get("icon", ""),
                    "color": data.get("color", ""),
                    "fns": data.get("fns", []),
                    "submods": data.get("submods", {}),
                })
            except (json.JSONDecodeError, OSError):
                pass
        Mod._cache = modules
        Mod._cache_at = time.time()
        return modules

    def forward(self, name: str = None, port_only: bool = False, q: str = None):
        """Return all modules, or a single module by name.

        port_only: if True, restrict the list to modules where port is not None.
        Ignored when name is provided (single-module lookup is unaffected).

        q: case-insensitive substring filter applied to name and description.
        Ignored when name is provided. Composes with port_only (both filters applied).
        Example: forward(q="chain") → all modules whose name or description contains "chain".

        When name is provided and no matching module is found, returns None.
        """
        modules = self._load_all()
        if name is None:
            if port_only:
                modules = [m for m in modules if m["port"] is not None]
            if q is not None:
                needle = q.lower()
                modules = [m for m in modules if needle in m["name"].lower() or needle in m["description"].lower()]
            return modules
        for mod in modules:
            if mod["name"] == name:
                return mod
        return None
