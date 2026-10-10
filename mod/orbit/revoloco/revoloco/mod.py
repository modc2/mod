
class Mod:
    description = """
    Minimal example mod — adds two numbers.
    """

    def forward(self, a=1, b=2) -> int:
        """Add two numbers and return the result."""
        return a + b
