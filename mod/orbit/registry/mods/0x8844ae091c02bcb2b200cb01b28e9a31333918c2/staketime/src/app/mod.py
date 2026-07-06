import os
import mod as m

class Mod:
    description = """StakeTime app"""
    path = r'/Users/broski/mod/mod/orbit/staketime/src/app'

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        return {
            'name': 'app',
            'description': self.description,
            'path': self.path,
            'files': [f for f in os.listdir(self.path) if not f.startswith('.')],
        }
