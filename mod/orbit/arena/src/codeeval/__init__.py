"""codeeval — a repo of choice, turned into a coding game the arena can play.

    from codeeval.harvest import build
    built = build('TheAlgorithms/Python', n=40, rounds=3)
    built['source']        # the game class, ready to upload

or, from the fleet, in one call that also stores it:

    m arena/codegame repo=TheAlgorithms/Python
    m arena/codeplay game=python-recon agents=builder,dev
"""
