class Memory:
    memory = []
    def add(self, *items):
        for item in items:
            self.memory.append(item)
        return self.memory

    def clear(self):
        self.memory = []
        return self.memory

    def get(self, query=None):
        if not hasattr(self, 'memory'):
            self.memory = []
        if query is not None:
            return [item for item in self.memory if query.lower() in str(item).lower()]
        return self.memory
