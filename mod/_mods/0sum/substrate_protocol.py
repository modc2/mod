from substrateinterface import SubstrateInterface, Keypair
from scalecodec.types import GenericCall
import random
import time

class DecayProtocol:
    """
    Substrate protocol forcing wallet transfers with decay mechanics
    Score based on transfer amounts, inflation redistributed to early keys
    """
    
    def __init__(self, substrate_url='ws://127.0.0.1:9944'):
        self.substrate = SubstrateInterface(url=substrate_url)
        self.wallets = {}
        self.scores = {}
        self.last_activity = {}
        self.decay_threshold = 86400  # 24 hours in seconds
        self.decay_rate = 0.01  # 1% decay per period
        self.inflation_pool = 0
        
    def register_wallet(self, address, keypair):
        """Register wallet in protocol"""
        self.wallets[address] = keypair
        self.scores[address] = 0
        self.last_activity[address] = time.time()
        
    def calculate_decay(self, address):
        """Calculate decay for stale keys"""
        time_elapsed = time.time() - self.last_activity[address]
        if time_elapsed > self.decay_threshold:
            periods = int(time_elapsed / self.decay_threshold)
            balance = self.get_balance(address)
            decay_amount = balance * (self.decay_rate * periods)
            return min(decay_amount, balance)
        return 0
    
    def apply_decay(self):
        """Apply decay to all stale wallets"""
        for address in list(self.wallets.keys()):
            decay = self.calculate_decay(address)
            if decay > 0:
                self.inflation_pool += decay
                print(f"Decayed {decay} from {address}")
                
    def get_balance(self, address):
        """Get wallet balance from chain"""
        result = self.substrate.query('System', 'Account', [address])
        return result.value['data']['free'] if result else 0
    
    def force_transfer(self, from_addr, to_addr, amount):
        """Force transfer between wallets"""
        keypair = self.wallets.get(from_addr)
        if not keypair:
            raise ValueError(f"Wallet {from_addr} not registered")
            
        call = self.substrate.compose_call(
            call_module='Balances',
            call_function='transfer',
            call_params={
                'dest': to_addr,
                'value': amount
            }
        )
        
        extrinsic = self.substrate.create_signed_extrinsic(
            call=call,
            keypair=keypair
        )
        
        receipt = self.substrate.submit_extrinsic(extrinsic, wait_for_inclusion=True)
        
        if receipt.is_success:
            self.scores[from_addr] = self.scores.get(from_addr, 0) + amount
            self.last_activity[from_addr] = time.time()
            self.last_activity[to_addr] = time.time()
            print(f"Forced transfer: {amount} from {from_addr} to {to_addr}")
            return True
        return False
    
    def distribute_inflation(self):
        """Distribute inflation pool to early keys randomly"""
        if self.inflation_pool == 0 or not self.wallets:
            return
            
        # Sort by registration time (early keys)
        sorted_wallets = sorted(self.wallets.items(), 
                               key=lambda x: self.last_activity.get(x[0], 0))
        
        # Select random early wallet (top 30%)
        early_count = max(1, len(sorted_wallets) // 3)
        winner = random.choice(sorted_wallets[:early_count])[0]
        
        amount = self.inflation_pool
        self.inflation_pool = 0
        
        print(f"Distributing {amount} inflation to early key {winner}")
        # Transfer from treasury/pool to winner
        return winner, amount
    
    def run_protocol_cycle(self):
        """Execute one protocol cycle"""
        # Apply decay
        self.apply_decay()
        
        # Force random transfers
        if len(self.wallets) >= 2:
            wallets_list = list(self.wallets.keys())
            from_addr = random.choice(wallets_list)
            to_addr = random.choice([w for w in wallets_list if w != from_addr])
            
            balance = self.get_balance(from_addr)
            if balance > 0:
                amount = min(balance // 10, balance)  # Transfer 10% or less
                self.force_transfer(from_addr, to_addr, amount)
        
        # Distribute inflation randomly
        if random.random() < 0.3:  # 30% chance per cycle
            self.distribute_inflation()
    
    def get_leaderboard(self):
        """Get wallet scores sorted by transfer amount"""
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)


if __name__ == "__main__":
    protocol = DecayProtocol()
    print("Substrate Decay Protocol initialized")
    print("Features:")
    print("- Forced wallet-to-wallet transfers")
    print("- Decay function for stale keys")
    print("- Inflation redistribution to early keys")
    print("- Score based on transfer amounts")
