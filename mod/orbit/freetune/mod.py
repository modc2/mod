import json
import os
import urllib.request

import mod as m


class Mod:
    """freetune — CPU LoRA finetuning of Qwen over a directory of code.

    The heavy lifting lives in the Rust API (src/api) + Next app (src/app); this
    Python module is the mod-protocol entry point and a thin CLI over the API.
    A null/default call returns module info (mod protocol convention).
    """

    description = "CPU LoRA finetuning of Qwen over a directory of code (Rust API + Next app)."
    path = r'/root/mod/mod/orbit/freetune'
    api_url = os.environ.get("FREETUNE_API_URL", "http://localhost:50210")

    # ── mod protocol ─────────────────────────────────────────────────────────
    def forward(self, **kwargs):
        """Default entry point → module info."""
        return self.info()

    def info(self):
        return {
            'name': 'freetune',
            'description': self.description,
            'path': self.path,
            'api_url': self.api_url,
            'app': '/freetune',
            'api': '/api/freetune',
            'files': os.listdir(self.path),
        }

    def readme(self):
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None

    # ── direct (no running API needed) ──────────────────────────────────────────
    def models(self):
        """List selectable base models (from trainer/common.py)."""
        from trainer import common
        return json.loads(common.models_json())

    def scan(self, src):
        """Preview the training corpus for a code directory."""
        from trainer import dataset
        stats, _ = dataset.build(src, out=None)
        return stats

    # ── thin client over the running Rust API ──────────────────────────────────
    def _req(self, path, method="GET", body=None):
        url = f"{self.api_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read().decode())

    def train(self, src, model="Qwen/Qwen2.5-Coder-0.5B-Instruct", epochs=1,
              lora_r=8, block_size=512, threads=4, max_blocks=0, name=None):
        """Start a finetune run on the API. Returns {id}."""
        return self._req("/train", "POST", {
            "src": src, "model": model, "epochs": epochs, "lora_r": lora_r,
            "block_size": block_size, "threads": threads, "max_blocks": max_blocks,
            "name": name,
        })

    def runs(self):
        return self._req("/runs")

    def status(self, run_id):
        return self._req(f"/runs/{run_id}")

    def stop(self, run_id):
        return self._req(f"/runs/{run_id}/stop", "POST")

    def infer(self, prompt, model=None, run_id=None, max_new_tokens=200):
        body = {"prompt": prompt, "max_new_tokens": max_new_tokens}
        if run_id:
            body["run_id"] = run_id
        else:
            body["model"] = model or "Qwen/Qwen2.5-Coder-0.5B-Instruct"
        return self._req("/infer", "POST", body)

    def bench(self, model=None, run_id=None):
        body = {}
        if run_id:
            body["run_id"] = run_id
        else:
            body["model"] = model or "Qwen/Qwen2.5-Coder-0.5B-Instruct"
        return self._req("/bench", "POST", body)

    def metrics(self):
        """Live CPU/RAM + warm-worker stats."""
        return self._req("/metrics")
