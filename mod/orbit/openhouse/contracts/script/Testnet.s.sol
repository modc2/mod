// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {OpenHouse} from "../OpenHouse.sol";
import {ApprovedTokens} from "../ApprovedTokens.sol";

/// The four cheatcodes a broadcast needs — no forge-std, same as the tests.
interface Vm {
    function envOr(string calldata name, uint256 defaultValue) external view returns (uint256);
    function addr(uint256 privateKey) external pure returns (address);
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
}

/// @title Testnet walkthrough, on a real chain
/// @notice Deploys the token whitelist and one OpenHouse property, then has a
///         renter pay one month of rent in the native coin — three signed
///         transactions from three different keys, exactly as mainnet would.
///
///   local (anvil, zero setup):   ./testnet.sh
///   Base Sepolia (real testnet): RPC_URL=https://sepolia.base.org \
///        OWNER_KEY=0x… BANK_KEY=0x… RENTER_KEY=0x… ./testnet.sh
///
/// Keys come from the environment and are never written anywhere. The
/// defaults are anvil's published development keys — public, worthless, and
/// only ever valid against a local chain.
contract Testnet {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    // anvil accounts 0, 1, 2 — public development keys
    uint256 constant ANVIL_0 = 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80;
    uint256 constant ANVIL_1 = 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d;
    uint256 constant ANVIL_2 = 0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a;

    function run()
        external
        returns (address house, address tokens, uint256 principalUsd18, uint256 equityPpm, uint256 feePoolUsd18)
    {
        uint256 ownerKey = vm.envOr("OWNER_KEY", ANVIL_0);
        uint256 bankKey = vm.envOr("BANK_KEY", ANVIL_1);
        uint256 renterKey = vm.envOr("RENTER_KEY", ANVIL_2);
        uint256 ethUsd = vm.envOr("ETH_USD", 2500) * 1e18;          // 1 ETH = $2,500
        uint256 homeUsd = vm.envOr("HOME_USD", 240_000) * 1e18;     // a $240k home
        uint256 rentWei = vm.envOr("RENT_WEI", 0.0062 ether);       // small: testnet ETH is scarce
        address owner = vm.addr(ownerKey);

        // 1. The owner lists the payment tokens and deploys the property:
        //    2.5% protocol fee, full credit — every net dollar buys the house.
        vm.startBroadcast(ownerKey);
        ApprovedTokens list = new ApprovedTokens("ETH", ethUsd);
        OpenHouse oh = new OpenHouse(
            "TESTNET - 128 Maple Ave (fictional)",
            homeUsd,
            address(list),
            address(0),          // no yield vault: principal settles to the owner
            owner,               // treasury
            vm.addr(bankKey),    // the second governance seat
            250,                 // 2.5% fee
            10_000               // 100% of the rest is renter equity
        );
        vm.stopBroadcast();

        // 2. The renter pays one month in the native coin.
        vm.startBroadcast(renterKey);
        oh.payRent{value: rentWei}(address(0), rentWei);
        vm.stopBroadcast();

        address renter = vm.addr(renterKey);
        // principal and pool are 18-dec USD value; equity in parts per million of the
        // home, because one month of a $240k house rounds to 0 basis points.
        uint256 p = oh.principalPaid(renter);
        return (address(oh), address(list), p, (p * 1e6) / homeUsd, oh.pendingPool());
    }
}
