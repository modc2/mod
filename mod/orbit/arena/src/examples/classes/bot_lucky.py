"""The baseline: plays a legal move at random, at any game.

It reads `Legal moves: …` out of the view and picks one, so it can sit at any
game whose view says that much. Every rating needs a floor, and this is it —
an entrant that cannot beat this has not learned the game.

`random` is seeded from the match seed before this class is built, so the same
match played twice plays the same way.
"""

import random


class LuckyBot:
    """Picks uniformly from whatever the view calls legal."""

    name = "lucky"

    def play(self, view, seat):
        options = self.legal(view)
        if not options:
            # Nothing announced. Say nothing rather than guess: an empty move
            # is scored as illegal, which is the honest outcome.
            print("no legal moves found in the view")
            return ""
        return random.choice(options)

    def legal(self, view):
        for line in view.splitlines():
            head, _, rest = line.partition(":")
            if head.strip().lower() in ("legal moves", "moves", "options"):
                return [token.strip() for token in rest.replace(",", " ").split() if token.strip()]
        return []
