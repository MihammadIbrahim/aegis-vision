"""Evaluate a chatbot model (or the deterministic baseline) on the test split (decision D-027).

Backends:
* ``deterministic``        the runtime's rule-based planner (the baseline every model must beat); phrasing = facts
* ``ollama``               a local Ollama model, e.g. ``--model cctv-assistant`` after ``export_to_ollama``
* ``openai_compatible``    any /v1/chat/completions endpoint (vLLM, SGLang) with ``--base-url``
* ``predictions``          a JSONL of ``{"id", "output"}`` produced elsewhere
* ``hf``                   transformers + the PEFT adapter (``--model`` base, ``--adapter`` dir; GPU)

    python -m chatbot.evaluate_chatbot --data datasets/chatbot/test.jsonl --backend deterministic
    python -m chatbot.evaluate_chatbot --data datasets/chatbot/test.jsonl --backend ollama --model cctv-assistant \
        --output experiments/runs/chatbot_qwen25_7b_qlora/metrics_test.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from assistant_engine import build_provider, planner
from assistant_engine.providers import LLMUnavailable

from chatbot import world
from chatbot.dataset_builder import read
from chatbot.metrics import Scorer


def deterministic_output(record: dict[str, Any], now: datetime, tz: str) -> Any:
    if record["task"] in ("phrasing", "missing_data"):
        return record["gold"]["facts"]
    vocab = world.vocabulary(now, tz)
    plan = planner.plan(record["question"], record["gold"].get("context") or None, vocab)
    return [] if plan.refusal else plan.calls


async def model_outputs(records: list[dict[str, Any]], backend: str, args: argparse.Namespace) -> dict[str, str]:
    provider = build_provider(
        backend,
        base_url=args.base_url,
        model_name=args.model,
        api_key=args.api_key,
        timeout_seconds=args.timeout,
        allow_external=args.allow_external,
    )
    outputs: dict[str, str] = {}
    try:
        for record in records:
            prompt = record["messages"][:-1]
            try:
                if record["task"] in ("phrasing", "missing_data"):
                    outputs[record["id"]] = await provider.chat_text(prompt)
                else:
                    outputs[record["id"]] = json.dumps(await provider.chat_json(prompt))
            except LLMUnavailable as error:
                outputs[record["id"]] = f"<error: {error}>"
    finally:
        await provider.aclose()
    return outputs


def hf_outputs(records: list[dict[str, Any]], base: str, adapter: str | None, max_new_tokens: int) -> dict[str, str]:
    import torch  # noqa: PLC0415 - heavy, GPU-only path
    from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415

    tokenizer = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16, device_map="auto")
    if adapter:
        from peft import PeftModel  # noqa: PLC0415

        model = PeftModel.from_pretrained(model, adapter)
    model.eval()
    outputs: dict[str, str] = {}
    for record in records:
        text = tokenizer.apply_chat_template(record["messages"][:-1], tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        outputs[record["id"]] = tokenizer.decode(generated[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True)
    return outputs


def evaluate(records: list[dict[str, Any]], outputs: dict[str, Any], now: datetime, tz: str) -> dict[str, Any]:
    scorer = Scorer(world.vocabulary(now, tz))
    for record in records:
        scorer.score(record, outputs[record["id"]])
    return scorer.report()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, type=Path, help="test.jsonl from dataset_builder")
    parser.add_argument(
        "--backend",
        default="deterministic",
        choices=["deterministic", "ollama", "openai_compatible", "predictions", "hf"],
    )
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--adapter")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    records = list(read(args.data))[: args.limit]
    meta = json.loads((args.data.parent / "meta.json").read_text(encoding="utf-8"))
    now, tz = datetime.fromisoformat(meta["now"]), meta["time_zone"]
    if args.backend == "deterministic":
        outputs: dict[str, Any] = {r["id"]: deterministic_output(r, now, tz) for r in records}
    elif args.backend == "predictions":
        outputs = {p["id"]: p["output"] for p in read(args.predictions)}
    elif args.backend == "hf":
        outputs = hf_outputs(records, args.model, args.adapter, args.max_new_tokens)
    else:
        outputs = asyncio.run(model_outputs(records, args.backend, args))
    report = {
        "backend": args.backend,
        "model": args.model,
        "records": len(records),
        "metrics": evaluate(records, outputs, now, tz),
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
