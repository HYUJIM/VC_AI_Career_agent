import json
from pathlib import Path
from src.utils.schema_validator import validate

def load_queries(path):
    result = []
    seen = set()
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        query = json.loads(line)
        validate(query, "experiment-query")
        extra = set(query) - {"query_id", "subject_did", "query_text", "query_type", "answerable", "gold_record_ids", "gold_answer", "notes"}
        if extra or query["query_id"] in seen:
            raise ValueError("Unknown query fields or duplicate query_id")
        seen.add(query["query_id"])
        result.append(query)
    if not result:
        raise ValueError("Empty query set")
    return result
