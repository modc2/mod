from substrate_protocol import DecayProtocol

class BaseMod:
    description = """
    Substrate Protocol: Forced Transfer Decay System
    
    A substrate-based protocol that:
    - Forces wallets to send tokens to each other
    - Implements decay function for stale/inactive keys
    - Redistributes inflation from decayed keys to early participants at random intervals
    - Scores wallets based on total transfer amounts
    
    Mechanics:
    - Wallets must transfer tokens or face decay penalties
    - Inactive wallets (>24h) lose 1% per period to inflation pool
    - Early registered keys receive random inflation rewards
    - Leaderboard tracks most active transferrers
    """
    
    def __init__(self):
        self.protocol = DecayProtocol()
        
    def run(self):
        """Execute protocol cycle"""
        self.protocol.run_protocol_cycle()
        return self.protocol.get_leaderboard()
