# revoloco

A minimal example mod showing the standard module structure.

## Structure

```
revoloco/
├── revoloco/
│   └── mod.py    # Anchor file with Mod class
└── README.md
```

## Usage

```python
import mod as m

# Load and run
revoloco = m.mod('revoloco')()
result = revoloco.add(3, 4)        # 7
result = revoloco.multiply(3, 4)   # 12
result = revoloco.subtract(10, 3)  # 7
result = revoloco.divide(10, 4)    # 2.5
```

```bash
# CLI
m revoloco add a=3 b=4
m revoloco multiply a=3 b=4
m revoloco subtract a=10 b=3
m revoloco divide a=10 b=4
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
