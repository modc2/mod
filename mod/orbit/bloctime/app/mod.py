import os
import mod as m

class Mod:
    description = """BlocTime app"""
    path = os.path.dirname(__file__)

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {
            'name': 'app',
            'description': self.description,
            'path': self.path,
            'files': [f for f in os.listdir(self.path) if not f.startswith('.')],
        }
