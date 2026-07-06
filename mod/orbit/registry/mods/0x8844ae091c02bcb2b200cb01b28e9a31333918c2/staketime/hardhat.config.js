const path = require('path');

require('@nomicfoundation/hardhat-toolbox');
require('dotenv').config({ path: path.resolve(__dirname, '.env') });

module.exports = {
  solidity: {
    version: '0.8.20',
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
  networks: {
    ganache: {
      url: process.env.GANACHE_URL || 'http://localhost:8545',
      accounts: {
        mnemonic: process.env.MNEMONIC || 'test test test test test test test test test test test junk',
      },
      chainId: 1337,
    },
    base_sepolia: {
      url: process.env.BASE_TESTNET_RPC_URL || 'https://sepolia.base.org',
      accounts: process.env.PRIVATE_KEY ? [process.env.PRIVATE_KEY] : [],
      chainId: 84532,
    },
  },
  paths: {
    sources: './src/contracts',
    tests: './test',
    cache: './cache',
    artifacts: './artifacts',
  },
};
