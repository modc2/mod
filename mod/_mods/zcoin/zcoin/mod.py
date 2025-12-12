class ZconPrivacyMod:
    description = """
    ZCon Privacy Coin - EVM-based privacy token with ZK proofs
    
    A complete implementation of a privacy-preserving cryptocurrency on EVM
    that supports:
    - Private deposits and withdrawals of any ERC20 token including ETH
    - Zero-knowledge proof verification for transaction privacy
    - Authorized oracle system with ZK proof verification
    - Commitment/nullifier scheme to prevent double-spending
    - Docker containerized deployment with Hardhat
    
    Features:
    - ZconPrivacyToken: Main privacy token contract
    - ZconOracle: Authorized oracle for private data storage
    - Full Docker setup with docker-compose
    - Automated deployment scripts
    - Test suite included
    
    Usage:
    1. Run: docker-compose up --build
    2. Contracts auto-deploy to localhost:8545
    3. Use the provided scripts to interact with contracts
    
    Security: Uses commitment/nullifier cryptographic scheme with
    ZK proof verification (simplified - integrate real ZK library for production)
    """
    
    def __init__(self):
        self.name = "ZCon Privacy Coin"
        self.version = "1.0.0"
        self.contracts = [
            "ZconPrivacyToken.sol",
            "ZconOracle.sol"
        ]
    
    def get_info(self):
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "contracts": self.contracts,
            "features": [
                "Privacy transactions with ZK proofs",
                "Multi-token support (ETH + ERC20)",
                "Authorized oracle system",
                "Docker deployment ready"
            ]
        }
