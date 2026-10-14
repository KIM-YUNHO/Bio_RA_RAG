"""Compatibility entry point; all questions now use rag.pipeline."""
from rag.pipeline import RAGPipeline, main


class CandidateQA(RAGPipeline):
    """Old programmatic name retained without a second RAG implementation."""
    def __init__(self, bundle, config=None, review_url='http://127.0.0.1:8766', encoder_factory=None):
        super().__init__(bundle=bundle, config=config, review_url=review_url, encoder_factory=encoder_factory)

    def ask(self, question, document=None, top_k=None):
        return super().ask(question, documents=[document] if document else None)


if __name__ == '__main__':
    main()