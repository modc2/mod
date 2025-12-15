from typing import Any, Optional

class Memory:
    memory = {}
    def add(self, k:str, v : Any = None):
        if isinstance(k, dict):
            self.memory.update(k)
        else:
            self.memory[k] = v
        return self.memory

    def clear(self):
        self.memory = {}
        return self.memory

    def get(self, key=None):
        return self.memory.get(key, None) if key != None else self.memory

    def keys(self):
        return list(self.memory.keys())

    def rm(self, key):
        if key in self.memory:
            del self.memory[key]
        return self.memory

    def test(self):
        self.add('test1', 'This is a test memory item one.')
        self.add('test2', 'This is a test memory item two.')
        assert self.get('test1') == 'This is a test memory item one.'
        assert self.keys() == ['test1', 'test2']
        self.rm('test1')
        assert self.get('test1') == None
        self.clear()
        assert self.memory == {}
        return True


