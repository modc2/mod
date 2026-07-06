"""Multi-chain Registry deployer.

Deploys the Registry contract to EVM (Base), NEAR, and Solana networks.
Updates config.json with deployed addresses.

Usage:
    python deploy.py                    # Deploy to all networks (testnet)
    python deploy.py --network evm      # Deploy only EVM
    python deploy.py --network near     # Deploy only NEAR
    python deploy.py --network all      # Deploy to all
    python deploy.py --mainnet          # Deploy to mainnet
"""

import json
import os
import subprocess
import sys
import time
import argparse

DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(DIR, 'config.json')


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {
        'default_backend': 'offchain',
        'default_network': 'testnet',
        'evm': {}, 'solana': {}, 'near': {}, 'offchain': {'storage_path': None}
    }


def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)
    print(f'  Saved config to {CONFIG_PATH}')


def run(cmd, cwd=None, timeout=300):
    """Run a shell command."""
    print(f'  $ {cmd}')
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True, timeout=timeout
    )
    if result.stdout:
        print(result.stdout[:2000])
    if result.returncode != 0:
        print(f'  ERROR: {result.stderr[:1000]}')
        return None
    return result.stdout.strip()


# ── EVM Deploy ───────────────────────────────────────────────────────────────

def deploy_evm(network='testnet'):
    """Deploy Registry.sol to Base (EVM) via Hardhat."""
    print(f'\n{"="*60}')
    print(f'  EVM Registry Deploy → {network}')
    print(f'{"="*60}')

    evm_dir = os.path.join(DIR, '..', 'onchain', 'src', 'evm')

    # Check for node_modules
    if not os.path.exists(os.path.join(evm_dir, 'node_modules')):
        print('  Installing npm dependencies...')
        run('npm install', cwd=evm_dir)

    # Compile
    print('  Compiling contracts...')
    result = run('npx hardhat compile', cwd=evm_dir)
    if result is None:
        print('  Failed to compile. Check hardhat config and Solidity source.')
        return None

    # Deploy
    print(f'  Deploying to {network}...')
    hh_network = network
    result = run(f'npx hardhat run scripts/deploy.js --network {hh_network}', cwd=evm_dir)
    if result is None:
        print('  Deploy failed. Make sure PRIVATE_KEY is set in .env')
        return None

    # Read updated config
    config = load_config()
    addr = config.get('evm', {}).get(network, {}).get('registry')
    if addr:
        print(f'  Registry deployed: {addr}')

    # Also generate ABI artifact for the frontend app
    artifact_path = os.path.join(evm_dir, 'artifacts', 'contracts', 'Registry.sol', 'Registry.json')
    if os.path.exists(artifact_path):
        app_abi_dir = os.path.join(DIR, 'app', 'src', 'contracts')
        os.makedirs(app_abi_dir, exist_ok=True)
        with open(artifact_path) as f:
            artifact = json.load(f)
        abi_out = os.path.join(app_abi_dir, 'Registry.json')
        with open(abi_out, 'w') as f:
            json.dump({'abi': artifact.get('abi', [])}, f, indent=2)
        print(f'  ABI saved to {abi_out}')

    return addr


# ── NEAR Deploy ──────────────────────────────────────────────────────────────

def deploy_near(network='testnet'):
    """Deploy Registry contract to NEAR via deploy.sh."""
    print(f'\n{"="*60}')
    print(f'  NEAR Registry Deploy → {network}')
    print(f'{"="*60}')

    near_dir = os.path.join(DIR, '..', 'onchain', 'src', 'near')
    deploy_script = os.path.join(near_dir, 'deploy.sh')

    if not os.path.exists(deploy_script):
        print('  deploy.sh not found')
        return None

    result = run(f'bash deploy.sh', cwd=near_dir, timeout=600)
    if result is None:
        print('  NEAR deploy failed.')
        return None

    # Read deployment info
    deploy_info_path = os.path.join(near_dir, 'deployment.json')
    if os.path.exists(deploy_info_path):
        with open(deploy_info_path) as f:
            info = json.load(f)
        account = info.get('account')

        config = load_config()
        config.setdefault('near', {})
        config['near'][network] = {
            'rpc': f'https://rpc.{network}.near.org',
            'account': account,
            'deployed_at': info.get('deployed_at'),
        }
        save_config(config)
        print(f'  Registry deployed: {account}')
        return account

    return None


