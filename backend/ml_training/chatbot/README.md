# Chatbot fine-tuning & evaluation — RTX 5080 runbook (decision D-027)

**Status (2026-11-19): code built and CPU-tested; no weights downloaded, no GPU run yet.** The runtime chatbot in
`core_api` works today with `AI_LLM_PROVIDER=deterministic` (no model). This folder turns Qwen2.5-Instruct into a
better planner and phraser for it, on the university's **NVIDIA RTX 5080 (16 GB, Blackwell `sm_120`)**.

What the model learns is narrow on purpose: **choose the read-only tools** for a question (and the context of a
follow-up), **refuse** accusations, identity claims, state-changing requests and uploads, and **rephrase a grounded
answer** without adding a number. Every number in production still comes from SQL under the caller's Row-Level
Security, and `assistant_engine` rejects any model output that adds a number or names a culprit.

| File | Purpose |
|---|---|
| `../configs/chatbot_qwen25_7b_qlora.yaml` | **Primary**: Qwen/Qwen2.5-7B-Instruct, 4-bit NF4 QLoRA, bf16 compute, LoRA r=16 / α=32 on all attention + MLP projections, gradient checkpointing, batch 4 × accumulation 4; ≈ 11–12 GB peak |
| `../configs/chatbot_qwen25_14b_qlora.yaml` | Optional: Qwen/Qwen2.5-14B-Instruct, same QLoRA, batch 1 × 16; ≈ 15 GB (dedicated runs) |
| `dataset_builder.py` | train / validation / test JSONL from the runtime's tool catalogue and prompts: planner (all 6 capability areas, multi-tool), follow-ups with context, refusals, safety (no accusation), missing-data and grounded phrasing; hashed splits (no question shared between splits); `export-logs` scrubs real chat logs (`pii.py`) |
| `train_chatbot_qlora.py` | `prepare` (no GPU: validate config, build data, render ChatML, length check) and `train` (bitsandbytes 4-bit + PEFT LoRA + TRL `SFTTrainer`, loss on the answer only, eval every `eval_steps`, best checkpoint by validation loss, fixed seeds, `summary.json`) |
| `evaluate_chatbot.py` | metrics on the test split for `deterministic`, `ollama`, `openai_compatible`, `hf` (base + adapter) or a predictions file |
| `metrics.py` | tool selection, argument (date / place / filter) accuracy after the runtime's own normalisation, follow-up accuracy, refusal accuracy, safety compliance, numeric faithfulness and completeness, citation grounding, missing-data refusal, JSON validity |
| `export_to_ollama.py` | merge the adapter → GGUF (llama.cpp) → quantize → Ollama `Modelfile` |

## 1. Environment (once)

```bash
nvidia-smi                       # driver ≥ 570 (Blackwell); CUDA 12.8 or newer
cd backend/ml_training
python3.11 -m venv .venv && source .venv/bin/activate
# PyTorch built for CUDA 12.8+ (cu128 wheels or newer). Older cu12.4 builds have no sm_120 kernels for the RTX 5080.
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements-chatbot.txt
pip install -e ../services/ai_rag_and_agent          # the runtime engine: same tools, prompts and guardrails
python -c "import torch; print(torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0), torch.cuda.is_bf16_supported())"
# expect: NVIDIA GeForce RTX 5080 (12, 0) True
python -m pytest -q tests/test_chatbot_pipeline.py   # CPU checks before any download
```

`bitsandbytes` must be a release with Blackwell support (0.46 or newer). `huggingface-cli login` is only needed if the
model download requires it; the Qwen2.5 weights are Apache-2.0 (7B) / Qwen licence (check the model card for 14B).

## 2. Data

```bash
python -m chatbot.dataset_builder build --config configs/chatbot_qwen25_7b_qlora.yaml   # → datasets/chatbot/
```

6,000 / 600 / 600 records (planner 45 %, follow-up 15 %, refusal 10 %, safety 10 %, missing-data 10 %, phrasing
10 %), built in a few seconds with `dataset.now` fixed so that "last month" means the same dates everywhere.
Later, real usage can be added: export questions and answers from `ai_chat_messages` (Admin-approved), then

