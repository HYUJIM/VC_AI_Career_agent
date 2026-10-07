from typing import Protocol
import json
from pathlib import Path

class FixedCandidateRetriever:
    """Development-only candidates keyed by query ID, never a semantic retriever."""
    def __init__(self, records, queries, path, top_k):
        self.records = {r['record_id']: r for r in records}
        query_map = {q['query_id']: q for q in queries}
        self.candidates = {}
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            qid, ids = item['query_id'], item['record_ids']
            if qid in self.candidates or qid not in query_map:
                raise ValueError('Duplicate or unknown candidate query_id')
            if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
                raise ValueError('record_ids must be a list of strings')
            if len(set(ids)) != len(ids):
                raise ValueError('Duplicate candidate record_id')
            if top_k < 1 or len(ids) > top_k:
                raise ValueError('top_k must cover every fixed candidate; truncation is prohibited')
            if any(rid not in self.records for rid in ids):
                raise ValueError('Unknown candidate record_id')
            if any(self.records[rid]['subject_did'] != query_map[qid]['subject_did'] for rid in ids):
                raise ValueError('Cross-subject candidate prohibited')
            self.candidates[qid] = ids
        if set(self.candidates) != set(query_map):
            raise ValueError('Every query must have an explicit candidate set (empty is allowed)')

    def search_for_query(self, query, top_k):
        ids = self.candidates[query['query_id']]
        if top_k < 1 or len(ids) > top_k:
            raise ValueError('Fixed candidates cannot be truncated')
        return [self.records[rid] for rid in ids]

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
