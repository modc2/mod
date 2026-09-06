#!/usr/bin/env python3
"""Deploy AppStaking to Base Sepolia against the live BlocTime + Registry.

Signs with the mod server key (~/.mod/key/test/ecdsa). Writes the deployed
address back into this module's config.json under contracts.testnet.
sepolia.base.org sits behind a flaky load balancer: gas is explicit (no
eth_estimateGas), nonce comes from 'pending', fees are EIP-1559.
"""
import json
import os
import sys
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3

HERE = Path(__file__).resolve().parent.parent
RPC = os.environ.get("STAKE_RPC", "https://sepolia.base.org")
CHAIN_ID = 84532
BLOC = "0xF25AAFDd0A842ff50b041595C79210b48d6795bD"
REGISTRY = "0xF7a5498369d7ceA13461BcfDC65995B8743baE97"
KEY_DIR = Path.home() / ".mod" / "key" / "test" / "ecdsa"


def load_account() -> Account:
    key_file = next(KEY_DIR.glob("0x*.json"))
    pk = json.loads(key_file.read_text())["data"]["private_key"]
    return Account.from_key(pk)


def main():
    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 30}))
    acct = load_account()
    assert w3.eth.chain_id == CHAIN_ID, f"wrong chain: {w3.eth.chain_id}"
    balance = w3.eth.get_balance(acct.address)
    print(f"deployer {acct.address}  balance {w3.from_wei(balance, 'ether')} ETH")

    art = json.loads((HERE / "artifacts" / "AppStaking.json").read_text())
    contract = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])

    base_fee = w3.eth.get_block("latest").get("baseFeePerGas") or w3.to_wei(0.01, "gwei")
    tip = w3.to_wei(0.001, "gwei")
    tx = contract.constructor(BLOC, REGISTRY).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
        "chainId": CHAIN_ID,
        "gas": 2_500_000,
        "maxFeePerGas": base_fee * 2 + tip,
        "maxPriorityFeePerGas": tip,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"deploy tx {tx_hash.hex()}")
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    assert rcpt.status == 1, "deploy reverted"
    addr = rcpt.contractAddress
    print(f"AppStaking deployed at {addr} (gas used {rcpt.gasUsed})")

    # sanity reads (retry: the LB serves lagging backends)
    deployed = w3.eth.contract(address=addr, abi=art["abi"])
    for _ in range(5):
        try:
            assert deployed.functions.bloc().call().lower() == BLOC.lower()
            assert deployed.functions.registry().call().lower() == REGISTRY.lower()
            break
        except Exception:
            time.sleep(3)
    else:
        sys.exit("sanity reads never came back")
    print("sanity reads ok: bloc + registry wired")

    cfg_path = HERE / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    nets = cfg.setdefault("contracts", {})
    nets["testnet"] = {
        "chainId": CHAIN_ID,
        "rpc": RPC,
        "AppStaking": addr,
        "BlocTime": BLOC,
        "Registry": REGISTRY,
        "deployTx": tx_hash.hex(),
    }
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    print("config.json updated")


if __name__ == "__main__":
    main()