```bash
python -m chatbot.dataset_builder export-logs --input logs.jsonl --names staff_names.txt --output real_scrubbed.jsonl
```

which removes emails, phone numbers, CNICs, tokens, internal IDs, street addresses and the listed people's names. Have
a person review real records before training on them.

## 3. Train (RTX 5080)

```bash
python -m chatbot.train_chatbot_qlora prepare --config configs/chatbot_qwen25_7b_qlora.yaml
python -m chatbot.train_chatbot_qlora train   --config configs/chatbot_qwen25_7b_qlora.yaml
# 14B (optional): close the desktop session first
python -m chatbot.train_chatbot_qlora train   --config configs/chatbot_qwen25_14b_qlora.yaml
# out of memory: model.max_seq_length=1536 or train.per_device_train_batch_size=2 train.gradient_accumulation_steps=8
```

Output: `experiments/runs/<name>/adapter` (LoRA weights + tokenizer), `checkpoints/`, `config.yaml`, `summary.json`
(losses, peak VRAM, GPU, CUDA, PyTorch versions, commit). Record the run in `experiments/model_registry.md`.

## 4. Evaluate

```bash
python -m chatbot.evaluate_chatbot --data datasets/chatbot/test.jsonl --backend deterministic \
    --output experiments/runs/baseline_deterministic/metrics_test.json
python -m chatbot.evaluate_chatbot --data datasets/chatbot/test.jsonl --backend hf \
    --model Qwen/Qwen2.5-7B-Instruct --adapter experiments/runs/chatbot_qwen25_7b_qlora/adapter \
    --output experiments/runs/chatbot_qwen25_7b_qlora/metrics_test.json
```

Measured on CPU on 2026-11-19, the **deterministic baseline** (the runtime's rule planner) on the 600-record test
split: tool selection **0.925**, argument accuracy **0.914**, follow-up accuracy **0.933**, refusal accuracy **1.0**,
safety compliance **1.0**, numeric faithfulness / completeness **1.0**, citation grounding **1.0**, missing-data
refusal **1.0**, JSON validity **1.0**. A fine-tuned model is worth deploying only if it beats the baseline on tool
selection and argument accuracy **and** keeps every safety and grounding metric at 1.0. These are synthetic-data
figures, not a claim about real campus questions.

## 5. Deploy to Ollama (`AI_LLM_PROVIDER=ollama`)

```bash
RUN=experiments/runs/chatbot_qwen25_7b_qlora
python -m chatbot.export_to_ollama merge --base Qwen/Qwen2.5-7B-Instruct --adapter $RUN/adapter --out $RUN/merged
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp && cmake -S ~/llama.cpp -B ~/llama.cpp/build && \
    cmake --build ~/llama.cpp/build -j --target llama-quantize && pip install -r ~/llama.cpp/requirements.txt
python -m chatbot.export_to_ollama gguf --merged $RUN/merged --llama-cpp ~/llama.cpp --quant Q4_K_M --out $RUN/gguf
python -m chatbot.export_to_ollama modelfile --gguf $RUN/gguf/model-Q4_K_M.gguf --out $RUN/gguf/Modelfile
cd $RUN/gguf && ollama create cctv-assistant -f Modelfile
python -m chatbot.evaluate_chatbot --data datasets/chatbot/test.jsonl --backend ollama --model cctv-assistant
```

Then in `backend/.env`: `AI_LLM_PROVIDER=ollama`, `AI_LLM_BASE_URL=http://127.0.0.1:11434`,
`AI_LLM_MODEL=cctv-assistant`. Without fine-tuning, `ollama pull qwen2.5:7b` and `AI_LLM_MODEL=qwen2.5:7b` work too.
The web page's provider badge and `GET /api/v1/assistant/status` show whether the model is reachable; if it is not,
answers still come from the deterministic engine and say so.
