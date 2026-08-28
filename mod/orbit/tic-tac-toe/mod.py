"""Tic Tac Toe — a wasm game in the arena.

Three in a row.

The bytes are not here. They live in the store mod under the hash of
themselves, which is what `bytes()` fetches — so this directory is a pointer
and the game survives it being deleted.
"""
import json
import os


class Mod:
    path = os.path.dirname(os.path.abspath(__file__))

    def config(self):
        with open(os.path.join(self.path, 'config.json')) as f:
            return json.load(f)

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        """The card: what this game is, and where its bytes are."""
        return self.config()

    def abi(self):
        """The contract this module implements."""
        return self.config()['abi']

    def bytes(self):
        """The wasm, fetched from the store mod."""
        import base64
        import mod as m
        blob = m.mod('store')().get(self.config()['blob'])
        return base64.b64decode(blob['b64'])

    def play(self, players, seed: int = None):
        """Play a match of this game. The arena runs it and rates the result."""
        import mod as m
        return m.mod('arena')().play(game='tic-tac-toe', players=players, seed=seed)
