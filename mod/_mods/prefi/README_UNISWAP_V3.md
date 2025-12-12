# Uniswap V3 Prediction Market - Modular Architecture

## Overview

Super modular prediction market integrated with Uniswap V3 for real-time price feeds and liquidity management. Built with a clean, extensible architecture.

## Architecture

### Core Modules

1. **PriceFeedModule** - Fetch real-time prices from Uniswap V3 pools
2. **LiquidityModule** - Manage liquidity positions (optional)
3. **SwapModule** - Execute token swaps (optional)
4. **PredictionMarket** - Core prediction logic

### Modular Design Benefits

- ✅ **Plug & Play**: Enable/disable modules as needed
- ✅ **Extensible**: Easy to add new modules
- ✅ **Testable**: Each module can be tested independently
- ✅ **Configurable**: Runtime configuration without code changes
- ✅ **Maintainable**: Clean separation of concerns

## Smart Contract Features

### Uniswap V3 Integration

```solidity
// Get price from any Uniswap V3 pool
function getUniswapV3Price(
    address token0,
    address token1,
    uint24 fee
) public view returns (uint256)

// Open position with automatic price feed
function openPosition(
    uint256 amount,
    Direction direction,
    address token0,
    address token1,
    uint24 poolFee
) external returns (bytes32)

// Auto-settle with current Uniswap price
function settlePosition(bytes32 positionId) external
```

### Modular Configuration

```solidity
// Configure reward percentage
function setRewardPoolPercentage(uint256 _percentage) external

// Configure prediction duration (not just 24h!)
function setPredictionDuration(uint256 _duration) external

// Set custom price oracle
function setPriceOracle(address _oracle) external
```

## Python Module Usage

### Basic Setup

```python
from prefi.uniswap_prediction_mod import UniswapV3PredictionMod

# Minimal configuration
mod = UniswapV3PredictionMod({
    'uniswap_factory': '0x1F98431c8aD98523631AE4a59f267346ea31F984',
    'uniswap_router': '0xE592427A0AEce92De3Edee1F18E0157C05861564',
    'uniswap_quoter': '0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6',
    'reward_pool_percentage': 0.95
})
```

### Advanced Configuration

```python
# Enable all modules
mod = UniswapV3PredictionMod({
    'uniswap_factory': '0x1F98431c8aD98523631AE4a59f267346ea31F984',
    'uniswap_router': '0xE592427A0AEce92De3Edee1F18E0157C05861564',
    'uniswap_quoter': '0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6',
    'position_manager': '0xC36442b4a4522E871399CD717aBDD847Ab11FE88',
    'reward_pool_percentage': 0.95,
    'prediction_duration_hours': 48,  # 48-hour predictions!
    'enable_liquidity_module': True,
    'enable_swap_module': True
})
```

### Module Operations

#### Price Feed Module

```python
# Get current price from Uniswap V3
result = mod.execute(
    action='get_price',
    module='price_feed',
    token0='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',  # WETH
    token1='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',  # USDC
    fee=3000  # 0.3% pool
)

# Get pool address
result = mod.execute(
    action='get_pool',
    module='price_feed',
    token0='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    token1='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    fee=3000
)
```

#### Prediction Market Module

```python
# Deposit funds
mod.execute(
    action='deposit',
    module='market',
    user_address='0x123...',
    amount=1.0
)

# Open position (auto-fetches price from Uniswap)
result = mod.execute(
    action='open_position',
    module='market',
    user_address='0x123...',
    amount=0.5,
    direction='up',
    token0='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',  # WETH
    token1='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',  # USDC
    fee=3000
)

position_id = result['position_id']

# Settle position (auto-fetches exit price)
mod.execute(
    action='settle_position',
    module='market',
    position_id=position_id,
    token0='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    token1='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    fee=3000
)

# Distribute rewards
mod.execute(
    action='distribute_rewards',
    module='market',
    asset='WETH/USDC',
    settlement_time='2024-01-01T00:00:00'
)
```

#### Liquidity Module (Optional)

```python
# Add liquidity to Uniswap V3
result = mod.execute(
    action='add_liquidity',
    module='liquidity',
    token0='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    token1='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    amount0=1.0,
    amount1=2000.0,
    fee=3000,
    tick_lower=-887220,
    tick_upper=887220
)

# Remove liquidity
mod.execute(
    action='remove_liquidity',
    module='liquidity',
    position_id='12345',
    liquidity=1000.0
)
```

#### Swap Module (Optional)

```python
# Execute swap
result = mod.execute(
    action='swap',
    module='swap',
    token_in='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    token_out='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    amount_in=1.0,
    fee=3000,
    slippage=0.01
)

# Get quote
result = mod.execute(
    action='quote',
    module='swap',
    token_in='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    token_out='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    amount_in=1.0,
    fee=3000
)
```

## Deployment

### Uniswap V3 Contract Addresses

#### Ethereum Mainnet
```
Factory: 0x1F98431c8aD98523631AE4a59f267346ea31F984
Router: 0xE592427A0AEce92De3Edee1F18E0157C05861564
Quoter: 0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6
Position Manager: 0xC36442b4a4522E871399CD717aBDD847Ab11FE88
```

#### Base Mainnet
```
Factory: 0x33128a8fC17869897dcE68Ed026d694621f6FDfD
Router: 0x2626664c2603336E57B271c5C0b26F421741e481
Quoter: 0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a
```

### Deploy Contract

```bash
# Install dependencies
npm install @uniswap/v3-core @uniswap/v3-periphery

# Deploy to Base
node deploy_uniswap_v3.js --network base
```

## Testing

### Unit Tests

```bash
# Test individual modules
python -m pytest tests/test_price_feed_module.py
python -m pytest tests/test_liquidity_module.py
python -m pytest tests/test_swap_module.py
```

### Integration Tests

```bash
# Test full flow
python -m pytest tests/test_uniswap_integration.py
```

## Extending the System

### Add Custom Module

```python
from prefi.uniswap_v3_module import UniswapV3Module

class CustomModule(UniswapV3Module):
    """Your custom module."""
    
    def get_pool_price(self, token0: str, token1: str, fee: int) -> float:
        # Your implementation
        pass
    
    def get_pool_address(self, token0: str, token1: str, fee: int) -> str:
        # Your implementation
        pass
    
    def custom_function(self, **kwargs):
        # Your custom logic
        pass
```

### Integrate Custom Module

```python
class ExtendedPredictionMod(UniswapV3PredictionMod):
    def __init__(self, config):
        super().__init__(config)
        self.custom_module = CustomModule(
            config['factory'],
            config['router'],
            config['quoter']
        )
    
    def execute(self, **kwargs):
        if kwargs.get('module') == 'custom':
            return self._execute_custom(**kwargs)
        return super().execute(**kwargs)
```

## Best Practices

1. **Module Selection**: Only enable modules you need
2. **Price Caching**: Use price cache for frequent reads
3. **Error Handling**: Always check `success` in results
4. **Gas Optimization**: Batch operations when possible
5. **Testing**: Test each module independently

## Security

- ✅ Modular design reduces attack surface
- ✅ Each module can be audited separately
- ✅ Price feeds from trusted Uniswap V3 pools
- ✅ Configurable parameters for flexibility
- ⚠️ Always audit before production use

## License

MIT License

---

**Built with modularity and extensibility in mind** 🚀