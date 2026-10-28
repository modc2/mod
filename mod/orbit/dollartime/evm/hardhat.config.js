const path = require('path');
require('@nomicfoundation/hardhat-toolbox');
require('dotenv').config({ path: path.resolve(__dirname, '.env') });

// Same stack as orbit/staketime (node_modules is a symlink to it).
module.exports = {
  solidity: { version: '0.8.20', settings: { optimizer: { enabled: true, runs: 200 } } },
  networks: {
    base_sepolia: {
      url: process.env.BASE_TESTNET_RPC_URL || 'https://sepolia.base.org',
      accounts: process.env.PRIVATE_KEY ? [process.env.PRIVATE_KEY] : [],
      chainId: 84532,
    },
  },
  paths: { sources: './contracts', tests: './test', cache: './cache', artifacts: './artifacts' },
};
