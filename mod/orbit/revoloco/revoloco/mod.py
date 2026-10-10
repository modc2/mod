
def _coerce(a, b):
    try:
        return float(a), float(b)
    except (ValueError, TypeError) as e:
        raise ValueError(f"requires numeric inputs for a and b; got a={a!r}, b={b!r}") from e


class Mod:
    description = """
    Minimal example mod — adds two numbers.
    """

    def forward(self, a=1, b=2) -> int | float:
        """Add two numbers and return the result."""
        a, b = _coerce(a, b)
        result = a + b
        return int(result) if result == int(result) else result

    def multiply(self, a=1, b=2) -> int | float:
        """Multiply two numbers and return the result."""
        a, b = _coerce(a, b)
        result = a * b
        return int(result) if result == int(result) else result
