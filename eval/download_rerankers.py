"""Download reranker candidates into the local Hugging Face cache and record their revisions.

LocalReranker loads with local_files_only=True (no hidden downloads), so candidates are fetched
explicitly here once. The resolved commit of each snapshot is printed; pin it in
eval/retrieval_gold.MODELS before reporting results.

  python eval/download_rerankers.py jina-reranker-v2-multilingual gte-multilingual-reranker qwen3-reranker-0.6b
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from huggingface_hub import snapshot_download
    from eval.retrieval_gold import MODELS
    for name in sys.argv[1:]:
        model = MODELS[name][0]
        path = Path(snapshot_download(model))
        print(name, model, 'revision', path.name, path)


if __name__ == '__main__':
    main()
