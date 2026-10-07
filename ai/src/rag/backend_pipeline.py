"""BE connectivity path sharing the existing prompt and LLM generate interface.

This is deliberately not the A/B/C research pipeline: a BE status is not proof
of actual verification. No changes to the Stage1 filtering policy are needed.
"""
from datetime import datetime, timezone
from uuid import uuid4

from src.data.backend_adapter import validate_backend_export
from src.rag.pipeline import build_prompt, snippet, PROMPT_VERSION


def prepare_backend_prompt(export, question, *, record_ids=None, status_filter="all"):
    validate_backend_export(export)
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question must be non-empty")
    if status_filter not in ("all", "verified"):
        raise ValueError("Status filter must be all or verified (BE-reported only)")
    by_id = {r["record_id"]: r for r in export["records"]}
    ids = list(by_id) if record_ids is None else list(record_ids)
    if len(ids) != len(set(ids)) or any(rid not in by_id for rid in ids):
        raise ValueError("Duplicate or unknown selected record_id")
    candidates = [by_id[rid] for rid in ids]
    selected = [r for r in candidates if status_filter == "all" or r["backend_status"] == "verified"]
    contexts = [{"record_id": r["record_id"], "snippet": snippet(r),
                 "backend_reported_status": r["backend_status"]} for r in selected]
    selected_ids = {r["record_id"] for r in selected}
    return {
        "subject_did": export["subject_did"], "question": question,
        "purpose": "backend_connectivity_only", "paper_evaluation_eligible": False,
        "verification_basis": "backend_status_only", "status_filter": status_filter,
        "prompt_version": PROMPT_VERSION, "prompt": build_prompt(question, contexts),
        "candidate_record_ids": ids, "retrieved_contexts": contexts,
        "excluded_records": [{"record_id": r["record_id"], "reason": "backend_reported_status",
                              "backend_status": r["backend_status"]}
                             for r in candidates if r["record_id"] not in selected_ids],
    }


class BackendPipeline:
    """Pass any existing LLM adapter (generate(prompt)) after preparing BE input."""
    def __init__(self, llm):
        self.llm = llm

    def run(self, export, question, *, record_ids=None, status_filter="all"):
        result = prepare_backend_prompt(export, question, record_ids=record_ids, status_filter=status_filter)
        result["run_id"] = str(uuid4())
        result["generated_answer"] = self.llm.generate(result["prompt"])
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        return result
