
import requests

UNISWAP_V3_SUBGRAPH = "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"

POOL_ORDER_BY_VALUES = frozenset({
    "totalValueLockedUSD",
    "volumeUSD",
    "feeTier",
    "txCount",
    "createdAtTimestamp",
    "liquidityProviderCount",
})

_DEFAULT_QUERY = """
query TopPools($first: Int!, $orderBy: Pool_orderBy!, $orderDirection: OrderDirection!) {{
  pools(first: $first, orderBy: $orderBy, orderDirection: $orderDirection) {{
    token0 {{ symbol }}
    token1 {{ symbol }}
    feeTier
    totalValueLockedUSD
  }}
}}
"""


class Mod:
    description = "Uniswap v3 GraphQL scraper — fetches pool data from The Graph subgraph"

    def forward(self, query: str = None, first: int = 10, order_by: str = "totalValueLockedUSD", order_direction: str = "desc", subgraph_url: str = None) -> dict:
        """Query the Uniswap v3 subgraph and return the parsed data dict.

        Args:
            query: Raw GraphQL query string override. If omitted, fetches top pools by TVL.
            first: Number of results to fetch (default 10). Ignored when query is provided.
            order_by: Field to order pools by (default totalValueLockedUSD). Ignored when query is provided.
            order_direction: Sort direction, "asc" or "desc" (default "desc"). Ignored when query is provided.
            subgraph_url: URL of a compatible Graph subgraph endpoint; defaults to Uniswap v3 mainnet.

        Returns:
            The parsed JSON 'data' dict from the subgraph response.
        """
        url = subgraph_url or UNISWAP_V3_SUBGRAPH
        if query is None and order_by not in POOL_ORDER_BY_VALUES:
            raise ValueError(f"order_by must be one of {POOL_ORDER_BY_VALUES!r}, got {order_by!r}")
        if query is None and order_direction not in {"asc", "desc"}:
            raise ValueError(f"order_direction must be 'asc' or 'desc', got {order_direction!r}")
        if query is not None:
            payload = {"query": query}
        else:
            payload = {
                "query": _DEFAULT_QUERY.format(),
                "variables": {"first": first, "orderBy": order_by, "orderDirection": order_direction},
            }

        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        if "errors" in result:
            raise RuntimeError(f"Subgraph errors: {result['errors']}")
        return result.get("data", result)
