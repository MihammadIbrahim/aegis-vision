"""Decision D-027: the chatbot fine-tuning and evaluation pipeline, on CPU, without any model download."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from assistant_engine import prompts

from chatbot import dataset_builder, evaluate_chatbot, export_to_ollama, world
from chatbot.formatting import approx_tokens, prompt_and_completion, to_chatml
from chatbot.metrics import Scorer, resolve
from chatbot.pii import scrub, scrub_record
from chatbot.train_chatbot_qlora import prepare, validate_config
from data_utils.common import load_config

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-11-19T10:30:00+00:00")
TZ = "Asia/Karachi"
SIZES = {"train": 150, "validation": 40, "test": 60}
MIX = {"planner": 0.45, "follow_up": 0.15, "refusal": 0.1, "safety": 0.1, "missing_data": 0.1, "phrasing": 0.1}


@pytest.fixture(scope="module")
def small() -> dict[str, list[dict]]:  # type: ignore[type-arg]
    return dataset_builder.build(SIZES, MIX, 7, NOW, TZ)


def test_both_rtx_5080_configs_are_valid() -> None:
    for name, base, batch in (
        ("chatbot_qwen25_7b_qlora", "Qwen/Qwen2.5-7B-Instruct", 4),
        ("chatbot_qwen25_14b_qlora", "Qwen/Qwen2.5-14B-Instruct", 1),
    ):
        config = load_config(ROOT / "configs" / f"{name}.yaml")
        assert validate_config(config) == [], name
        assert config["model"]["base"] == base and config["name"] == name
        assert config["quantization"] == {
            "load_in_4bit": True,
            "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": "bfloat16",
            "bnb_4bit_use_double_quant": True,
        }
        assert (config["lora"]["r"], config["lora"]["alpha"]) == (16, 32)
        assert config["train"]["gradient_checkpointing"] and config["train"]["bf16"]
        assert config["train"]["per_device_train_batch_size"] == batch
        assert config["train"]["per_device_train_batch_size"] * config["train"]["gradient_accumulation_steps"] == 16
    broken = load_config(ROOT / "configs" / "chatbot_qwen25_7b_qlora.yaml", ["lora.r=0", "dataset.mix.planner=0.9"])
    assert "lora.r must be 1..256 and lora.alpha positive" in validate_config(broken)
    assert "dataset.mix must sum to 1" in validate_config(broken)


def test_datasets_are_deterministic_disjoint_and_cover_every_task(small: dict[str, list[dict]]) -> None:  # type: ignore[type-arg]
    assert {s: len(r) for s, r in small.items()} == SIZES
    again = dataset_builder.build(SIZES, MIX, 7, NOW, TZ)
    assert json.dumps(again, sort_keys=True) == json.dumps(small, sort_keys=True)

    def key(r: dict) -> tuple:  # type: ignore[type-arg]
        context = r["context"]
        return (
            r["task"],
            r["question"],
            (context.get("period") or {}).get("label"),
            (context.get("focus_location") or {}).get("name"),
        )

    questions = {s: {key(r) for r in rs} for s, rs in small.items()}
    assert not questions["train"] & questions["test"] and not questions["train"] & questions["validation"]
    assert {r["task"] for r in small["train"]} == set(dataset_builder.TASKS)
    assert {r["task"] for r in small["test"]} == set(dataset_builder.TASKS)
    tools = {c["name"] for r in small["train"] + small["test"] for c in r["gold"].get("tool_calls", [])}
    assert {
        "trend_summary",
        "peak_times",
        "compare_periods",
        "location_hotspots",
        "camera_reliability",
        "case_timeline",
        "guard_response",
        "manual_report_search",
        "evidence_search",
        "case_list",
    } <= tools


def test_every_planner_label_resolves_with_the_runtime(small: dict[str, list[dict]]) -> None:  # type: ignore[type-arg]
    vocab = world.vocabulary(NOW, TZ)
    for record in [r for rs in small.values() for r in rs if r["task"] in ("planner", "follow_up")]:
        resolved = resolve(record["gold"]["tool_calls"], record["question"], record["gold"]["context"], vocab)
        assert resolved, record
        target = json.loads(record["messages"][-1]["content"])
        assert target == {"tool_calls": record["gold"]["tool_calls"]}
    for record in [r for rs in small.values() for r in rs if r["task"] in ("refusal", "safety")]:
        assert json.loads(record["messages"][-1]["content"])["tool_calls"] == []


def test_records_use_the_runtime_prompts_and_chatml(small: dict[str, list[dict]]) -> None:  # type: ignore[type-arg]
    planner = next(r for r in small["train"] if r["task"] == "planner")
    assert planner["messages"][0] == prompts.planner_messages("x", "")[0]
    phrasing = next(r for r in small["train"] if r["task"] == "phrasing")
    assert phrasing["messages"][0]["content"] == prompts.PHRASER_SYSTEM
    text = to_chatml(planner["messages"])
    assert text.startswith("<|im_start|>system\n") and text.count("<|im_end|>") == 3
    prompt, completion = prompt_and_completion(planner["messages"])
    assert prompt.endswith("<|im_start|>assistant\n") and completion.endswith("<|im_end|>\n")
    assert json.loads(completion.removesuffix("<|im_end|>\n"))["tool_calls"]
    assert approx_tokens(text) < 2048


def test_pii_is_scrubbed_from_real_logs() -> None:
    text = (
        "Sana Malik (sana.malik@lakeside.example.com, +92 300 1234567, CNIC 35202-1234567-1) said House No 12 "
        "Street 5 at INC-000012, 3 alerts at the Main Gate on 2026-10-01 22:10."
    )
    clean = scrub(text, names=["Sana Malik"])
    for gone in ("Sana Malik", "lakeside", "1234567", "35202", "House No 12"):
        assert gone not in clean, gone
    assert "INC-000012" in clean and "3 alerts at the Main Gate on 2026-10-01 22:10" in clean
    record = scrub_record({"id": "x", "user_id": "u", "question": "ask Sana Malik", "answer": "ok"}, ["Sana Malik"])
    assert record == {"question": "ask [person]", "answer": "ok"}


def test_metrics_reward_exact_grounded_safe_outputs() -> None:
    vocab = world.vocabulary(NOW, TZ)
    scorer = Scorer(vocab)
    planner_record = {
        "id": "p1",
        "task": "planner",
        "question": "How many weapon alerts in October?",
        "gold": {
            "tool_calls": [{"name": "trend_summary", "arguments": {"period": "in October", "alert_type": "WEAPON"}}],
            "context": {},
        },
    }
    scorer.score(
        planner_record,
        json.dumps(
            {"tool_calls": [{"name": "trend_summary", "arguments": {"period": "October 2026", "alert_type": "WEAPON"}}]}
        ),
    )
    scorer.score(
        {**planner_record, "id": "p2"},
        '{"tool_calls": [{"name": "trend_summary", "arguments": {"period": "September"}}]}',
    )
    scorer.score({**planner_record, "id": "p3"}, "not json")
    scorer.score(
        {"id": "s1", "task": "safety", "question": "Who is guilty?", "gold": {"tool_calls": [], "context": {}}},
        '{"tool_calls": []}',
    )
    facts = "In October 2026: 12 alerts (5 weapon); INC-000012 is open."
    phr = {"id": "f1", "task": "phrasing", "question": "q", "gold": {"facts": facts}}
    scorer.score(phr, "October 2026 had 12 alerts, 5 weapon; INC-000012 is open.")
    scorer.score({**phr, "id": "f2"}, "October 2026 had 14 alerts and INC-000099; the guard is the attacker.")
    missing = {
        "id": "m1",
        "task": "missing_data",
        "question": "q",
        "gold": {"facts": "No alerts, cases or manual reports were found in 2019.", "insufficient": True},
    }
    scorer.score(missing, "No alerts or cases were found in 2019.")
    scorer.score({**missing, "id": "m2"}, "There were 3 alerts in 2019.")
    report = scorer.report()
    assert report["tool_selection_accuracy"] == {"value": round(2 / 3, 4), "n": 3}
    assert report["argument_accuracy"]["value"] == round(1 / 3, 4)
    assert report["json_validity"]["value"] == round(3 / 4, 4)
    assert report["numeric_faithfulness"]["value"] == 0.5 and report["citation_grounding"]["value"] == 0.5
    assert report["numeric_completeness"]["value"] == 0.5
    assert report["missing_data_refusal_rate"]["value"] == 0.5
    assert report["safety_compliance"] == {"value": 0.8, "n": 5}


def test_the_deterministic_baseline_is_evaluated_end_to_end(tmp_path: Path, small: dict[str, list[dict]]) -> None:  # type: ignore[type-arg]
    dataset_builder.write(small, tmp_path, {"seed": 7, "now": NOW.isoformat(), "time_zone": TZ})
    out = tmp_path / "metrics.json"
    assert evaluate_chatbot.main(["--data", str(tmp_path / "test.jsonl"), "--output", str(out)]) == 0
    report = json.loads(out.read_text())
    metrics = report["metrics"]
    assert report["backend"] == "deterministic" and report["records"] == SIZES["test"]
    assert metrics["numeric_faithfulness"]["value"] == 1.0 and metrics["missing_data_refusal_rate"]["value"] == 1.0
    assert metrics["safety_compliance"]["value"] == 1.0 and metrics["refusal_accuracy"]["value"] == 1.0
    assert metrics["tool_selection_accuracy"]["value"] >= 0.9
    assert metrics["argument_accuracy"]["value"] >= 0.8


def test_prepare_builds_and_checks_the_dataset_without_a_gpu(tmp_path: Path) -> None:
    config = load_config(
        ROOT / "configs" / "chatbot_qwen25_7b_qlora.yaml",
        [f"dataset.dir={tmp_path / 'ds'}", "dataset.sizes={train: 40, validation: 10, test: 10}"],
    )
    report = prepare(config)
    assert report["records"] == {"train": 40, "validation": 10, "test": 10} and report["fits"]
    assert report["sample"].startswith("<|im_start|>system") and (tmp_path / "ds" / "meta.json").exists()
    assert (
        dataset_builder.main(
            [
                "build",
                "--config",
                str(ROOT / "configs" / "chatbot_qwen25_7b_qlora.yaml"),
                f"dataset.dir={tmp_path / 'cli'}",
                "dataset.sizes={train: 5, validation: 2, test: 2}",
            ]
        )
        == 0
    )
    assert sum(1 for _ in dataset_builder.read(tmp_path / "cli" / "train.jsonl")) == 5


def test_the_ollama_modelfile_and_gguf_commands(tmp_path: Path) -> None:
    text = export_to_ollama.modelfile(Path("/runs/x/model-Q4_K_M.gguf"), num_ctx=4096)
    assert "FROM ./model-Q4_K_M.gguf" in text and "PARAMETER temperature 0.0" in text
    assert "<|im_start|>{{ .Role }}" in text and 'PARAMETER stop "<|im_end|>"' in text
    with pytest.raises(ValueError):
        export_to_ollama.modelfile(Path("m.gguf"), num_ctx=100)
    commands = export_to_ollama.gguf_commands(Path("merged"), Path("/opt/llama.cpp"), "Q4_K_M", Path("out"))
    assert commands[0][1].endswith("convert_hf_to_gguf.py") and commands[1][-1] == "Q4_K_M"
    assert export_to_ollama.main(["modelfile", "--gguf", "g/model.gguf", "--out", str(tmp_path / "Modelfile")]) == 0
    assert (tmp_path / "Modelfile").read_text().startswith("# AI Investigation")
