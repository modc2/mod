
class Mod:
    description = """
    Minimal example mod — adds two numbers.
    """

    def forward(self, a=1, b=2) -> int | float:
        """Add two numbers and return the result."""
        try:
            a, b = float(a), float(b)
        except (ValueError, TypeError) as e:
            raise ValueError(f"forward() requires numeric inputs for a and b; got a={a!r}, b={b!r}") from e
        result = a + b
        return int(result) if result == int(result) else result
