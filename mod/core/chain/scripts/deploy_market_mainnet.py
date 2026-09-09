#!/usr/bin/env python3
"""Deploy the Market fleet to Base mainnet (chainId 8453) and wire it up.

The Market contract (credit / mint / mintWithETH top-ups, debit billing)
exists on Base Sepolia testnet but not on any mainnet. This script ships the
same fleet to Base mainnet using the chain module's wave deployer:

  - real USDC / USDT / DAI are recorded from KNOWN_ADDRESSES, never deployed
  - NativeToken, ManualPriceOracle, TokenGate, BlocTime, Treasury, Market and
    Debit are deployed and wired exactly like testnet
  - after deploy, the ETH oracle price is set from a live quote instead of the
    hardcoded $3000 default, so mintWithETH() top-ups are priced correctly

Usage:
    python3 deploy_market_mainnet.py            # preflight only, no gas spent
    python3 deploy_market_mainnet.py --deploy   # the real thing

Env:
    KEY=<key name>   deployer key (default: test — same key as testnet)

Once deployed, top-up works with the same plumbing as testnet:
    chain API:  POST /credit {"stable_amount": 10, "payment_token": "usdc",
                              "network": "mainnet"}
    compute:    Compute(network='mainnet').topup(10, token='usdc')
"""
import importlib.util
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CHAIN_DIR = os.path.dirname(HERE)
MIN_GAS_ETH = 0.002  # comfortable headroom; the whole fleet is ~10-15M gas

# The market dependency chain only — registry/perms are not needed for top-up
# or billing and can ship later.
MODS = ['token', 'oracle', 'tokengate', 'bloctime', 'treasury', 'market', 'debit']


def load_chain_cls():
    spec = importlib.util.spec_from_file_location(
        'mod_core_chain', os.path.join(CHAIN_DIR, 'src', 'mod.py'))
    chain_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(chain_mod)
    return chain_mod


def live_eth_price_8dec():
    """Live ETH/USD as an 8-decimal int (Chainlink convention), or None."""
    sources = [
        ('https://api.coinbase.com/v2/prices/ETH-USD/spot',
         lambda d: float(d['data']['amount'])),
        ('https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd',
         lambda d: float(d['ethereum']['usd'])),
    ]
    for url, pick in sources:
        try:
            req = urllib.request.Request(url, headers={'user-agent': 'mod-chain'})
            usd = pick(json.load(urllib.request.urlopen(req, timeout=10)))
            if usd > 0:
                return int(usd * 10 ** 8)
        except Exception:
            continue
    return None


def main():
    do_deploy = '--deploy' in sys.argv
    key = os.environ.get('KEY', 'test')

    chain_mod = load_chain_cls()
    c = chain_mod.Mod(network='mainnet', key=key)
    # Pin paths to this checkout — name-collision-proof (a stub module named
    # `chain` elsewhere in the tree can hijack m.dp('chain')).
    c.path = CHAIN_DIR
    c.contracts_path = os.path.join(CHAIN_DIR, 'artifacts', 'src', 'contracts')

    print(f'network   : mainnet ({c.rpc_url}, chainId {c.chain_id})')
    print(f'deployer  : {c.account.address} (key: {key})')

    if c.chain_id != 8453:
        sys.exit(f'ABORT: expected Base mainnet chainId 8453, got {c.chain_id}')

    # ── Preflight: artifacts ────────────────────────────────────────────
    needed = ['Token', 'ManualPriceOracle', 'TokenGate', 'BlocTime',
              'Treasury', 'Market', 'Debit']
    missing = []
    for name in needed:
        try:
            c.artifact(name)
        except Exception:
            missing.append(name)
    if missing:
        sys.exit(f'ABORT: missing compiled artifacts for {missing} — '
                 f'run `npx hardhat compile` in {CHAIN_DIR}')
    print(f'artifacts : all {len(needed)} present')

    # ── Preflight: already deployed? ────────────────────────────────────
    existing = c.deployed_address('Market', 'mainnet')
    if existing:
        code = c.w3.eth.get_code(c.checksum(existing))
        if len(code) > 2:
            sys.exit(f'Market already live on mainnet at {existing} — nothing to do.')
        print(f'config records Market at {existing} but no code on chain; redeploying')

    # ── Preflight: gas ──────────────────────────────────────────────────
    bal = c.w3.eth.get_balance(c.account.address) / 1e18
    print(f'gas       : {bal:.6f} ETH on Base mainnet '
          f'(need ~{MIN_GAS_ETH} ETH)')
    if bal < MIN_GAS_ETH:
        sys.exit(f'ABORT: fund {c.account.address} with at least {MIN_GAS_ETH} ETH '
                 f'on Base mainnet (bridge: https://bridge.base.org), then re-run.')

    print(f'waves     : {c.waves(MODS)}')
    if not do_deploy:
        print('\nPreflight OK. Re-run with --deploy to spend gas and ship it.')
        return

    # ── Deploy ──────────────────────────────────────────────────────────
    deployed = c.deploy(network='mainnet', mods=MODS)

    # ── Correct the ETH oracle price for mintWithETH ────────────────────
    price = live_eth_price_8dec()
    if price:
        c.send_tx('manualpriceoracle', 'setPrice',
                  [chain_mod.ETH_SENTINEL, price, 8])
        print(f'ETH oracle price set to ${price / 1e8:,.2f}')
    else:
        print('WARNING: could not fetch a live ETH price — mintWithETH will '
              'use the $3000 default until you call ManualPriceOracle.setPrice.')

    print('\nDeployed to Base mainnet:')
    for name, addr in deployed.items():
        print(f'  {name:24s} {addr}  https://basescan.org/address/{addr}')
    print('\nTop-up is live: POST /credit with network="mainnet", or '
          "Compute(network='mainnet').topup(<usd>, token='usdc')")


if __name__ == '__main__':
    main()
