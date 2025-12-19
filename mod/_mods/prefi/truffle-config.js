module.exports = {
  networks: {
    // Ganache local development
    development: {
      host: "127.0.0.1",
      port: 8545,
      network_id: "*",
      gas: 6721975,
      gasPrice: 20000000000
    },
    
    // Ganache CLI
    ganache: {
      host: "127.0.0.1",
      port: 7545,
      network_id: "5777",
      gas: 6721975
    },
    
    // Base Mainnet
    base: {
      provider: () => new HDWalletProvider(
        process.env.MNEMONIC,
        `https://mainnet.base.org`
      ),
      network_id: 8453,
      gas: 5000000,
      gasPrice: 1000000000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: true
    },
    
    // Base Goerli Testnet
    baseGoerli: {
      provider: () => new HDWalletProvider(
        process.env.MNEMONIC,
        `https://goerli.base.org`
      ),
      network_id: 84531,
      gas: 5000000,
      gasPrice: 1000000000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: true
    },
    
    // Ethereum Mainnet
    mainnet: {
      provider: () => new HDWalletProvider(
        process.env.MNEMONIC,
        `https://mainnet.infura.io/v3/${process.env.INFURA_KEY}`
      ),
      network_id: 1,
      gas: 5500000,
      gasPrice: 20000000000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: false
    },
    
    // Ethereum Goerli Testnet
    goerli: {
      provider: () => new HDWalletProvider(
        process.env.MNEMONIC,
        `https://goerli.infura.io/v3/${process.env.INFURA_KEY}`
      ),
      network_id: 5,
      gas: 5500000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: true
    },
    
    // Ethereum Sepolia Testnet
    sepolia: {
      provider: () => new HDWalletProvider(
        process.env.MNEMONIC,
        `https://sepolia.infura.io/v3/${process.env.INFURA_KEY}`
      ),
      network_id: 11155111,
      gas: 5500000,
      confirmations: 2,
      timeoutBlocks: 200,
      skipDryRun: true
    }
  },
  
  compilers: {
    solc: {
      version: "0.8.19",
      settings: {
        optimizer: {
          enabled: true,
          runs: 200
        },
        evmVersion: "paris"
      }
    }
  },
  
  plugins: [
    'truffle-plugin-verify'
  ],
  
  api_keys: {
    etherscan: process.env.ETHERSCAN_API_KEY,
    basescan: process.env.BASESCAN_API_KEY
  }
};

const HDWalletProvider = require('@truffle/hdwallet-provider');
