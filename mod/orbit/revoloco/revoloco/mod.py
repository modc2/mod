
def _normalize(result):
    return int(result) if result == int(result) else result


def _coerce(a, b):
    try:
        return float(a), float(b)
    except (ValueError, TypeError) as e:
        raise ValueError(f"requires numeric inputs for a and b; got a={a!r}, b={b!r}") from e


class Mod:
    description = """
    Minimal example mod — supports add, subtract, multiply, and divide.
    """

    def forward(self, a=1, b=2) -> int | float:
        """Add two numbers and return the result."""
        a, b = _coerce(a, b)
        result = a + b
        return _normalize(result)

    def add(self, a=1, b=2) -> int | float:
        """Add two numbers and return the result."""
        return self.forward(a, b)

    def multiply(self, a=1, b=2) -> int | float:
        """Multiply two numbers and return the result."""
        a, b = _coerce(a, b)
        result = a * b
        return _normalize(result)

    def subtract(self, a=1, b=2) -> int | float:
        """Subtract b from a and return the result."""
        a, b = _coerce(a, b)
        result = a - b
        return _normalize(result)

    def divide(self, a=1, b=2) -> int | float:
        """Divide a by b and return the result."""
        a, b = _coerce(a, b)
        if b == 0:
            raise ValueError("division by zero: b must not be 0")
        result = a / b
        return _normalize(result)
