"""Whisper through transformers — the engine that needs nothing but pip.

The one thing worth knowing about this family: the encoder always sees thirty
seconds. A two-second clip is padded to thirty and costs what thirty costs, so
handing the model many short segments one at a time is the slowest way to use
it. Here they are batched, which turns that padding from wasted time into
wasted memory — much the cheaper of the two.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .base import RATE, Engine, clean

SIZES = ('tiny.en', 'tiny', 'base.en', 'base', 'small.en', 'small',
         'medium', 'large-v3', 'large-v3-turbo')


def repo_for(model: str) -> str:
    """'base.en' is openai/whisper-base.en; anything with a slash is taken as given."""
    return model if '/' in model else f'openai/whisper-{model}'


class WhisperTorch(Engine):
    name = 'whisper-torch'
    kind = 'local'
    description = ('OpenAI Whisper via transformers + torch. Runs on CPU, uses '
                   'the GPU and fp16 when there is one, batches segments.')
    needs = ('torch', 'transformers')
    default_model = 'base.en'
    batches = True

    def __init__(self, model: Optional[str] = None, device: Optional[str] = None,
                 batch_size: int = 8, **options: Any):
        super().__init__(model, **options)
        self._device = device
        self._batch = int(batch_size)
        self._model = None
        self._processor = None

    def check(self) -> Tuple[bool, str]:
        try:
            import torch                                            # noqa: F401
            import transformers                                     # noqa: F401
        except ImportError as exc:
            return False, f'pip install torch transformers ({exc.name} missing)'
        if not self._cached() and os.environ.get('HF_HUB_OFFLINE') == '1':
            return False, f'{repo_for(self.model)} not downloaded, and the hub is offline'
        return True, ('ready' if self._cached()
                      else f'ready — {repo_for(self.model)} downloads on first use')

    def _cached(self) -> bool:
        """Is the model already on disk? Nothing here should surprise-download."""
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(repo_for(self.model), 'config.json'), str)

    def device(self) -> str:
        import torch
        if self._device:
            return self._device
        return 'cuda' if torch.cuda.is_available() else 'cpu'

    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        import transformers
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
        # transformers narrates every generate() call; the useful warnings here
        # are ours, and they are in the result.
        transformers.logging.set_verbosity_error()
        repo, device = repo_for(self.model), self.device()
        dtype = torch.float16 if device.startswith('cuda') else torch.float32
        self._processor = WhisperProcessor.from_pretrained(repo)
        self._model = WhisperForConditionalGeneration.from_pretrained(
            repo, dtype=dtype).to(device).eval()
        self._dtype, self._on = dtype, device

    def transcribe(self, clips: List[np.ndarray], language: Optional[str] = None,
                   task: str = 'transcribe', **options: Any) -> List[Dict[str, Any]]:
        if not clips:
            return []
        self.load()
        import torch

        english_only = self.model.endswith('.en')
        kwargs: Dict[str, Any] = {'max_new_tokens': int(options.get('max_new_tokens', 220))}
        if not english_only:
            kwargs.update(task=task, language=language or None)

        results: List[Dict[str, Any]] = []
        for start in range(0, len(clips), self._batch):
            batch = clips[start:start + self._batch]
            began = time.time()
            features = self._processor(
                [np.asarray(c, dtype=np.float32) for c in batch],
                sampling_rate=RATE, return_tensors='pt').input_features
            with torch.no_grad():
                tokens = self._model.generate(
                    features.to(self._on, dtype=self._dtype), **kwargs)
            texts = self._processor.batch_decode(tokens, skip_special_tokens=True)
            spent = time.time() - began
            share = spent / max(len(batch), 1)
            for clip, text in zip(batch, texts):
                results.append({'text': clean(text), 'engine': self.name,
                                'model': self.model, 'seconds': round(share, 3),
                                'audio_s': round(float(clip.size) / RATE, 3),
                                'device': self._on, 'batch': len(batch)})
        return results
