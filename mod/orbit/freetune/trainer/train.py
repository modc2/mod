"""CPU LoRA finetuning of a Qwen model over a code corpus.

Driven entirely by a job config JSON written by the Rust API:

    python3 -m trainer.train --run-dir ~/.mod/freetune/runs/<id>

The run dir must contain `config.json`. This script:
  1. builds the dataset from `config.src` (via dataset.py),
  2. tokenizes + packs into fixed-length blocks,
  3. attaches a LoRA adapter and trains on CPU,
  4. streams progress to `progress.json` (the API polls it) and logs to stdout
     (the API redirects that to `train.log`),
  5. saves the adapter to `<run-dir>/adapter` on success.

Everything is wrapped so any failure lands in progress.json as status="error"
with the message — the API surfaces that verbatim instead of a silent crash.
"""
import argparse
import json
import os
import time
import traceback

from trainer.common import write_json, read_json
from trainer import dataset as ds


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    run = args.run_dir
    prog_path = os.path.join(run, "progress.json")
    cfg = read_json(os.path.join(run, "config.json"))
    if cfg is None:
        raise SystemExit("missing config.json in run dir")

    def progress(**kw):
        cur = read_json(prog_path, {}) or {}
        cur.update(kw)
        write_json(prog_path, cur)

    started = time.time()
    progress(status="preparing", step=0, total_steps=0, loss=None,
             started_at=started, model=cfg["model"], src=cfg["src"])

    try:
        run_training(run, cfg, progress, log)
    except Exception as e:  # noqa: BLE001 — surface everything to the UI
        log("ERROR: " + str(e))
        traceback.print_exc()
        progress(status="error", error=str(e), ended_at=time.time())
        raise SystemExit(1)


def run_training(run, cfg, progress, log):
    # Force single-process CPU + bounded threads so a training run doesn't peg
    # every core and starve the API/app. THREADS is a config knob.
    threads = int(cfg.get("threads", 4))
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    import torch
    from datasets import Dataset
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
        TrainerCallback,
    )
    from peft import LoraConfig, get_peft_model

    torch.set_num_threads(threads)

    model_id = cfg["model"]
    block = int(cfg.get("block_size", 512))
    epochs = float(cfg.get("epochs", 1))
    lr = float(cfg.get("learning_rate", 2e-4))
    rank = int(cfg.get("lora_r", 8))
    alpha = int(cfg.get("lora_alpha", 16))
    batch = int(cfg.get("batch_size", 1))
    grad_accum = int(cfg.get("grad_accum", 8))
    max_blocks = int(cfg.get("max_blocks", 0))  # 0 = use all

    # ── 1. Dataset ────────────────────────────────────────────────────────────
    log(f"building dataset from {cfg['src']}")
    progress(status="dataset")
    stats, records = ds.build(cfg["src"], out=os.path.join(run, "dataset.jsonl"))
    log(f"dataset: {stats['files']} files, ~{stats['approx_tokens']} tokens")
    progress(dataset=stats)
    if stats["files"] == 0:
        raise RuntimeError("no code files found under src — nothing to train on")

    # ── 2. Tokenize + pack into fixed-length blocks ─────────────────────────────
    log(f"loading tokenizer {model_id}")
    progress(status="tokenizing")
    tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    eos = tok.eos_token or ""
    big = eos.join(r["text"] for r in records)
    ids = tok(big, return_attention_mask=False)["input_ids"]
    blocks = [ids[i:i + block] for i in range(0, len(ids) - block + 1, block)]
    if not blocks:  # corpus smaller than one block — keep the single short block
        blocks = [ids] if ids else []
    if not blocks:
        raise RuntimeError("corpus tokenized to zero blocks")
    if max_blocks and len(blocks) > max_blocks:
        blocks = blocks[:max_blocks]
    log(f"packed into {len(blocks)} blocks of {block} tokens")
    train_ds = Dataset.from_dict({"input_ids": blocks})

    steps_per_epoch = max(1, len(blocks) // (batch * grad_accum))
    total_steps = max(1, int(steps_per_epoch * epochs))
    progress(total_steps=total_steps, blocks=len(blocks))

    # ── 3. Model + LoRA ─────────────────────────────────────────────────────────
    log(f"loading base model {model_id} (this downloads on first run)")
    progress(status="loading_model")
    model = AutoModelForCausalLM.from_pretrained(
        model_id, torch_dtype=torch.float32, trust_remote_code=True
    )
    model.config.use_cache = False
    lora = LoraConfig(
        r=rank, lora_alpha=alpha, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, lora)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    log(f"LoRA: {trainable:,} trainable / {total:,} total "
        f"({100 * trainable / total:.3f}%)")
    progress(trainable_params=trainable, total_params=total)

    collator = DataCollatorForLanguageModeling(tok, mlm=False)

    class ProgressCb(TrainerCallback):
        def on_log(self, a, state, control, logs=None, **kw):
            if logs and "loss" in logs:
                eta = None
                if state.global_step:
                    elapsed = time.time() - progress_t0[0]
                    per = elapsed / state.global_step
                    eta = per * (total_steps - state.global_step)
                progress(status="training", step=state.global_step,
                         total_steps=total_steps, loss=round(float(logs["loss"]), 4),
                         eta_seconds=round(eta) if eta else None)
                log(f"step {state.global_step}/{total_steps} loss={logs['loss']:.4f}")

    progress_t0 = [time.time()]

    targs = TrainingArguments(
        output_dir=os.path.join(run, "checkpoints"),
        per_device_train_batch_size=batch,
        gradient_accumulation_steps=grad_accum,
        num_train_epochs=epochs,
        learning_rate=lr,
        logging_steps=1,
        save_strategy="no",
        report_to=[],
        use_cpu=True,
        dataloader_num_workers=0,
    )
    trainer = Trainer(
        model=model, args=targs, train_dataset=train_ds,
        data_collator=collator, callbacks=[ProgressCb()],
    )

    log("training started")
    progress(status="training", step=0)
    progress_t0[0] = time.time()
    result = trainer.train()

    # ── 4. Save adapter ─────────────────────────────────────────────────────────
    adapter_dir = os.path.join(run, "adapter")
    model.save_pretrained(adapter_dir)
    tok.save_pretrained(adapter_dir)
    write_json(os.path.join(adapter_dir, "freetune_meta.json"), {
        "base_model": model_id, "lora_r": rank, "lora_alpha": alpha,
        "blocks": len(blocks), "block_size": block,
        "final_loss": round(float(result.training_loss), 4),
    })
    log(f"saved adapter → {adapter_dir}")
    progress(status="done", step=total_steps, total_steps=total_steps,
             final_loss=round(float(result.training_loss), 4),
             ended_at=time.time(), adapter=adapter_dir)


if __name__ == "__main__":
    main()
