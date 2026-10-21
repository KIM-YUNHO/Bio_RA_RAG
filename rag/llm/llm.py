#!/usr/bin/env python3
"""LLM client for query translation and answer generation; provider and model come from .env."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = ("openai", "ollama")


def load_env(path=ROOT / ".env"):
    """Read KEY=VALUE lines from .env; variables already set in the environment win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        # An empty OPENAI_BASE_URL in the environment makes the SDK connect to "" instead of its default.
        if value:
            os.environ.setdefault(key.strip(), value)


def settings(provider=None):
    load_env()
    provider = (provider or os.environ.get("LLM_PROVIDER") or "openai").lower()
    if provider not in PROVIDERS:
        raise SystemExit(f"LLM_PROVIDER must be one of {PROVIDERS}, got {provider!r}")
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise SystemExit(f"OPENAI_API_KEY is empty. Put the key in {ROOT / '.env'} (see .env.example).")
        base_url = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        extra = {}
    else:
        # Ollama serves an OpenAI-compatible API; the key is ignored but the SDK requires one.
        api_key = "ollama"
        base_url = os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
        model = os.environ.get("OLLAMA_MODEL") or "qwen3.5:9b"
        # qwen3.5 thinks by default and used its whole 4,041-token budget on reasoning, leaving the reply empty.
        extra = {"reasoning_effort": os.environ.get("OLLAMA_REASONING_EFFORT") or "none"}
    return {
        "provider": provider, "api_key": api_key, "base_url": base_url, "model": model, "extra": extra,
        "temperature": float(os.environ.get("LLM_TEMPERATURE") or 0),
        "seed": int(os.environ.get("LLM_SEED") or 0),
        "timeout": float(os.environ.get("LLM_TIMEOUT") or 120),
    }


def chat(messages, json_schema=None, provider=None, model=None):
    """Send one chat request. json_schema={"name":..., "schema":...} forces a JSON reply of that shape.

    Returns the reply text plus the model snapshot and token usage the server reported,
    so each run can record which model version produced it.
    """
    from openai import OpenAI

    s = settings(provider)
    client = OpenAI(api_key=s["api_key"], base_url=s["base_url"], timeout=s["timeout"], max_retries=3)
    kwargs = {"model": model or s["model"], "messages": messages, "temperature": s["temperature"], "seed": s["seed"], **s["extra"]}
    if json_schema:
        kwargs["response_format"] = {"type": "json_schema", "json_schema": {**json_schema, "strict": True}}
    r = client.chat.completions.create(**kwargs)
    usage = r.usage
    text = r.choices[0].message.content or ""
    if not text:
        raise RuntimeError(f"{s['provider']}:{kwargs['model']} returned an empty reply (finish_reason={r.choices[0].finish_reason})")
    return {
        "text": text,
        "finish_reason": r.choices[0].finish_reason,
        "provider": s["provider"],
        "model_requested": kwargs["model"],
        "model_reported": r.model,
        "system_fingerprint": getattr(r, "system_fingerprint", None),
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="Send one small request and print the reply, model and token counts")
    p.add_argument("--provider", choices=PROVIDERS, help="Override LLM_PROVIDER for this run")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not a.check:
        s = settings(a.provider)
        print(json.dumps({k: v for k, v in s.items() if k != "api_key"}, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    schema = {"name": "check", "schema": {"type": "object", "properties": {"english": {"type": "string"}},
                                           "required": ["english"], "additionalProperties": False}}
    out = chat([{"role": "system", "content": "Translate the Korean question into English. Keep abbreviations, numbers and units unchanged."},
                {"role": "user", "content": "SB17 1상에서 Cmax의 기하평균비와 90% 신뢰구간은?"}], json_schema=schema, provider=a.provider)
    out["parsed"] = json.loads(out["text"])
    print(json.dumps(out, ensure_ascii=False, indent=2))
