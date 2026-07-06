# Oracle System Architecture 🔮

## Overview

The Prefi oracle system provides robust, decentralized price feeds through a modular adapter architecture. Multiple oracle sources are aggregated with weighted averaging and confidence scoring to ensure reliable price data.

## Architecture

```
PriceOracle (Main Aggregator)
    ├── ChainlinkAdapter
    ├── PythAdapter
    ├── UniswapAdapter
    ├── BinanceAdapter
    ├── CoinGeckoAdapter
    └── CoinMarketCapAdapter
```

## Core Components

### 1. IPriceOracleAdapter (Interface)

Standardized interface all adapters must implement:

```solidity
interface IPriceOracleAdapter {
    function getPrice(address _asset) external view returns (uint256 price);
    function supportsAsset(address _asset) external view returns (bool);
    function adapterName() external view returns (string memory);
}
```

### 2. PriceOracle (Aggregator)

Main oracle contract that:
- Manages multiple adapters
- Aggregates prices using weighted average
- Calculates confidence scores
- Enforces price staleness checks
- Provides fallback mechanisms

**Key Features**:
- ✅ Weighted average from all active adapters
- ✅ Confidence scoring based on price variance
- ✅ Automatic outlier detection
- ✅ Price staleness protection (1 hour default)
- ✅ Minimum adapter requirements (2+ sources)

### 3. Oracle Adapters

#### ChainlinkAdapter
**Type**: On-chain decentralized oracle
**Strengths**: High reliability, wide coverage, battle-tested
**Update Frequency**: Variable (typically 0.5-1% deviation or 1 hour)

```solidity
// Add Chainlink feed
chainlinkAdapter.addAsset(WBTC_ADDRESS, CHAINLINK_BTC_USD_FEED);
```

#### PythAdapter
**Type**: High-frequency oracle network
**Strengths**: Low latency, cross-chain, confidence intervals
**Update Frequency**: Sub-second updates

```solidity
// Add Pyth price ID
pythAdapter.addAsset(WBTC_ADDRESS, PYTH_BTC_USD_ID);
```

#### UniswapAdapter
**Type**: DEX-based TWAP oracle
**Strengths**: On-chain liquidity, manipulation resistant
**Update Frequency**: Real-time (TWAP smoothing)

```solidity
// Add Uniswap V3 pool
uniswapAdapter.addAsset(WBTC_ADDRESS, WBTC_WETH_POOL);
```

#### BinanceAdapter
**Type**: CEX price feed (off-chain updates)
**Strengths**: High liquidity reference, batch updates
**Update Frequency**: Configurable (typically 1-5 minutes)

```solidity
// Add Binance symbol
binanceAdapter.addAsset(WBTC_ADDRESS, "BTCUSDT");

// Authorized updater pushes prices
binanceAdapter.updatePrice(WBTC_ADDRESS, price);
```

#### CoinGeckoAdapter
**Type**: Aggregated market data (off-chain)
**Strengths**: Wide token coverage, historical data
**Update Frequency**: Configurable (typically 5-10 minutes)

```solidity
// Add CoinGecko ID
coinGeckoAdapter.addAsset(WBTC_ADDRESS, "bitcoin");
```

#### CoinMarketCapAdapter
**Type**: Market cap weighted prices (off-chain)
**Strengths**: Comprehensive coverage, trusted source
**Update Frequency**: Configurable (typically 5-10 minutes)

```solidity
// Add CMC ID
coinMarketCapAdapter.addAsset(WBTC_ADDRESS, 1); // Bitcoin CMC ID
```

## Price Aggregation

### Weighted Average Algorithm

```solidity
function _aggregatePrice(address _asset) internal {
    uint256 weightedSum = 0;
    uint256 totalWeight = 0;
    uint256 validOracleCount = 0;
    
    // Collect valid prices from all adapters
    for (uint256 i = 0; i < adapters.length; i++) {
        if (isValidPrice(_asset, adapters[i])) {
            uint256 price = adapterPrices[_asset][adapters[i]].price;
            uint256 weight = adapterWeights[adapters[i]];
            
            weightedSum += price * weight;
            totalWeight += weight;
            validOracleCount++;
        }
    }
    
    require(validOracleCount >= MIN_ADAPTERS, "Insufficient oracles");
    
    uint256 aggregatedPrice = weightedSum / totalWeight;
    uint256 confidence = _calculateConfidence(validPrices, aggregatedPrice);
    
    prices[_asset] = PriceData({
        price: aggregatedPrice,
        timestamp: block.timestamp,
        confidence: confidence
    });
}
```

### Confidence Scoring

Confidence based on maximum deviation from average:

```solidity
function _calculateConfidence(
    uint256[] memory _prices,
    uint256 _avgPrice
) internal pure returns (uint256) {
    uint256 maxDeviation = 0;
    
    for (uint256 i = 0; i < _prices.length; i++) {
        uint256 deviation = abs(_prices[i] - _avgPrice) * 10000 / _avgPrice;
        if (deviation > maxDeviation) {
            maxDeviation = deviation;
        }
    }
    
    // Confidence = 100% - max deviation
    return maxDeviation >= 10000 ? 0 : 10000 - maxDeviation;
}
```

