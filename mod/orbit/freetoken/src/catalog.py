"""What there is to serve, and what is already on this disk.

The known-good table is upstream's docs/models.md — the checkpoints the
prebuilt kernels are tuned for. Other checkpoints of the same architectures
load too; these are the ones with a guarantee behind them.

The local half is a scan, not a claim: the Hugging Face cache and any
directory the operator points at, with FTW conversions (a dir of
`freetoken-*.ftw` shards) told apart from raw safetensors, because `ft serve
--model` takes either and only one of them skips the conversion.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import state

# docs/models.md — model family → known-good HF checkpoints.
KNOWN: List[Dict[str, Any]] = [
    {'family': 'DeepSeek-V4', 'moe': True,
     'checkpoints': ['deepseek-ai/DeepSeek-V4-Flash-0731'],
     'note': 'keep the inference/config.json subdir — the authoritative model '
             'args are read from there; page size is forced to 128'},
    {'family': 'GLM-5.2', 'moe': True, 'checkpoints': ['nvidia/GLM-5.2-NVFP4']},
    {'family': 'GLM-4.7', 'moe': True, 'checkpoints': ['nvidia/GLM-4.7-NVFP4']},
    {'family': 'Qwen3.6 / Qwen3.5 MoE', 'moe': True,
     'checkpoints': ['Qwen/Qwen3.6-35B-A3B', 'Qwen/Qwen3.6-35B-A3B-FP8',
                     'nvidia/Qwen3.6-35B-A3B-NVFP4', 'Qwen/Qwen3.5-35B-A3B',
                     'Qwen/Qwen3.5-35B-A3B-FP8'],
     'note': 'the quickstart model'},
    {'family': 'Qwen3.6 dense', 'moe': False,
     'checkpoints': ['Qwen/Qwen3.6-27B', 'Qwen/Qwen3.6-27B-FP8',
                     'nvidia/Qwen3.6-27B-NVFP4'],
     'note': 'dense — --moe-backend auto resolves to fused, experts stay resident'},
    {'family': 'Qwen3-MoE', 'moe': True, 'checkpoints': ['Qwen/Qwen3-30B-A3B']},
    {'family': 'gpt-oss', 'moe': True,
     'checkpoints': ['openai/gpt-oss-120b', 'openai/gpt-oss-20b']},
    {'family': 'Gemma-4', 'moe': True,
     'checkpoints': ['google/gemma-4-26B-A4B-it', 'nvidia/Gemma-4-26B-A4B-NVFP4',
                     'google/gemma-4-12B-it', 'nvidia/Gemma-4-31B-IT-NVFP4'],
     'note': 'the one family that also loads native GGUF'},
    {'family': 'MiniMax-M2.5', 'moe': True, 'checkpoints': ['nvidia/MiniMax-M2.5-NVFP4']},
    {'family': 'Muse-Glimmer', 'moe': True,
     'checkpoints': ['meta-models/Muse-Glimmer-30B', 'RedHatAI/Muse-Glimmer-30B-NVFP4']},
]

# docs/models.md — what --moe-backend actually changes.
MOE_BACKENDS: Dict[str, str] = {
    'auto': 'dense → fused; MoE → offload, upgraded to hybrid when a cached '
            '`ft bench bw` profile recommends it',
    'fused': 'experts resident on the GPU — needs the VRAM, never auto-selected',
    'offload': 'experts in host RAM, an LRU cache of expert slots on the GPU; '
               'misses stream over PCIe',
    'cpu': 'misses are computed on the CPU instead of fetched over PCIe',
    'hybrid': 'per step, fetch some misses over PCIe and compute the rest on CPU, '
              'overlapped — run `ft bench bw` once per machine to calibrate the split',
}

QUANTS = ['MXFP4', 'NVFP4', 'FP8', 'BF16']


def known(moe_only: bool = False) -> List[Dict[str, Any]]:
    return [m for m in KNOWN if m['moe']] if moe_only else list(KNOWN)


def _dir_bytes(path: Path, cap: int = 40_000) -> int:
    total, seen = 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
            seen += 1
            if seen > cap:
                return total
    return total


def _kind(path: Path) -> Optional[str]:
    """What `ft serve --model` would see in this directory."""
    if any(path.glob('freetoken-*.ftw')):
        return 'ftw'
    if any(path.glob('*.safetensors')) or (path / 'model.safetensors.index.json').exists():
        return 'safetensors'
    if any(path.glob('*.gguf')):
        return 'gguf'
    return None


def search_paths() -> List[Path]:
    """The HF cache, this module's own model dir, and anything FREETOKEN_MODELS adds."""
    found = [Path(os.environ.get('HF_HOME', Path.home() / '.cache' / 'huggingface')) / 'hub',
             state.home() / 'models']
    for extra in (os.environ.get('FREETOKEN_MODELS') or '').split(':'):
        if extra.strip():
            found.append(Path(extra.strip()).expanduser())
    for extra in state.read('config.json', {}).get('model_dirs', []):
        found.append(Path(extra).expanduser())
    return [p for p in found if p.exists()]


def local(size: bool = True) -> List[Dict[str, Any]]:
    """Checkpoints already on this disk. A repo id where the cache knows one."""
    out: List[Dict[str, Any]] = []
    for root in search_paths():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith('models--'):        # HF cache layout
                repo = child.name[len('models--'):].replace('--', '/', 1)
                snapshots = child / 'snapshots'
                targets = sorted(snapshots.iterdir()) if snapshots.exists() else []
                for snap in targets:
                    kind = _kind(snap)
                    if kind:
                        out.append({'repo': repo, 'path': str(snap), 'kind': kind,
                                    'gb': round(_dir_bytes(child) / 1024 ** 3, 1) if size else None,
                                    'where': 'hf cache'})
                        break
                continue
            kind = _kind(child)
            if kind:
                out.append({'repo': None, 'path': str(child), 'kind': kind,
                            'gb': round(_dir_bytes(child) / 1024 ** 3, 1) if size else None,
                            'where': str(root)})
    return out


def catalog(size: bool = True) -> Dict[str, Any]:
    on_disk = local(size=size)
    have = {entry['repo'] for entry in on_disk if entry['repo']}
    table = []
    for family in known():
        table.append({**family,
                      'local': [c for c in family['checkpoints'] if c in have]})
    return {
        'known_good': table,
        'local': on_disk,
        'searched': [str(p) for p in search_paths()],
        'moe_backends': MOE_BACKENDS,
        'quantizations': QUANTS,
        'note': 'known-good means the prebuilt kernels are tuned for it; other '
                'checkpoints of the same architectures also load. Multimodal '
                'checkpoints are served text-only.',
    }
