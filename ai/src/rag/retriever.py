from typing import Protocol

class Retriever(Protocol):
    def search(self, query: str, subject_did: str, top_k: int) -> list: ...

class ListRetriever:
    """Fixture-order retriever for plumbing tests, not semantic retrieval."""
    def __init__(self, records):
        self.records = records
    def search(self, query, subject_did, top_k):
        if top_k < 1:
            raise ValueError("top_k must be positive")
        return [r for r in self.records if r["subject_did"] == subject_did][:top_k]
