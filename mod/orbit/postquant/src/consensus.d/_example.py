"""Template for a consensus plugin. Files starting with "_" are skipped —
copy this to e.g. supermajority.py to load it.

An engine only answers the rule; the chain does the rest (seals, votes,
migration in a header, replay in pq_verify). Every vote it counts is a
signature under one of the chain's post-quantum key types.
"""


def register(consensus):
    class Supermajority(consensus.BFT):
        name = "supermajority"
        title = "BFT with a 3/4 quorum"
        summary = "like bft, but 3/4 of voting power must sign"
        faults = "byzantine: safe with f < n/4; halts sooner than bft"

        def quorum(self, epoch, stake):
            total = sum(self.powers(epoch, stake).values())
            return total * 3 // 4 + 1

    consensus.register(Supermajority())
