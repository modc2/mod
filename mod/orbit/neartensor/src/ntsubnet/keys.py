"""
Hotkey handling. Local mode keeps a per-role sr25519 keypair on disk under
data/keys/ (0600, mnemonic never leaves the box). Subtensor mode uses the
configured bittensor wallet's hotkey. Either way callers get a Keypair with
.ss58_address and .sign(), or None when signing is unavailable — the reward
function treats unsigned answers as worthless, it never crashes on them.
"""
import json
import os


def get_keypair(cfg, role):
    if not cfg.is_local:
        try:
            import bittensor as bt
            return bt.wallet(name=cfg.wallet_name, hotkey=cfg.wallet_hotkey).hotkey
        except Exception:
            return None
    try:
        from bittensor_wallet import Keypair
    except Exception:
        return None
    keys_dir = os.path.join(cfg.data_dir, "keys")
    os.makedirs(keys_dir, exist_ok=True)
    path = os.path.join(keys_dir, f"{role}.json")
    if os.path.exists(path):
        with open(path) as f:
            return Keypair.create_from_mnemonic(json.load(f)["mnemonic"])
    mnemonic = Keypair.generate_mnemonic()
    kp = Keypair.create_from_mnemonic(mnemonic)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"mnemonic": mnemonic, "ss58": kp.ss58_address, "role": role}, f)
    return kp
