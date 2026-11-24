"""QLoRA fine-tuning of Qwen2.5-Instruct for the chatbot on one RTX 5080 (16 GB) — decision D-027.

    python -m chatbot.train_chatbot_qlora prepare --config configs/chatbot_qwen25_7b_qlora.yaml
    python -m chatbot.train_chatbot_qlora train   --config configs/chatbot_qwen25_7b_qlora.yaml [key=value ...]

``prepare`` needs no GPU and no download: it validates the config, builds the dataset if missing, renders sample
records in the exact ChatML format and estimates sequence lengths. ``train`` loads the base model in 4-bit NF4
(bitsandbytes), adds LoRA adapters (PEFT) to every attention and MLP projection, and trains with TRL's SFTTrainer on
the assistant turns only, evaluating on the validation split every ``eval_steps`` and keeping the best checkpoint by
validation loss. Seeds are fixed (Python, NumPy, PyTorch, transformers); bitsandbytes kernels are not bit-exact, so
two runs match closely, not exactly. Output: ``<project>/<name>/adapter`` + ``summary.json``.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chatbot import dataset_builder
from chatbot.formatting import approx_tokens, to_chatml
from data_utils.common import dump_config, load_config

REQUIRED = {
    "model": ("base", "max_seq_length"),
    "quantization": ("load_in_4bit", "bnb_4bit_quant_type", "bnb_4bit_compute_dtype"),
    "lora": ("r", "alpha", "dropout", "target_modules"),
    "train": ("epochs", "per_device_train_batch_size", "gradient_accumulation_steps", "learning_rate", "eval_steps"),
    "dataset": ("dir", "seed", "sizes", "mix", "now", "time_zone"),
}


def validate_config(config: dict[str, Any]) -> list[str]:
    """Problems with a chatbot config (empty when it is usable)."""
    problems = [
        f"{section}.{key} is missing"
        for section, keys in REQUIRED.items()
        for key in keys
        if key not in (config.get(section) or {})
    ]
    if problems:
        return problems
    q, lora, train = config["quantization"], config["lora"], config["train"]
    if q["bnb_4bit_quant_type"] not in ("nf4", "fp4"):
        problems.append("quantization.bnb_4bit_quant_type must be nf4 or fp4")
    if q["bnb_4bit_compute_dtype"] not in ("bfloat16", "float16"):
        problems.append("quantization.bnb_4bit_compute_dtype must be bfloat16 (RTX 5080) or float16")
    if not (1 <= int(lora["r"]) <= 256) or float(lora["alpha"]) <= 0:
        problems.append("lora.r must be 1..256 and lora.alpha positive")
    if not 256 <= int(config["model"]["max_seq_length"]) <= 32768:
        problems.append("model.max_seq_length must be 256..32768")
    if float(train["learning_rate"]) <= 0 or int(train["epochs"]) < 1:
        problems.append("train.learning_rate must be positive and train.epochs at least 1")
    if abs(sum(config["dataset"]["mix"].values()) - 1) > 1e-6:
        problems.append("dataset.mix must sum to 1")
    return problems


def ensure_dataset(config: dict[str, Any]) -> Path:
    data = config["dataset"]
    directory = Path(data["dir"])
    if not (directory / "train.jsonl").exists():
        now = datetime.fromisoformat(data["now"])
        built = dataset_builder.build(data["sizes"], data["mix"], int(data["seed"]), now, data["time_zone"])
        dataset_builder.write(
            built, directory, {"seed": data["seed"], "now": data["now"], "time_zone": data["time_zone"]}
        )
    return directory


def prepare(config: dict[str, Any]) -> dict[str, Any]:
    problems = validate_config(config)
    if problems:
        raise SystemExit("Invalid config:\n  - " + "\n  - ".join(problems))
    directory = ensure_dataset(config)
    lengths = {}
    longest = 0
    for split in dataset_builder.SPLITS:
        records = list(dataset_builder.read(directory / f"{split}.jsonl"))
        lengths[split] = len(records)
        longest = max([longest, *(approx_tokens(to_chatml(r["messages"])) for r in records)])
    limit = int(config["model"]["max_seq_length"])
    sample = next(dataset_builder.read(directory / "train.jsonl"))
    return {
        "records": lengths,
        "longest_tokens_estimate": longest,
        "max_seq_length": limit,
        "fits": longest <= limit,
        "sample": to_chatml(sample["messages"]),
    }


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def train(config: dict[str, Any]) -> Path:  # pragma: no cover - needs a CUDA GPU and the model download
    import torch  # noqa: PLC0415
    from peft import LoraConfig  # noqa: PLC0415
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed  # noqa: PLC0415
    from trl import SFTConfig, SFTTrainer  # noqa: PLC0415

    from datasets import load_dataset  # noqa: PLC0415

    summary = prepare(config)
    if not torch.cuda.is_available():
        raise SystemExit("No CUDA GPU found. QLoRA training needs the RTX 5080 (PyTorch built for CUDA 12.8+).")
    seed = int(config.get("seed", 2027))
    set_seed(seed)
    m, q, lora, t = config["model"], config["quantization"], config["lora"], config["train"]
    run_dir = Path(config.get("project", "experiments/runs")) / config["name"]
    run_dir.mkdir(parents=True, exist_ok=True)
    dump_config(config, run_dir / "config.yaml")
    compute = getattr(torch, q["bnb_4bit_compute_dtype"])
    tokenizer = AutoTokenizer.from_pretrained(m["base"], revision=m.get("revision", "main"))
    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        m["base"],
        revision=m.get("revision", "main"),
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=bool(q["load_in_4bit"]),
            bnb_4bit_quant_type=q["bnb_4bit_quant_type"],
            bnb_4bit_compute_dtype=compute,
            bnb_4bit_use_double_quant=bool(q.get("bnb_4bit_use_double_quant", True)),
        ),
        torch_dtype=compute,
        attn_implementation=m.get("attn_implementation", "sdpa"),
        device_map={"": 0},
    )
    directory = Path(config["dataset"]["dir"])
    data = load_dataset(
        "json", data_files={"train": str(directory / "train.jsonl"), "validation": str(directory / "validation.jsonl")}
    )
    data = data.map(
        lambda r: {"messages": r["messages"]}, remove_columns=[c for c in data["train"].column_names if c != "messages"]
    )
    args = SFTConfig(
        output_dir=str(run_dir / "checkpoints"),
        num_train_epochs=int(t["epochs"]),
        per_device_train_batch_size=int(t["per_device_train_batch_size"]),
        per_device_eval_batch_size=int(t.get("per_device_eval_batch_size", 1)),
        gradient_accumulation_steps=int(t["gradient_accumulation_steps"]),
        learning_rate=float(t["learning_rate"]),
        lr_scheduler_type=t.get("lr_scheduler", "cosine"),
        warmup_ratio=float(t.get("warmup_ratio", 0.03)),
        weight_decay=float(t.get("weight_decay", 0.0)),
        max_grad_norm=float(t.get("max_grad_norm", 0.3)),
        bf16=bool(t.get("bf16", True)),
        gradient_checkpointing=bool(t.get("gradient_checkpointing", True)),
        optim=t.get("optim", "paged_adamw_8bit"),
        logging_steps=int(t.get("logging_steps", 10)),
        eval_strategy="steps",
        eval_steps=int(t["eval_steps"]),
        save_strategy="steps",
        save_steps=int(t.get("save_steps", t["eval_steps"])),
        save_total_limit=int(t.get("save_total_limit", 3)),
        load_best_model_at_end=bool(t.get("load_best_model_at_end", True)),
        metric_for_best_model=t.get("metric_for_best_model", "eval_loss"),
        greater_is_better=False,
        max_length=int(m["max_seq_length"]),
        packing=bool(t.get("packing", False)),
        completion_only_loss=bool(t.get("completion_only_loss", True)),
        seed=seed,
        data_seed=seed,
        report_to=[],
    )
    peft_config = LoraConfig(
        r=int(lora["r"]),
        lora_alpha=float(lora["alpha"]),
        lora_dropout=float(lora["dropout"]),
        bias=lora.get("bias", "none"),
        target_modules=list(lora["target_modules"]),
        task_type="CAUSAL_LM",
    )
    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=data["train"],
        eval_dataset=data["validation"],
        processing_class=tokenizer,
        peft_config=peft_config,
    )
    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    result = trainer.train()
    metrics = trainer.evaluate()
    trainer.model.save_pretrained(run_dir / "adapter")
    tokenizer.save_pretrained(run_dir / "adapter")
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "name": config["name"],
                "base": m["base"],
                "finished_at": datetime.now(UTC).isoformat(),
                "seconds": round(time.time() - started),
                "train_loss": result.training_loss,
                "eval": metrics,
                "records": summary["records"],
                "gpu": torch.cuda.get_device_name(0),
                "capability": torch.cuda.get_device_capability(0),
                "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                "torch": torch.__version__,
                "cuda": torch.version.cuda,
                "python": platform.python_version(),
                "commit": _git_commit(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return run_dir / "adapter"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["prepare", "train"])
    parser.add_argument("--config", required=True)
    parser.add_argument("overrides", nargs="*", help="key.subkey=value")
    args = parser.parse_args(argv)
    config = load_config(args.config, args.overrides)
    if args.command == "prepare":
        report = prepare(config)
        print(json.dumps({k: v for k, v in report.items() if k != "sample"}, indent=2))
        print("--- sample record (ChatML) ---\n" + report["sample"][:1500])
        return 0 if report["fits"] else 1
    print(f"adapter saved to {train(config)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
