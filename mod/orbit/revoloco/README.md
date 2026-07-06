# base

A minimal example mod showing the standard module structure.

## Structure

```
base/
├── base/
│   └── mod.py    # Anchor file with Mod class
└── README.md
```

## Usage

```python
import mod as m

# Load and run
base = m.mod('base')()
result = base.forward(3, 4)  # 7
```

```bash
# CLI
m base forward a=3 b=4
```

## Creating a New Mod

Every mod follows this pattern:

1. Create a directory: `orbit/<name>/<name>/mod.py`
2. Define a `Mod` class with a `description` and a `forward` method:

```python
class Mod:
    description = """
    What your mod does
    """

    def forward(self, **kwargs):
        """Entry point for the mod."""
        # your logic here
        return result
```

The `forward` method is the default entry point called when the mod is invoked. Additional methods can be called via `m.fn('name/method')()`.
