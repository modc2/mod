import os
import re
import json
import urllib.request
import urllib.error
import urllib.parse
from decimal import Decimal
import mod as m

NETWORKS = {
    "sepolia":  "https://eth-sepolia.blockscout.com",
    "holesky":  "https://eth-holesky.blockscout.com",
    "amoy":     "https://polygon-amoy.blockscout.com",
    "base-sepolia": "https://base-sepolia.blockscout.com",
}

class Mod:
    description = "Scrape ERC-20 token balances for an address on an EVM testnet (Sepolia by default)"
    path = r'/root/mod/mod/orbit/scrape-testnet-tokens'

    def forward(self, **kwargs):
        """Dispatch to scrape() when address is provided, else info()."""
        address = kwargs.get("address")
        if address:
            network = kwargs.get("network", "sepolia")
            return self.scrape(address, network)
        return self.info()

    def scrape(self, address: str, network: str = "sepolia"):
        """Return ERC-20 token balances for address on the given testnet."""
        if not re.fullmatch(r'0x[0-9a-fA-F]{40}', address):
            return {"error": f"Invalid address '{address}': must be a 0x-prefixed 40-hex-character Ethereum address"}

        base = NETWORKS.get(network.lower())
        if base is None:
            return {"error": f"Unknown network '{network}'. Supported: {list(NETWORKS)}"}

        base_url = f"{base}/api/v2/addresses/{address}/tokens?type=ERC-20"
        tokens = []
        next_params = None
        max_pages = 10

        for page_num in range(max_pages):
            if next_params:
                url = base_url + "&" + urllib.parse.urlencode(next_params)
            else:
                url = base_url
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return {"error": f"HTTP {e.code}: {e.reason}", "url": url}
            except Exception as e:
                return {"error": str(e), "url": url}

            for item in data.get("items", []):
                token = item.get("token", {})
                raw_value = item.get("value", "0")
                decimals = int(token.get("decimals") or 0)
                try:
                    balance = format(Decimal(raw_value) / Decimal(10 ** decimals), 'f')
                except (ValueError, TypeError):
                    balance = raw_value
                tokens.append({
                    "symbol":           token.get("symbol", ""),
                    "name":             token.get("name", ""),
                    "balance":          balance,
                    "contract_address": token.get("address", ""),
                })

            next_params = data.get("next_page_params") or None
            if not next_params:
                break

        truncated = next_params is not None
        return {"network": network, "address": address, "tokens": tokens, "truncated": truncated, "pages_fetched": page_num + 1}

    def info(self):
        """Return module info."""
        return {
            'name': 'scrape-testnet-tokens',
            'description': self.description,
            'supported_networks': list(NETWORKS.keys()),
            'usage': 'Call with address=<0x…> and optionally network=<name> (default: sepolia)',
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
