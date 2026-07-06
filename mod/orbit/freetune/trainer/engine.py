"""Model load + measured generation, shared by infer.py (chat) and bench.py.

Kept separate so both the interactive worker and the benchmark report the SAME
usage numbers: prompt/completion tokens, wall latency, tokens/sec, and peak RSS.
"""
import os
import time


def _set_threads(threads: int):
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def rss_mb() -> float:
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 1e6, 1)
    except Exception:
        return 0.0


class Engine:
    def __init__(self, model_id: str, adapter: str | None = None, threads: int = 4):
        _set_threads(threads)
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        torch.set_num_threads(threads)
        self.torch = torch
        self.model_id = model_id
        self.adapter = adapter

        t0 = time.time()
        self.tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        model = AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype=torch.float32, trust_remote_code=True
        )
        if adapter:
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, adapter)
        model.eval()
        self.model = model
        self.load_s = round(time.time() - t0, 2)

    def generate(self, prompt: str, max_new_tokens: int = 200,
                 temperature: float = 0.2) -> dict:
        torch = self.torch
        # Use the chat template so Instruct models behave as expected.
        messages = [{"role": "user", "content": prompt}]
        try:
            text = self.tok.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        except Exception:
            text = prompt
        inputs = self.tok(text, return_tensors="pt")
        prompt_tokens = int(inputs["input_ids"].shape[1])

        t0 = time.time()
        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=temperature > 0,
                temperature=max(temperature, 1e-4),
                top_p=0.95,
                pad_token_id=self.tok.pad_token_id,
            )
        latency = time.time() - t0
        gen_ids = out[0][prompt_tokens:]
        completion_tokens = int(gen_ids.shape[0])
        completion = self.tok.decode(gen_ids, skip_special_tokens=True)
        return {
            "text": completion,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_s": round(latency, 3),
            "tokens_per_s": round(completion_tokens / latency, 2) if latency > 0 else 0,
            "peak_rss_mb": rss_mb(),
            "model": self.model_id,
            "adapter": bool(self.adapter),
        }
