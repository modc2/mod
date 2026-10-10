# proxyevm

Fetches Uniswap v3 pool data from The Graph's public subgraph endpoint via GraphQL.

## Structure

```
proxyevm/
├── proxyevm/
│   └── mod.py    # Mod class — GraphQL client for Uniswap v3
└── README.md
```

## Usage

```python
import mod as m

# Fetch the top 10 pools by TVL (default)
result = m.mod('proxyevm')().forward()

# Fetch the top 5 pools by TVL
result = m.mod('proxyevm')().forward(first=5)

# Use a custom subgraph URL (any Graph-compatible endpoint)
result = m.mod('proxyevm')().forward(
    first=5,
    subgraph_url="https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"
)

# Pass a raw GraphQL query string
result = m.mod('proxyevm')().forward(
    query="{ pools(first: 3) { token0 { symbol } token1 { symbol } feeTier } }"
)
```

```bash
# CLI — fetch top 5 pools
m proxyevm forward first=5
```

## Parameters

`forward(query, first, order_by, order_direction, subgraph_url)`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | `None` | Raw GraphQL query string. When provided, all other parameters are ignored. |
| `first` | `int` | `10` | Number of pools to return. |
| `order_by` | `str` | `"totalValueLockedUSD"` | Field to sort pools by. |
| `order_direction` | `str` | `"desc"` | Sort direction: `"asc"` or `"desc"`. |
| `subgraph_url` | `str` | `None` | Override the subgraph endpoint. Defaults to the Uniswap v3 mainnet subgraph on The Graph. |

## Returns

The parsed JSON `data` dict from the subgraph response. Each pool entry includes `id`, `token0`, `token1`, `feeTier`, `totalValueLockedUSD`, `volumeUSD`, and `txCount`.
