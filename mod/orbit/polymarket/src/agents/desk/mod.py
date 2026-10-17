"""polymarket.agents.desk — see README.md next to this file. The behaviour
lives in the app route named in config.json; this mod is its switch + handle."""

from __future__ import annotations

import os
import sys

from ..base.mod import Agent


class DeskAgent(Agent):
    def __init__(self):
        super().__init__(os.path.dirname(os.path.abspath(__file__)))


if __name__ == "__main__":
    a = DeskAgent()
    cmd = (sys.argv[1:] or ["info"])[0]
    print(getattr(a, cmd)() if cmd in ("on", "off", "toggle", "info", "enabled") else a.info())
