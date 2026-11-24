"""Shared helpers for every training pipeline: configuration files, seeding, deterministic splits,
device and mixed-precision selection, checkpoints and run directories.

Nothing here knows about a particular model or dataset.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import torch
import yaml

Split = Literal["train", "val", "test"]


# --- Configuration -------------------------------------------------------------------------------


def load_config(path: Path | str, overrides: Sequence[str] = ()) -> dict[str, Any]:
    """A YAML file plus ``key.subkey=value`` overrides from the command line. Values are parsed as
    YAML, so ``epochs=50`` is an int and ``model.name=swin3d_t`` a string."""
    with Path(path).open(encoding="utf-8") as handle:
        config: dict[str, Any] = yaml.safe_load(handle) or {}
    for item in overrides:
        key, sep, raw = item.partition("=")
        if not sep:
            raise ValueError(f"override must look like key=value, got {item!r}")
        node = config
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError(f"cannot override {key}: {part} is not a mapping")
        node[parts[-1]] = yaml.safe_load(raw)
    return config


def dump_config(config: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


# --- Reproducibility -------------------------------------------------------------------------------


def seed_everything(seed: int, *, deterministic: bool = False) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    os.environ.setdefault("PYTHONHASHSEED", str(seed))
    if deterministic:
        # Exact repeatability costs speed; cuDNN autotuning is turned off.
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    else:
        torch.backends.cudnn.benchmark = True


def stable_split(key: str, ratios: Sequence[float], seed: int) -> int:
    """Which split (index into ``ratios``) an item belongs to, from a hash of its name and the seed.
    The same name always lands in the same split, independent of file order or machine, so
    train/val/test never overlap and never change between runs."""
    if abs(sum(ratios) - 1.0) > 1e-6 or any(r < 0 for r in ratios):
        raise ValueError("split ratios must be non-negative and sum to 1")
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    point = int.from_bytes(digest[:8], "big") / 2**64
    edge = 0.0
    for index, ratio in enumerate(ratios):
        edge += ratio
        if point < edge:
            return index
    return len(ratios) - 1


def split_items(items: Iterable[str], ratios: Sequence[float], seed: int) -> dict[Split, list[str]]:
    """Deterministic train/val/test assignment of item keys (e.g. video names or identities)."""
    names: tuple[Split, ...] = ("train", "val", "test")
    result: dict[Split, list[str]] = {name: [] for name in names[: len(ratios)]}
    for item in sorted(items):
        result[names[stable_split(item, ratios, seed)]].append(item)
    return result


# --- Device and mixed precision ------------------------------------------------------------------------


@dataclass(frozen=True)
class Compute:
    device: torch.device
    amp_dtype: torch.dtype | None  # None = full precision
    use_scaler: bool  # fp16 needs loss scaling; bf16 and fp32 do not

    @property
    def amp_enabled(self) -> bool:
        return self.amp_dtype is not None

    def describe(self) -> str:
        name = self.device.type
        if self.device.type == "cuda":
            name = f"cuda ({torch.cuda.get_device_name(self.device)})"
        precision = {None: "fp32", torch.bfloat16: "bf16 autocast", torch.float16: "fp16 autocast + GradScaler"}
        return f"{name}, {precision[self.amp_dtype]}"


def select_compute(device: str = "auto", precision: str = "auto") -> Compute:
    """``device``: auto | cuda | cuda:N | cpu. ``precision``: auto | bf16 | fp16 | fp32.
    ``auto`` precision is bf16 on GPUs that support it (Ampere and newer: RTX 30/40 series), fp16
    on older GPUs, and fp32 on CPU. Asking for cuda without CUDA is an error, never a silent fallback."""
    if device == "auto":
        resolved = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        resolved = torch.device(device)
        if resolved.type == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but no CUDA device is available.")
    if precision == "fp32":
        return Compute(resolved, None, False)
    if resolved.type == "cpu":
        # bf16 autocast works on CPU but is slower on most CPUs; keep fp32 unless asked explicitly.
        return Compute(resolved, torch.bfloat16 if precision == "bf16" else None, False)
    if precision == "auto":
        precision = "bf16" if torch.cuda.is_bf16_supported() else "fp16"
    if precision == "bf16":
        return Compute(resolved, torch.bfloat16, False)
    if precision == "fp16":
        return Compute(resolved, torch.float16, True)
    raise ValueError(f"unknown precision {precision!r}")


# --- Runs and checkpoints ----------------------------------------------------------------------------------


def git_commit(repo_hint: Path | None = None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_hint or Path.cwd(),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:  # not a git checkout, or git missing: the manifest records null
        return None


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def make_run_dir(root: Path | str, name: str) -> Path:
    """``root/name`` or, if it exists, ``root/name_2``, ``name_3`` …"""
    base = Path(root)
    candidate = base / name
    counter = 2
    while candidate.exists():
        candidate = base / f"{name}_{counter}"
        counter += 1
    candidate.mkdir(parents=True)
    return candidate


def save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_checkpoint(path: Path, *, model: torch.nn.Module, optimizer: Any, scheduler: Any, epoch: int, best_metric: float, config: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
    payload = {
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict() if optimizer is not None else None,
        "scheduler": scheduler.state_dict() if scheduler is not None else None,
        "epoch": epoch,
        "best_metric": best_metric,
        "config": config,
        "saved_at": utc_now(),
        **(extra or {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save(payload, tmp)
    tmp.replace(path)  # never leave a half-written checkpoint behind


def load_checkpoint(path: Path, map_location: str | torch.device = "cpu") -> dict[str, Any]:
    return torch.load(path, map_location=map_location, weights_only=False)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CosineWithWarmup(torch.optim.lr_scheduler.LambdaLR):
    """Linear warm-up for ``warmup_steps``, then cosine decay to ``final_factor`` of the base LR at
    ``total_steps``. Stepped once per optimizer step."""

    def __init__(self, optimizer: torch.optim.Optimizer, *, warmup_steps: int, total_steps: int, final_factor: float = 0.01) -> None:
        if total_steps < 1 or warmup_steps < 0 or warmup_steps > total_steps:
            raise ValueError("0 <= warmup_steps <= total_steps, total_steps >= 1")

        def factor(step: int) -> float:
            if step < warmup_steps:
                return (step + 1) / max(1, warmup_steps)
            progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
            progress = min(1.0, progress)
            return final_factor + (1 - final_factor) * 0.5 * (1 + float(np.cos(np.pi * progress)))

        super().__init__(optimizer, factor)
