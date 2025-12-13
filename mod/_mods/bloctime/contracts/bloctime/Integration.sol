// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "./Marketplace.sol";
import "./Registry.sol";
import "./Staking.sol";

/**
 * @title BlocTimeIntegration
 * @dev Integration contract ensuring all components work together robustly
 * Validates cross-contract interactions and provides unified interface
 */
contract BlocTimeIntegration {
    BlocTimeMarketplaceV3 public marketplace;
    BlocTimeRegistry public registry;
    BlocTimeStaking public staking;
    BlocTimeToken public blocTimeToken;
    IERC20 public paymentToken;
    
    event SystemInitialized(address marketplace, address registry, address staking);
    event SystemHealthCheck(bool marketplaceOk, bool registryOk, bool stakingOk);
    
    constructor(
        address _marketplace,
        address _registry,
        address _staking
    ) {
        require(_marketplace != address(0), "Invalid marketplace");
        require(_registry != address(0), "Invalid registry");
        require(_staking != address(0), "Invalid staking");
        
        marketplace = BlocTimeMarketplaceV3(_marketplace);
        registry = BlocTimeRegistry(_registry);
        staking = BlocTimeStaking(_staking);
        blocTimeToken = staking.blocTimeToken();
        paymentToken = marketplace.paymentToken();
        
        // Validate integration
        require(address(marketplace.registry()) == _registry, "Marketplace registry mismatch");
        require(address(marketplace.staking()) == _staking, "Marketplace staking mismatch");
        
        emit SystemInitialized(_marketplace, _registry, _staking);
    }
    
    /**
     * @dev Comprehensive health check of all system components
     */
    function healthCheck() external view returns (
        bool marketplaceHealthy,
        bool registryHealthy,
        bool stakingHealthy,
        string memory status
    ) {
        // Check marketplace
        marketplaceHealthy = address(marketplace.paymentToken()) != address(0) &&
                            address(marketplace.staking()) != address(0) &&
                            address(marketplace.registry()) != address(0);
        
        // Check registry
        registryHealthy = registry.nextModuleId() > 0;
        
        // Check staking
        stakingHealthy = address(staking.nativeToken()) != address(0) &&
                        address(staking.blocTimeToken()) != address(0) &&
                        staking.maxLockBlocks() > 0;
        
        if (marketplaceHealthy && registryHealthy && stakingHealthy) {
            status = "All systems operational";
        } else {
            status = "System degraded";
        }
        
        return (marketplaceHealthy, registryHealthy, stakingHealthy, status);
    }
    
    /**
     * @dev Validate module registration flow
     */
    function validateModuleRegistration(
        uint256 moduleId
    ) external view returns (bool valid, string memory reason) {
        (address owner, uint256 price, uint256 maxUsers, uint256 currentUsers, bool active,) = 
            registry.getModule(moduleId);
        
        if (owner == address(0)) {
            return (false, "Module does not exist");
        }
        if (!active) {
            return (false, "Module not active");
        }
        if (price == 0) {
            return (false, "Invalid price");
        }
        if (maxUsers == 0) {
            return (false, "Invalid max users");
        }
        if (currentUsers > maxUsers) {
            return (false, "User count inconsistent");
        }
        
        return (true, "Module valid");
    }
    
    /**
     * @dev Validate rental flow end-to-end
     */
    function validateRentalFlow(
        uint256 rentalId
    ) external view returns (bool valid, string memory reason) {
        (address renter, uint256 moduleId, uint256 startBlock, uint256 paidBlocks, bool active) = 
            marketplace.getRental(rentalId);
        
        if (renter == address(0)) {
            return (false, "Rental does not exist");
        }
        if (!active) {
            return (false, "Rental not active");
        }
        if (startBlock == 0 || paidBlocks == 0) {
            return (false, "Invalid rental parameters");
        }
        
        // Validate module exists
        (address moduleOwner,,,, bool moduleActive,) = registry.getModule(moduleId);
        if (moduleOwner == address(0)) {
            return (false, "Module does not exist");
        }
        if (!moduleActive) {
            return (false, "Module not active");
        }
        
        return (true, "Rental valid");
    }
    
    /**
     * @dev Get comprehensive system statistics
     */
    function getSystemStats() external view returns (
        uint256 totalModules,
        uint256 totalRentals,
        uint256 totalStaked,
        uint256 totalBlocTime,
        uint256 treasuryBalance
    ) {
        totalModules = registry.nextModuleId() - 1;
        totalRentals = marketplace.nextRentalId() - 1;
        totalStaked = paymentToken.balanceOf(address(staking));
        totalBlocTime = staking.totalBlocTime();
        treasuryBalance = staking.treasuryBalance();
    }
}
