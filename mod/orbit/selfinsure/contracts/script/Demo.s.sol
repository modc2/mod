// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script, console2} from "forge-std/Script.sol";
import {SelfInsure} from "../src/SelfInsure.sol";
import {SelfInsureFactory} from "../src/SelfInsureFactory.sol";
import {SignedOracle} from "../src/oracles/SignedOracle.sol";

/// A stand-in USDC for a local chain.
contract DemoUSD {
    string public constant name = "Demo USD";
    string public constant symbol = "dUSD";
    uint8 public constant decimals = 6;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }
    function approve(address s, uint256 a) external returns (bool) { allowance[msg.sender][s] = a; return true; }
    function transfer(address to, uint256 a) external returns (bool) {
        balanceOf[msg.sender] -= a; balanceOf[to] += a; return true;
    }
    function transferFrom(address f, address to, uint256 a) external returns (bool) {
        allowance[f][msg.sender] -= a; balanceOf[f] -= a; balanceOf[to] += a; return true;
    }
}

/// One whole health mutual on a local anvil: factory, oracle, a stablecoin, a
/// pool on the health template, members, adjudicators, a claim verified by a
/// signed hospital bill and paid. Run against anvil's default accounts:
///
///   forge script script/Demo.s.sol --rpc-url http://127.0.0.1:8545 --broadcast
///
/// Then read it back with no key at all: si_onchain address=<pool>.
contract Demo is Script {
    // anvil's well-known test keys (mnemonic "test test ... junk")
    uint256 constant K0 = 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80; // operator
    uint256 constant K1 = 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d; // reporter
    uint256 constant K2 = 0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a; // judge A
    uint256 constant K3 = 0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6; // judge B
    uint256 constant K4 = 0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a; // member
    uint256 constant K5 = 0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba; // member
    uint256 constant K6 = 0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e; // member

    DemoUSD usd;
    SelfInsureFactory factory;
    SignedOracle oracle;
    SelfInsure pool;

    function run() external {
        _deploy();
        _members();
        _judges();
        uint256 id = _claim();
        _attestAndSettle(id);
        console2.log("USD      ", address(usd));
        console2.log("FACTORY  ", address(factory));
        console2.log("ORACLE   ", address(oracle));
        console2.log("POOL     ", address(pool));
        console2.log("CLAIM    ", id);
        console2.log("paid     ", pool.claim(id).paid);
        console2.log("balance  ", pool.balance());
        console2.log("opShare  ", pool.operatorShareBps());
    }

    function _deploy() internal {
        vm.startBroadcast(K0);
        usd = new DemoUSD();
        factory = new SelfInsureFactory();
        oracle = new SignedOracle(vm.addr(K0));
        oracle.setReporter(vm.addr(K1), true, "Mercy General billing v2");
        pool = SelfInsure(payable(factory.openHealth(
            "Travis County health mutual", "", address(usd), 1e6, address(oracle),
            SelfInsure.OracleMode.Required)));
        _noWait();
        usd.mint(vm.addr(K4), 2_000e6);
        usd.mint(vm.addr(K5), 2_000e6);
        usd.mint(vm.addr(K6), 2_000e6);
        vm.stopBroadcast();
    }

    /// a demo has no 30 days to wait; everything else stays on the template
    function _noWait() internal {
        (uint256 p, uint256 per, uint256 cov, uint256 ded, uint256 cap, , uint256 floor,
         uint16 fee, uint16 q, uint16 th, bool approved) = pool.terms();
        pool.setTerms(SelfInsure.Terms(p, per, cov, ded, cap, 0, floor, fee, q, th, approved));
    }

    function _members() internal {
        uint256[3] memory members = [K4, K5, K6];
        for (uint256 i = 0; i < 3; i++) {
            vm.startBroadcast(members[i]);
            usd.approve(address(pool), 400e6);
            pool.join(400e6);
            vm.stopBroadcast();
        }
    }

    function _judges() internal {
        vm.startBroadcast(K2); pool.registerAgent("claims reviewer", "human", ""); vm.stopBroadcast();
        vm.startBroadcast(K3); pool.registerAgent("bill checker", "ai", "claude-fable-5"); vm.stopBroadcast();
        vm.startBroadcast(K0);
        pool.admitAgent(vm.addr(K2), true);
        pool.admitAgent(vm.addr(K3), true);
        vm.stopBroadcast();
    }

    /// a member is billed $1,250 for an ER visit and files the itemised bill;
    /// both adjudicators accept; the claim then waits on the oracle (Required)
    function _claim() internal returns (uint256 id) {
        vm.startBroadcast(K4);
        id = pool.fileClaim(1_250e6, "ER visit, broken wrist", "ipfs://bafy...bill-4471");
        vm.stopBroadcast();
        vm.startBroadcast(K2); pool.vote(id, true, "itemised bill matches CPT 25600 at the contracted rate"); vm.stopBroadcast();
        vm.startBroadcast(K3); pool.vote(id, true, "provider licence verified; no duplicate claim on file"); vm.stopBroadcast();
    }

    /// the hospital's billing system signs the bill; the member relays it
    function _attestAndSettle(uint256 id) internal {
        bytes32 dataHash = keccak256("itemised bill #4471");
        uint64 expiry = uint64(block.timestamp + 1 days);
        bytes32 digest = oracle.digest(address(pool), id, true, 1_250e6, dataHash, expiry);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(K1, digest);
        vm.startBroadcast(K4);
        oracle.submit(address(pool), id, true, 1_250e6, dataHash, expiry,
                      "Mercy General billing v2", abi.encodePacked(r, s, v));
        pool.settle(id);
        vm.stopBroadcast();
    }
}
