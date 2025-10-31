import requests
from typing import List, Dict, Any, Optional
import json
import mod as m

class RaydiumAPI:
    """Simple Raydium API client to fetch all pairs and their info"""
    
    BASE_URL = "https://api.raydium.io/v2"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json'
        })
    
    def get_all_pairs(self) -> List[Dict[str, Any]]:
        """Fetch all trading pairs from Raydium"""
        path = 'all_pairs'
        pairs = m.get(path, [])

        if len(pairs) > 0:
            print("Fetched all pairs successfully.")
            return pairs

        try:
            print("Fetching all pairs from Raydium API...")
            response = self.session.get(f"{self.BASE_URL}/main/pairs")
            response.raise_for_status()
            print("Fetched all pairs successfully.")
            pairs =  response.json()
            m.put(path, pairs)
            return pairs

        except Exception as e:
            print(f"Error fetching pairs: {e}")
            return []
    
    def get_pair_info(self, pair_address: str) -> Optional[Dict[str, Any]]:
        """Get detailed info for a specific pair"""
        try:
            response = self.session.get(f"{self.BASE_URL}/main/pair/{pair_address}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching pair info for {pair_address}: {e}")
            return None
    
    def find_pairs_by_token(self, token_symbol: str) -> List[Dict[str, Any]]:
        """Find all pairs containing a specific token"""
        all_pairs = self.get_all_pairs()
        return [
            pair for pair in all_pairs 
            if token_symbol.upper() in pair.get('name', '').upper()
        ]
    
    def get_pair_summary(self, pair: Dict[str, Any]) -> Dict[str, Any]:
        """Extract key info from a pair"""
        return {
            'name': pair.get('name', 'N/A'),
            'address': pair.get('ammId', 'N/A'),
            'liquidity': pair.get('liquidity', 0),
            'volume_24h': pair.get('volume24h', 0),
            'price': pair.get('price', 0),
            'base_mint': pair.get('baseMint', 'N/A'),
            'quote_mint': pair.get('quoteMint', 'N/A')
        }
    
    def display_all_pairs(self, limit: Optional[int] = None):
        """Display all pairs with their key info"""
        pairs = self.get_all_pairs()
        
        if limit:
            pairs = pairs[:limit]
        
        print(f"\nFound {len(pairs)} pairs:\n")
        for i, pair in enumerate(pairs, 1):
            summary = self.get_pair_summary(pair)
            print(f"{i}. {summary['name']}")
            print(f"   Address: {summary['address']}")
            print(f"   Liquidity: ${summary['liquidity']:,.2f}")
            print(f"   24h Volume: ${summary['volume_24h']:,.2f}")
            print(f"   Price: ${summary['price']}")
            print()

# Usage example
if __name__ == "__main__":
    raydium = RaydiumAPI()
    
    # Get all pairs
    all_pairs = raydium.get_all_pairs()
    print(f"Total pairs: {len(all_pairs)}")
    
    # Display first 10 pairs
    raydium.display_all_pairs(limit=10)
    
    # Find specific token pairs
    sol_pairs = raydium.find_pairs_by_token('SOL')
    print(f"\nSOL pairs found: {len(sol_pairs)}")
