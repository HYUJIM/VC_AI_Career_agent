"""Exercise BE -> existing prompt builder -> MockLLM. Never loads Qwen or a GPU."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.backend_adapter import load_backend_export
from src.llm.local_llm import MockLLM
from src.rag.backend_pipeline import BackendPipeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, type=Path, help="backend_evidence.json from export_backend_credentials.py")
    query = parser.add_mutually_exclusive_group(required=True)
    query.add_argument("--question")
    query.add_argument("--question-file", type=Path, help="UTF-8 question text")
    parser.add_argument("--record-id", action="append", help="Repeat to explicitly choose candidates; default is all")
    parser.add_argument("--status", choices=("all", "verified"), default="all",
                        help="verified uses only the BE label; this is NOT condition C")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/backend_previews")
    args = parser.parse_args()
    question = args.question if args.question is not None else args.question_file.read_text(encoding="utf-8-sig")
    export = load_backend_export(args.export)
    if not export["records"]:
        parser.exit(2, "No usable backend records. Inspect the export audit first.\n")
    result = BackendPipeline(MockLLM()).run(export, question, record_ids=args.record_id, status_filter=args.status)
    result.update(mock=True, inference_performed=False)
    directory = args.output / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "prompt.txt").write_text(result["prompt"], encoding="utf-8")
    (directory / "mock_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"status": "completed", "purpose": "backend_connectivity_only", "mock": True,
                "inference_performed": False, "paper_evaluation_eligible": False,
                "verification_basis": "backend_status_only", "prompt_version": result["prompt_version"],
                "input_sha256": hashlib.sha256(args.export.read_bytes()).hexdigest(),
                "prompt_sha256": hashlib.sha256(result["prompt"].encode("utf-8")).hexdigest(),
                "candidate_count": len(result["candidate_record_ids"]),
                "context_count": len(result["retrieved_contexts"]), "status_filter": args.status,
                "prompt_characters": len(result["prompt"]), "input_tokens": None,
                "token_budget_checked": False, "retriever": "explicit-record-ids-or-all-no-truncation"}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Saved mock preview:", directory)
    print("Contexts:", manifest["context_count"], "/ Candidates:", manifest["candidate_count"])
    print("No model inference performed. Token budget must be checked by Qwen in the next Work.")


if __name__ == "__main__":
    main()
