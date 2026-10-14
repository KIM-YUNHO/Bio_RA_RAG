#!/usr/bin/env python3
"""Embed retrieval chunks with the selected BGE-M3 model and save the vectors.

One model per run so GPU memory is released between models:
    uv run python rag/embed_index.py --rag-dir data/rag/candidates/<build>
Vectors are stored exactly as the model's own usage prescribes (normalized or not); `similarity` in
meta.json says how to score them. The Embedder class is also what search uses to encode queries.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
RAG = ROOT / "data" / "rag"
DEFAULT_MODEL = "bge-m3"

# Other models remain available explicitly for reproducing historical experiments.
# kind: how the model's card says to pool and format inputs.
MODELS = {
    "medcpt": {
        "kind": "medcpt", "doc": "ncbi/MedCPT-Article-Encoder", "query": "ncbi/MedCPT-Query-Encoder",
        "max_doc": 512, "max_query": 64, "normalize": False, "batch": 64, "group": "biomedical",
    },
    "medte": {
        # GTE-base fine-tune; the card gives no pooling, so GTE's own convention is used: mean pooling, normalized.
        "kind": "mean", "doc": "MohammadKhodadad/MedTE-cl15-step-8000",
        "max_doc": 512, "max_query": 512, "normalize": True, "batch": 64, "group": "biomedical",
    },
    "bmretriever": {
        "kind": "last_token", "doc": "BMRetriever/BMRetriever-410M",
        "max_doc": 512, "max_query": 512, "normalize": False, "batch": 16, "half": True, "group": "biomedical",
        "query_task": "Given a question, retrieve relevant documents that best answer the question",
    },
    "bge-m3": {
        "kind": "st", "doc": "BAAI/bge-m3", "max_doc": 8192, "max_query": 512, "normalize": True, "batch": 8,
        "half": True, "group": "general",
    },
    "qwen3": {
        "kind": "st", "doc": "Qwen/Qwen3-Embedding-0.6B", "max_doc": 1024, "max_query": 512, "normalize": True,
        "batch": 16, "half": True, "group": "general", "query_prompt_name": "query",
        "processor_kwargs": {"padding_side": "left"},
    },
}


def load_chunks(rag_dir=None):
    path = Path(rag_dir or RAG) / "chunks.jsonl"
    chunks = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return chunks, hashlib.sha256(path.read_bytes()).hexdigest()


def last_token_pool(hidden, mask):
    # From the BMRetriever model card.
    hidden = hidden.masked_fill(~mask[..., None].bool(), 0.0)
    if mask[:, -1].sum() == mask.shape[0]:
        return hidden[:, -1]
    lengths = mask.sum(dim=1) - 1
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), lengths]


class Embedder:
    def __init__(self, key=DEFAULT_MODEL, device=None, local_only=False):
        self.key, self.cfg = key, MODELS[key]
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.half = bool(self.cfg.get("half")) and self.device == "cuda"
        self.truncated = 0
        self._query_model = None
        kind = self.cfg["kind"]
        if kind == "st":
            from sentence_transformers import SentenceTransformer

            kwargs = {"torch_dtype": torch.float16} if self.half else {}
            self.model = SentenceTransformer(self.cfg["doc"], device=self.device, model_kwargs=kwargs,
                                             processor_kwargs=self.cfg.get("processor_kwargs") or {}, local_files_only=local_only)
            self.tok = self.model.tokenizer
            self.revision = getattr(self.model[0].auto_model.config, "_commit_hash", None)
        else:
            from transformers import AutoModel, AutoTokenizer, logging

            logging.set_verbosity_error()
            self.tok = AutoTokenizer.from_pretrained(self.cfg["doc"], local_files_only=local_only)
            self.model = AutoModel.from_pretrained(self.cfg["doc"], local_files_only=local_only).to(self.device).eval()
            if self.half:
                self.model = self.model.half()
            if self.tok.pad_token is None:
                self.tok.pad_token = self.tok.eos_token
            self.revision = getattr(self.model.config, "_commit_hash", None)

    def _forward(self, model, tok, inputs, max_len, pool):
        """inputs: list of str, or list of [title, text] pairs."""
        with torch.no_grad():
            if pool == "last_token":
                enc = tok(inputs, max_length=max_len - 1, truncation=True, padding=False)
                ids = [x + [tok.eos_token_id] for x in enc["input_ids"]]
                batch = tok.pad({"input_ids": ids}, padding=True, return_attention_mask=True, return_tensors="pt").to(self.device)
            else:
                batch = tok(inputs, truncation=True, padding=True, max_length=max_len, return_tensors="pt").to(self.device)
            hidden = model(**batch).last_hidden_state
            mask = batch["attention_mask"]
            if pool == "cls":
                emb = hidden[:, 0]
            elif pool == "mean":
                m = mask[..., None].to(hidden.dtype)
                emb = (hidden * m).sum(1) / m.sum(1).clamp(min=1e-6)
            else:
                emb = last_token_pool(hidden, mask)
            emb = emb.float()
            if self.cfg["normalize"]:
                emb = torch.nn.functional.normalize(emb, dim=-1)
            return emb.cpu().numpy()

    def _count_truncated(self, inputs, max_len):
        lens = [len(x) for x in self.tok(inputs, add_special_tokens=True, truncation=False)["input_ids"]]
        return sum(n > max_len for n in lens)

    def encode_docs(self, chunks, progress=True, use_header=True):
        cfg, kind = self.cfg, self.cfg["kind"]
        # Longest first: similar lengths share a batch, and an out-of-memory batch fails at the start.
        order = sorted(range(len(chunks)), key=lambda i: -chunks[i]["n_tokens_with_header"])
        if kind == "medcpt":
            # The article encoder was trained on [title, abstract] pairs; the context header plays the title.
            inputs = [[chunks[i]["header"], chunks[i]["text"]] if use_header else chunks[i]["text"] for i in order]
            pool = "cls"
        else:
            inputs = [(chunks[i]["header"] + "\n" if use_header else "") + chunks[i]["text"] for i in order]
            if kind == "last_token":
                inputs = ["Represent this passage\npassage: " + x for x in inputs]
            pool = {"mean": "mean", "last_token": "last_token"}.get(kind)
        out, bs, t0 = [], cfg["batch"], time.time()
        for s in range(0, len(inputs), bs):
            part = inputs[s:s + bs]
            truncated = self._count_truncated(part, cfg["max_doc"])
            self.truncated += truncated
            if truncated:
                raise ValueError(f"{truncated} inputs exceed {self.key}'s {cfg['max_doc']}-token budget; rebuild chunks instead of truncating evidence")
            if kind == "st":
                self.model.max_seq_length = cfg["max_doc"]
                out.append(self.model.encode(part, batch_size=bs, normalize_embeddings=cfg["normalize"], convert_to_numpy=True, show_progress_bar=False).astype(np.float32))
            else:
                out.append(self._forward(self.model, self.tok, part, cfg["max_doc"], pool))
            if progress and (s // bs) % 20 == 0:
                print(f"  {min(s + bs, len(inputs))}/{len(inputs)}  {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
        vec = np.concatenate(out).astype(np.float32)
        restored = np.empty_like(vec)
        restored[order] = vec
        return restored

    def encode_queries(self, queries, max_query=None):
        cfg, kind = self.cfg, self.cfg["kind"]
        max_query = max_query or cfg["max_query"]
        if kind == "st":
            self.model.max_seq_length = max_query
            kwargs = {"prompt_name": cfg["query_prompt_name"]} if cfg.get("query_prompt_name") else {}
            return self.model.encode(queries, normalize_embeddings=cfg["normalize"], convert_to_numpy=True, show_progress_bar=False, **kwargs).astype(np.float32)
        if kind == "medcpt":
            if self._query_model is None:
                from transformers import AutoModel, AutoTokenizer

                self._query_tok = AutoTokenizer.from_pretrained(cfg["query"])
                self._query_model = AutoModel.from_pretrained(cfg["query"]).to(self.device).eval()
            return self._forward(self._query_model, self._query_tok, queries, max_query, "cls")
        if kind == "last_token":
            queries = [f"{cfg['query_task']}\nQuery: {q}" for q in queries]
            return self._forward(self.model, self.tok, queries, max_query, "last_token")
        return self._forward(self.model, self.tok, queries, max_query, "mean")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=DEFAULT_MODEL, choices=list(MODELS))
    p.add_argument("--rag-dir", type=Path, default=RAG, help="Chunk/index directory, including an isolated candidate build")
    p.add_argument("--local-only", action="store_true", help="Use cached model files only")
    p.add_argument("--limit", type=int, help="Embed only the first N chunks (smoke test; nothing is saved)")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    chunks, sha = load_chunks(a.rag_dir)
    if a.limit:
        chunks = chunks[:a.limit]
    t0 = time.time()
    emb = Embedder(a.model, local_only=a.local_only)
    load_s = time.time() - t0
    if emb.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    vec = emb.encode_docs(chunks)
    meta = {
        "model_key": a.model, **{k: v for k, v in MODELS[a.model].items() if k != "processor_kwargs"},
        "revision": emb.revision, "dim": int(vec.shape[1]), "n_chunks": int(vec.shape[0]),
        "similarity": "cosine" if MODELS[a.model]["normalize"] else "dot",
        "chunks_sha256": sha, "truncated_inputs": int(emb.truncated),
        "non_finite_values": int((~np.isfinite(vec)).sum()),
        "device": emb.device, "dtype_model": "float16" if emb.half else "float32",
        "gpu_peak_mib": int(torch.cuda.max_memory_allocated() // 2**20) if emb.device == "cuda" else None,
        "load_seconds": round(load_s, 1), "embed_seconds": round(time.time() - t1, 1),
        "torch": torch.__version__, "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
    }
    if not a.limit:
        out = a.rag_dir / "emb" / a.model
        out.mkdir(parents=True, exist_ok=True)
        np.save(out / "doc.npy", vec)
        (out / "chunk_ids.json").write_text(json.dumps([c["chunk_id"] for c in chunks]), encoding="utf-8")
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