**Example**:
- Prices: [$50,000, $50,100, $49,900]
- Average: $50,000
- Max Deviation: 0.2% (200 basis points)
- Confidence: 98% (9800 basis points)

## Deployment Guide

### 1. Deploy Main Oracle

```javascript
const PriceOracle = await ethers.getContractFactory("PriceOracle");
const oracle = await PriceOracle.deploy();
await oracle.deployed();
```

### 2. Deploy Adapters

```javascript
// Chainlink
const ChainlinkAdapter = await ethers.getContractFactory("ChainlinkAdapter");
const chainlink = await ChainlinkAdapter.deploy(oracle.address);

// Pyth
const PythAdapter = await ethers.getContractFactory("PythAdapter");
const pyth = await PythAdapter.deploy(oracle.address, PYTH_ADDRESS);

// Uniswap
const UniswapAdapter = await ethers.getContractFactory("UniswapAdapter");
const uniswap = await UniswapAdapter.deploy(oracle.address);

// Binance (requires authorized updater)
const BinanceAdapter = await ethers.getContractFactory("BinanceAdapter");
const binance = await BinanceAdapter.deploy(oracle.address, UPDATER_ADDRESS);
```

### 3. Register Adapters

```javascript
await oracle.addAdapter(chainlink.address);
await oracle.addAdapter(pyth.address);
await oracle.addAdapter(uniswap.address);
await oracle.addAdapter(binance.address);
```

### 4. Configure Assets

```javascript
// Chainlink
await chainlink.addAsset(WBTC_ADDRESS, CHAINLINK_BTC_FEED);

// Pyth
await pyth.addAsset(WBTC_ADDRESS, PYTH_BTC_ID);

// Uniswap
await uniswap.addAsset(WBTC_ADDRESS, WBTC_WETH_POOL);

// Binance
await binance.addAsset(WBTC_ADDRESS, "BTCUSDT");
```

## Usage Examples

### Get Aggregated Price

```javascript
const price = await oracle.getPrice(WBTC_ADDRESS);
console.log(`BTC Price: $${ethers.utils.formatUnits(price, 18)}`);
```

### Get Price with Metadata

```javascript
const { price, timestamp, confidence, oracleCount } = 
    await oracle.getPriceWithMetadata(WBTC_ADDRESS);

console.log(`Price: $${ethers.utils.formatUnits(price, 18)}`);
console.log(`Confidence: ${confidence / 100}%`);
console.log(`Active Oracles: ${oracleCount}`);
```

### Get Individual Oracle Prices

```javascript
const { price: chainlinkPrice } = 
    await oracle.getOraclePrice(WBTC_ADDRESS, chainlink.address);

const { price: pythPrice } = 
    await oracle.getOraclePrice(WBTC_ADDRESS, pyth.address);
```

## Security Considerations

### Price Staleness

```solidity
uint256 public constant MAX_PRICE_AGE = 1 hours;

require(
    block.timestamp - data.timestamp < MAX_PRICE_AGE,
    "Price too stale"
);
```

### Minimum Oracles

```solidity
uint256 public constant MIN_ORACLES_REQUIRED = 2;

require(
    validOracleCount >= MIN_ORACLES_REQUIRED,
    "Insufficient oracle data"
);
```

### Confidence Threshold

```solidity
uint256 public constant CONFIDENCE_THRESHOLD = 9500; // 95%

require(
    data.confidence >= CONFIDENCE_THRESHOLD,
    "Low confidence price"
);
```

### Outlier Detection

Prices deviating >10% from average are flagged in confidence score.

## Best Practices

1. **Use 3+ Adapters**: Redundancy ensures reliability
2. **Mix Oracle Types**: Combine on-chain (Chainlink, Uniswap) with off-chain (Binance, CoinGecko)
3. **Monitor Confidence**: Alert on low confidence scores
4. **Regular Updates**: Ensure off-chain adapters update frequently
5. **Fallback Mechanisms**: Have backup oracles ready
6. **Test Thoroughly**: Simulate oracle failures

## Monitoring

### Events to Track

```solidity
event PriceUpdated(address indexed asset, address indexed adapter, uint256 price, uint256 timestamp);
event AggregatedPriceUpdated(address indexed asset, uint256 price, uint256 confidence, uint256 timestamp);
event AdapterAdded(address indexed adapter, string name);
event AdapterRemoved(address indexed adapter);
```

### Health Checks

```javascript
// Check all adapters are updating
const adapters = await oracle.getApprovedOracles();
for (const adapter of adapters) {
    const { timestamp } = await oracle.getOraclePrice(WBTC_ADDRESS, adapter);
    const age = Date.now() / 1000 - timestamp;
    if (age > 3600) {
        console.warn(`Adapter ${adapter} price is stale (${age}s old)`);
    }
}
```

## Troubleshooting

### "Price not available"
- Ensure adapters have been configured for the asset
- Check that at least one adapter has updated recently

### "Price too stale"
- Verify adapters are actively updating
- Check off-chain updater services are running

### "Low confidence price"
- Investigate price discrepancies between oracles
- May indicate market volatility or oracle issues

### "Insufficient oracle data"
- Ensure minimum number of adapters are active
- Check adapter health and connectivity

## License

MIT License

---

**Robust, decentralized, and reliable price feeds for DeFi** 🔮