# ── Solana Deploy ────────────────────────────────────────────────────────────

def deploy_solana(network='devnet'):
    """Deploy Registry to Solana (Anchor program)."""
    print(f'\n{"="*60}')
    print(f'  Solana Registry Deploy → {network}')
    print(f'{"="*60}')

    solana_dir = os.path.join(DIR, '..', 'onchain', 'src', 'solana')

    if not os.path.exists(os.path.join(solana_dir, 'Anchor.toml')):
        print('  Anchor.toml not found. Solana deploy requires Anchor framework.')
        print('  Install: cargo install --git https://github.com/coral-xyz/anchor avm')
        return None

    # Build
    print('  Building Anchor program...')
    result = run('anchor build', cwd=solana_dir, timeout=600)
    if result is None:
        print('  Build failed.')
        return None

    # Deploy
    print(f'  Deploying to {network}...')
    result = run(f'anchor deploy --provider.cluster {network}', cwd=solana_dir)
    if result is None:
        print('  Deploy failed.')
        return None

    # Extract program ID from output or Anchor.toml
    config = load_config()
    config.setdefault('solana', {})
    config['solana'][network] = {
        'rpc': f'https://api.{network}.solana.com',
        'program_id': None,  # Will be set after successful deploy
        'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    save_config(config)
    return None


# ── Generate app config ─────────────────────────────────────────────────────

def generate_app_config():
    """Generate frontend config from registry config.json."""
    config = load_config()
    app_config = {
        'networks': {},
        'default_network': config.get('default_network', 'testnet'),
    }

    # EVM networks
    for net_name, net_cfg in config.get('evm', {}).items():
        app_config['networks'][f'evm_{net_name}'] = {
            'type': 'evm',
            'name': f'Base {net_name.title()}',
            'rpc': net_cfg.get('rpc', ''),
            'chain_id': net_cfg.get('chain_id'),
            'registry': net_cfg.get('registry'),
        }

    # NEAR networks
    for net_name, net_cfg in config.get('near', {}).items():
        app_config['networks'][f'near_{net_name}'] = {
            'type': 'near',
            'name': f'NEAR {net_name.title()}',
            'rpc': net_cfg.get('rpc', ''),
            'account': net_cfg.get('account'),
        }

    # Solana networks
    for net_name, net_cfg in config.get('solana', {}).items():
        app_config['networks'][f'solana_{net_name}'] = {
            'type': 'solana',
            'name': f'Solana {net_name.title()}',
            'rpc': net_cfg.get('rpc', ''),
            'program_id': net_cfg.get('program_id'),
        }

    app_config_path = os.path.join(DIR, 'app', 'src', 'config.json')
    os.makedirs(os.path.dirname(app_config_path), exist_ok=True)
    with open(app_config_path, 'w') as f:
        json.dump(app_config, f, indent=2)
    print(f'\n  App config generated: {app_config_path}')
    return app_config


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Deploy Registry contracts')
    parser.add_argument('--network', '-n', default='all',
                        choices=['all', 'evm', 'near', 'solana'],
                        help='Which network to deploy to')
    parser.add_argument('--env', '-e', default='testnet',
                        help='Environment (testnet, mainnet, devnet)')
    parser.add_argument('--skip-app-config', action='store_true',
                        help='Skip generating frontend app config')
    args = parser.parse_args()

    print('='*60)
    print('  Registry Multi-Chain Deployer')
    print(f'  Networks: {args.network}  |  Env: {args.env}')
    print('='*60)

    results = {}

    if args.network in ('all', 'evm'):
        results['evm'] = deploy_evm(args.env)

    if args.network in ('all', 'near'):
        results['near'] = deploy_near(args.env)

    if args.network in ('all', 'solana'):
        solana_env = 'devnet' if args.env == 'testnet' else args.env
        results['solana'] = deploy_solana(solana_env)

    # Generate app config
    if not args.skip_app_config:
        generate_app_config()

    print(f'\n{"="*60}')
    print('  Deployment Results')
    print(f'{"="*60}')
    for net, addr in results.items():
        status = addr or 'skipped/failed'
        print(f'  {net:>10}: {status}')
    print(f'{"="*60}\n')


if __name__ == '__main__':
    main()
