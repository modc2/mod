const PriceOracle = artifacts.require("PriceOracle");
const PredictionMarket = artifacts.require("PredictionMarket");

module.exports = async function(deployer, network, accounts) {
  // Deploy adapters (mock for now)
  const coinGeckoAdapter = accounts[1];
  const coinMarketCapAdapter = accounts[2];
  
  // Deploy PriceOracle
  await deployer.deploy(PriceOracle, coinGeckoAdapter, coinMarketCapAdapter);
  const oracle = await PriceOracle.deployed();
  
  console.log("PriceOracle deployed at:", oracle.address);
  
  // Deploy PredictionMarket
  await deployer.deploy(PredictionMarket, oracle.address);
  const market = await PredictionMarket.deployed();
  
  console.log("PredictionMarket deployed at:", market.address);
  
  // Setup initial configuration
  if (network === "development" || network === "ganache") {
    console.log("Setting up development environment...");
    
    // Approve some test tokens (you'll need to deploy ERC20 tokens first)
    // await market.approveCollateral(testTokenAddress);
  }
};
