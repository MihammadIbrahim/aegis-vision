"""Deploy a fine-tuned chatbot adapter into local Ollama (decision D-027; ``AI_LLM_PROVIDER=ollama``).

    python -m chatbot.export_to_ollama merge --base Qwen/Qwen2.5-7B-Instruct --adapter <run>/adapter \
        --out <run>/merged
    python -m chatbot.export_to_ollama gguf --merged <run>/merged --llama-cpp ~/llama.cpp --quant Q4_K_M \
        --out <run>/gguf
    python -m chatbot.export_to_ollama modelfile --gguf <run>/gguf/model-Q4_K_M.gguf --out <run>/gguf/Modelfile
    ollama create cctv-assistant -f <run>/gguf/Modelfile      # then AI_LLM_MODEL=cctv-assistant in backend/.env

``merge`` folds the LoRA weights into the bf16 base (PEFT ``merge_and_unload``); ``gguf`` converts it with llama.cpp's
``convert_hf_to_gguf.py`` and quantizes with ``llama-quantize``; ``modelfile`` writes an Ollama Modelfile with the
Qwen2.5 ChatML template, temperature 0 and the context length of the config. Only ``modelfile`` runs without a GPU,
the model files or llama.cpp.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

# Ollama's Go template for Qwen2.5 ChatML (system, user and assistant turns; the runtime sends its own system text).
CHATML_TEMPLATE = """{{- range .Messages }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end }}<|im_start|>assistant
"""


def modelfile(gguf: Path, *, num_ctx: int = 4096, temperature: float = 0.0) -> str:
    if not 512 <= num_ctx <= 32768:
        raise ValueError("num_ctx must be 512..32768")
    return (
        f"# AI Investigation & Security Analytics Chatbot (decision D-027) — fine-tuned Qwen2.5, read-only planner.\n"
        f"FROM ./{gguf.name}\n"
        f'TEMPLATE """{CHATML_TEMPLATE}"""\n'
        f"PARAMETER temperature {temperature}\n"
        f"PARAMETER num_ctx {num_ctx}\n"
        f"PARAMETER seed 7\n"
        'PARAMETER stop "<|im_end|>"\n'
        'PARAMETER stop "<|im_start|>"\n'
    )


def merge(base: str, adapter: Path, out: Path) -> Path:  # pragma: no cover - needs the model download
    import torch  # noqa: PLC0415
    from peft import PeftModel  # noqa: PLC0415
    from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415

    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16, device_map="cpu")
    merged = PeftModel.from_pretrained(model, str(adapter)).merge_and_unload()
    out.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(out, safe_serialization=True)
    AutoTokenizer.from_pretrained(str(adapter)).save_pretrained(out)
    return out


def gguf_commands(merged: Path, llama_cpp: Path, quant: str, out: Path) -> list[list[str]]:
    f16 = out / "model-f16.gguf"
    return [
        ["python", str(llama_cpp / "convert_hf_to_gguf.py"), str(merged), "--outfile", str(f16), "--outtype", "f16"],
        [str(llama_cpp / "build" / "bin" / "llama-quantize"), str(f16), str(out / f"model-{quant}.gguf"), quant],
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--base", required=True)
    m.add_argument("--adapter", required=True, type=Path)
    m.add_argument("--out", required=True, type=Path)
    g = sub.add_parser("gguf")
    g.add_argument("--merged", required=True, type=Path)
    g.add_argument("--llama-cpp", required=True, type=Path)
    g.add_argument("--quant", default="Q4_K_M")
    g.add_argument("--out", required=True, type=Path)
    f = sub.add_parser("modelfile")
    f.add_argument("--gguf", required=True, type=Path)
    f.add_argument("--out", required=True, type=Path)
    f.add_argument("--num-ctx", type=int, default=4096)
    args = parser.parse_args(argv)
    if args.command == "merge":
        print(f"merged model → {merge(args.base, args.adapter, args.out)}")
    elif args.command == "gguf":
        args.out.mkdir(parents=True, exist_ok=True)
        for command in gguf_commands(args.merged, args.llama_cpp, args.quant, args.out):
            subprocess.run(command, check=True)
        print(f"GGUF → {args.out}")
    else:
        args.out.write_text(modelfile(args.gguf, num_ctx=args.num_ctx), encoding="utf-8")
        print(f"Modelfile → {args.out}\nnext: ollama create cctv-assistant -f {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
