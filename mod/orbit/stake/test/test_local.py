#!/usr/bin/env python3
"""End-to-end test of AppStaking on the local hardhat node (:8545).

Deploys the REAL chain Registry contract + MockBloc + AppStaking, registers
apps, then walks stake / unstake / reward / claim including the failure cases.
Run: python3 test/test_local.py
"""
import json
import sys
from pathlib import Path

from web3 import Web3

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "scripts"))
from compile import compile_sources  # noqa: E402

RPC = "http://localhost:8545"
REGISTRY_SRC = Path("/root/mod/mod/core/chain/src/contracts/registry/Registry.sol")

E = 10**18
checks = []


def ok(label, cond):
    checks.append((label, bool(cond)))
    print(("PASS " if cond else "FAIL ") + label)


def reverts(label, fn):
    try:
        fn()
        ok(label, False)
    except Exception:
        ok(label, True)


def artifact(name):
    return json.loads((HERE / "artifacts" / f"{name}.json").read_text())


def main():
    w3 = Web3(Web3.HTTPProvider(RPC))
    assert w3.is_connected(), "no local node on :8545"
    deployer, alice, bob = w3.eth.accounts[:3]

    def deploy(art, *args, frm=deployer):
        c = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
        tx = c.constructor(*args).transact({"from": frm})
        rcpt = w3.eth.wait_for_transaction_receipt(tx)
        return w3.eth.contract(address=rcpt.contractAddress, abi=art["abi"])

    def send(fn, frm):
        tx = fn.transact({"from": frm})
        return w3.eth.wait_for_transaction_receipt(tx)

    # registry is compiled fresh from the chain module's canonical source
    reg_out = compile_sources({"Registry.sol": REGISTRY_SRC.read_text()})
    reg_art = next(c for c in reg_out["contracts"] if c["name"] == "Registry")

    registry = deploy(reg_art)
    bloc = deploy(artifact("MockBloc"))
    staking = deploy(artifact("AppStaking"), bloc.address, registry.address)

    send(registry.functions.registerMod("web", "cid-web"), deployer)     # id 1
    send(registry.functions.registerMod("agent", "cid-agent"), deployer)  # id 2

    for who in (alice, bob):
        send(bloc.functions.mint(who, 1000 * E), deployer)
        send(bloc.functions.approve(staking.address, 10**30), who)

    # --- stake ---------------------------------------------------------------
    reverts("stake on unregistered app reverts", lambda: send(staking.functions.stake(99, 10 * E), alice))
    reverts("stake of zero reverts", lambda: send(staking.functions.stake(1, 0), alice))

    send(staking.functions.stake(1, 100 * E), alice)
    send(staking.functions.stake(1, 300 * E), bob)
    send(staking.functions.stake(2, 50 * E), alice)

    ok("totalStaked app1 = 400", staking.functions.totalStaked(1).call() == 400 * E)
    ok("totalStaked app2 = 50", staking.functions.totalStaked(2).call() == 50 * E)
    ok("totalStakedAll = 450", staking.functions.totalStakedAll().call() == 450 * E)
    ok("getStakedApps = [1,2]", staking.functions.getStakedApps().call() == [1, 2])
    stakers, amounts = staking.functions.getAppStakers(1).call()
    ok("app1 staker book", stakers == [alice, bob] and amounts == [100 * E, 300 * E])
    ids, amts, claimable = staking.functions.getPositions(alice).call()
    ok("alice positions", ids == [1, 2] and amts == [100 * E, 50 * E] and claimable == [0, 0])

    # --- rewards -------------------------------------------------------------
    reverts("reward with no stakers reverts", lambda: send(staking.functions.reward(3, 10 * E), deployer))
    send(bloc.functions.mint(deployer, 100 * E), deployer)
    send(bloc.functions.approve(staking.address, 10**30), deployer)
    send(staking.functions.reward(1, 100 * E), deployer)  # alice 25%, bob 75%

    ok("alice earned 25", staking.functions.earned(1, alice).call() == 25 * E)
    ok("bob earned 75", staking.functions.earned(1, bob).call() == 75 * E)
    ok("app2 earned untouched", staking.functions.earned(2, alice).call() == 0)

    before = bloc.functions.balanceOf(alice).call()
    send(staking.functions.claim(1), alice)
    ok("claim pays alice 25", bloc.functions.balanceOf(alice).call() - before == 25 * E)
    ok("earned resets after claim", staking.functions.earned(1, alice).call() == 0)
    send(staking.functions.claim(1), alice)  # no-op, must not revert
    ok("double claim is a no-op", bloc.functions.balanceOf(alice).call() - before == 25 * E)

    # late staker earns nothing from past rewards
    send(bloc.functions.mint(deployer, 500 * E), deployer)
    send(staking.functions.stake(1, 400 * E), deployer)
    ok("late staker starts at 0 earned", staking.functions.earned(1, deployer).call() == 0)
    send(staking.functions.reward(1, 80 * E), deployer)  # 800 staked: a 100, b 300, d 400
    ok("second reward splits by weight (alice 10)", staking.functions.earned(1, alice).call() == 10 * E)
    ok("second reward splits by weight (bob 75+30)", staking.functions.earned(1, bob).call() == 105 * E)
    ok("second reward splits by weight (deployer 40)", staking.functions.earned(1, deployer).call() == 40 * E)

    # --- unstake -------------------------------------------------------------
    reverts("unstake more than staked reverts", lambda: send(staking.functions.unstake(1, 101 * E), alice))
    before = bloc.functions.balanceOf(alice).call()
    send(staking.functions.unstake(1, 40 * E), alice)
    ok("partial unstake returns principal", bloc.functions.balanceOf(alice).call() - before == 40 * E)
    ok("partial unstake keeps earned", staking.functions.earned(1, alice).call() == 10 * E)

    # app removal never traps funds
    send(registry.functions.removeMod(1), deployer)
    reverts("stake on removed app reverts", lambda: send(staking.functions.stake(1, 1 * E), alice))
    before = bloc.functions.balanceOf(alice).call()
    send(staking.functions.unstake(1, 0), alice)  # 0 = all
    ok("unstake-all after app removal", bloc.functions.balanceOf(alice).call() - before == 60 * E)
    ok("stake zeroed", staking.functions.staked(1, alice).call() == 0)
    send(staking.functions.claimMany([1, 2]), alice)
    ok("rewards survive app removal", staking.functions.earned(1, alice).call() == 0)

    reverts("unstake with nothing staked reverts", lambda: send(staking.functions.unstake(1, 0), alice))

    # conservation: contract holds exactly principal + unclaimed rewards
    held = bloc.functions.balanceOf(staking.address).call()
    outstanding = staking.functions.totalStakedAll().call() \
        + staking.functions.earned(1, bob).call() + staking.functions.earned(1, deployer).call()
    ok("contract balance covers principal + unclaimed", held == outstanding)

    failed = [l for l, c in checks if not c]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    if failed:
        raise SystemExit("FAILED: " + ", ".join(failed))


if __name__ == "__main__":
    main()
