
import json
from pathlib import Path


ORBIT_ROOT = Path(__file__).parent.parent.parent


class Mod:
    description = """
    Module registry — lists orbit modules with their metadata (name, description, version)
    read from each module's config.json.
    """

    def _load_all(self):
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
                })
            except (json.JSONDecodeError, OSError):
                pass
        return modules

    def forward(self, name: str = None):
        """Return all modules, or a single module by name."""
        modules = self._load_all()
        if name is None:
            return modules
        for mod in modules:
            if mod["name"] == name:
                return mod
        raise KeyError(f"Module not found: {name!r}")
