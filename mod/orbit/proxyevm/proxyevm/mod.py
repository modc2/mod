
import requests

UNISWAP_V3_SUBGRAPH = "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"

_DEFAULT_QUERY = """
query TopPools($first: Int!, $orderBy: Pool_orderBy!) {{
  pools(first: $first, orderBy: $orderBy, orderDirection: desc) {{
    token0 {{ symbol }}
    token1 {{ symbol }}
    feeTier
    totalValueLockedUSD
  }}
}}
"""


class Mod:
    description = "Uniswap v3 GraphQL scraper — fetches pool data from The Graph subgraph"

    def forward(self, query: str = None, first: int = 10, order_by: str = "totalValueLockedUSD", subgraph_url: str = None) -> dict:
        """Query the Uniswap v3 subgraph and return the parsed data dict.

        Args:
            query: Raw GraphQL query string override. If omitted, fetches top pools by TVL.
            first: Number of results to fetch (default 10). Ignored when query is provided.
            order_by: Field to order pools by (default totalValueLockedUSD). Ignored when query is provided.
            subgraph_url: URL of a compatible Graph subgraph endpoint; defaults to Uniswap v3 mainnet.

        Returns:
            The parsed JSON 'data' dict from the subgraph response.
        """
        url = subgraph_url or UNISWAP_V3_SUBGRAPH
        if query is not None:
            payload = {"query": query}
        else:
            payload = {
                "query": _DEFAULT_QUERY.format(),
                "variables": {"first": first, "orderBy": order_by},
            }

        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        if "errors" in result:
            raise RuntimeError(f"Subgraph errors: {result['errors']}")
        return result.get("data", result)
