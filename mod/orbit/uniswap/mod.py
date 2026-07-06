import os
import importlib.util

# Import the actual module class from src/mod.py (can't use "from mod import" — name collision)
DIR = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('uniswap_src', os.path.join(DIR, 'src', 'mod.py'))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
Uniswap = _mod.Uniswap

class Mod(Uniswap):
    """Uniswap V3 multi-chain trader scraper.

    Discovers and ranks traders across Ethereum, Arbitrum, Base, Polygon, Optimism.
    Rust backend (Axum) + Next.js frontend with NDJSON streaming progress.

    Usage:
        m uniswap/serve          # Start API + app
        m uniswap/traders        # Get top traders (base, 30d)
        m uniswap/trader 0x...   # Get trader profile
        m uniswap/scrape         # Trigger fresh scrape
        m uniswap/kill           # Stop services
    """
    pass
