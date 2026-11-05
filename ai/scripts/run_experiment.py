import argparse
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import ModelConfig, from_env
from src.data.credential_loader import load_credentials
from src.data.query_loader import load_queries
from src.llm.local_llm import MockLLM, OpenAICompatibleLLM
from src.rag.retriever import ListRetriever, FixedCandidateRetriever
from src.rag.pipeline import Pipeline, INSTRUCTION, PROMPT_VERSION

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--queries", required=True)
    p.add_argument("--credentials", required=True)
    p.add_argument("--top-k", required=True, type=int)
    p.add_argument("--mock", action="store_true")
    p.add_argument('--candidate-sets', help='Development-only query-ID candidate JSONL; no truncation')
    p.add_argument("--output", default="outputs/runs")
    args = p.parse_args()
    queries, records = load_queries(args.queries), load_credentials(args.credentials)
    if args.mock:
        config = ModelConfig("mock-fixed-output", "test-only-v1", 0, 0, 1)
        llm = MockLLM()
    else:
        config, provider, url = from_env()
        if provider != "openai-compatible":
            raise ValueError("Supported adapter: openai-compatible")
        llm = OpenAICompatibleLLM(url, config)
    retriever = FixedCandidateRetriever(records, queries, args.candidate_sets, args.top_k) if args.candidate_sets else ListRetriever(records)
    pipeline = Pipeline(retriever, llm, config, args.top_k)
    directory = Path(args.output) / ("mock-" if args.mock else "run-") / str(uuid4())
    directory.mkdir(parents=True)
    manifest = {"mock": args.mock, "retriever": 'fixed-query-candidates-v1' if args.candidate_sets else "fixture-order-list-v1", "top_k": args.top_k,
        'prompt_version': PROMPT_VERSION, 'status': 'running', 'completed_results': 0,
        "filter_stage": "post-retrieval-no-refill", "model": config.metadata(), "prompt_instruction": INSTRUCTION,
        "input_sha256": {k: hashlib.sha256(Path(v).read_bytes()).hexdigest() for k,v in [("queries", args.queries), ("credentials", args.credentials)]}}
    if args.candidate_sets:
        manifest['input_sha256']['candidate_sets'] = hashlib.sha256(Path(args.candidate_sets).read_bytes()).hexdigest()
    manifest['verification_versions'] = sorted({r['verification']['verifier_version'] for r in records})
    def save_manifest():
        (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    save_manifest()
    try:
        for query in queries:
            for result in pipeline.iter_all(query):
                (directory / (result["run_id"] + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                manifest['completed_results'] += 1
                save_manifest()
    except Exception as error:
        manifest.update(status='failed', failed_query_id=query['query_id'], error_type=type(error).__name__)
        save_manifest()
        raise
    manifest['status'] = 'completed'
    save_manifest()
    print(f"Saved {len(queries)*3} results: {directory}")
if __name__ == "__main__":
    main()
