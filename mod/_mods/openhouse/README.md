# OpenHouse - Collective Asset Ownership Platform

## Overview

OpenHouse is a blockchain-based smart contract system that enables collective ownership of real-world assets (primarily real estate) through tokenized shares. The platform allows multiple individuals to pool their resources and collectively own property, with transparent governance and automated dividend distribution.

## Features

- **Fractional Ownership**: Purchase shares in real estate assets at affordable price points
- **Transparent Management**: All transactions and management actions recorded on-chain
- **Automated Dividends**: Rental income and profits distributed proportionally to shareholders
- **Legal Compliance**: Managed by registered legal entities (trusts/companies) with fiduciary responsibility
- **Secure Transfers**: Authority can be transferred to new legal entities when needed

## Smart Contract Architecture

### Core Components

1. **Authority Management**: Legal entity oversight with fiduciary duties
2. **Share Distribution**: Tokenized ownership with transparent allocation
3. **Dividend System**: Automated proportional profit distribution
4. **Governance**: On-chain recording of management decisions

### Key Functions

- `purchaseShares()`: Buy ownership shares in the property
- `distributeDividends()`: Distribute rental income to shareholders
- `recordManagementAction()`: Log property management activities
- `transferAuthority()`: Transfer legal entity control
- `getShareholderInfo()`: View ownership details and percentages

## Installation

### Prerequisites

- Solidity ^0.8.0
- Ethereum development environment (Hardhat/Truffle)
- Web3 wallet (MetaMask recommended)

### Deployment

```bash
# Clone the repository
git clone <repository-url>
cd openhouse

# Install dependencies
npm install

# Compile contracts
npx hardhat compile

# Deploy to network
npx hardhat run scripts/deploy.js --network <network-name>
```

## Usage Example

```javascript
// Deploy contract
const OpenHouse = await ethers.getContractFactory("OpenHouse");
const openhouse = await OpenHouse.deploy(
    authorityAddress,
    "123 Main St, City, State - 3BR/2BA",
    1000, // Total shares
    ethers.utils.parseEther("0.1") // Price per share
);

// Purchase shares
await openhouse.purchaseShares(10, { 
    value: ethers.utils.parseEther("1.0") 
});

// Distribute dividends (authority only)
await openhouse.distributeDividends({ 
    value: ethers.utils.parseEther("5.0") 
});
```

## Security Considerations

- Authority address must be a verified legal entity
- All financial transactions are transparent and auditable
- Excess payments are automatically refunded
- Contract can be paused by authority in emergencies

## Legal Framework

- Managed by registered trust or company
- Compliant with local real estate and securities regulations
- Shareholders have proportional ownership rights
- Legal entity has fiduciary responsibility to shareholders

## Roadmap

- [ ] Multi-property support
- [ ] Secondary market for share trading
- [ ] Governance voting system
- [ ] Integration with property management platforms
- [ ] Mobile application interface

## Contributing

Contributions are welcome! Please submit pull requests or open issues for bugs and feature requests.

## License

MIT License - See LICENSE file for details

## Contact

For questions or partnership inquiries, please contact the development team.

---

**Disclaimer**: This smart contract system is provided as-is. Users should consult legal and financial advisors before participating in collective asset ownership arrangements.