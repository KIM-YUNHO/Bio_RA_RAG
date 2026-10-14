#!/usr/bin/env python3
"""Report whether this environment can run the RAG PoC: package versions, GPU, one embedding on the GPU."""
import argparse
import importlib
import json
import sys
import time

p = argparse.ArgumentParser()
p.add_argument("--model", default="BAAI/bge-m3", help="Hugging Face model used for the one-off embedding test")
p.add_argument("--skip-embedding", action="store_true", help="Do not download or run a model")
a = p.parse_args()
sys.stdout.reconfigure(encoding="utf-8")

out = {"python": sys.version.split()[0], "packages": {}, "import_errors": {}}
# import name -> what it is used for
for name in ("torch", "transformers", "sentence_transformers", "qdrant_client", "openai", "numpy",
             "fitz", "pdfplumber", "PIL", "onnxruntime", "rapidocr", "rapid_table"):
    try:
        out["packages"][name] = getattr(importlib.import_module(name), "__version__", "imported")
    except Exception as e:
        out["import_errors"][name] = f"{type(e).__name__}: {e}"

import torch

out["cuda_available"] = torch.cuda.is_available()
if out["cuda_available"]:
    out["torch_cuda_build"] = torch.version.cuda
    out["gpu"] = torch.cuda.get_device_name(0)
    out["gpu_memory_mib"] = torch.cuda.get_device_properties(0).total_memory // 2**20

if not a.skip_embedding:
    from transformers import AutoModel, AutoTokenizer

    device = "cuda" if out["cuda_available"] else "cpu"
    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModel.from_pretrained(a.model).to(device).eval()
    texts = ["geometric mean ratio of Cmax and 90% CI", "anti-drug antibody incidence"]
    t = time.time()
    with torch.no_grad():
        enc = tok(texts, truncation=True, padding=True, return_tensors="pt", max_length=64).to(device)
        emb = model(**enc).last_hidden_state[:, 0, :]
    out["embedding"] = {"model": a.model, "device": str(emb.device), "shape": list(emb.shape),
                        "seconds": round(time.time() - t, 2),
                        "gpu_memory_used_mib": torch.cuda.memory_allocated() // 2**20 if out["cuda_available"] else None}

print(json.dumps(out, ensure_ascii=False, indent=2))
ok = not out["import_errors"] and out["cuda_available"] and (a.skip_embedding or out["embedding"]["device"].startswith("cuda"))
raise SystemExit(0 if ok else 1)
