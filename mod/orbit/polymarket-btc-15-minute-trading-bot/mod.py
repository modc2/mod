import os
import mod as m

class Mod:
    description = """🤖 Polymarket BTC 15-Minute Trading Bot"""
    path = r'/root/mod/mod/orbit/polymarket-btc-15-minute-trading-bot'

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        return {
            'name': 'polymarket-btc-15-minute-trading-bot',
            'description': self.description,
            'path': self.path,
            'files': os.listdir(self.path),
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None

    def install(self):
        """Install Python dependencies."""
        import subprocess
        return subprocess.run(['pip', 'install', '-r', 'requirements.txt'], cwd=r'/root/mod/mod/orbit/polymarket-btc-15-minute-trading-bot', capture_output=True, text=True).stdout
