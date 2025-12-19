class Prefi: 
    def __init__(self, prefix: str):
        self.prefix = prefix

    def add_prefix(self, word: str) -> str:
        return f"{self.prefix}{word}"

    def remove_prefix(self, word: str) -> str:
        if word.startswith(self.prefix):
            return word[len(self.prefix):]
        return word