"""Real A/B/C development run using the existing Pipeline. No HTTP server or .env."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import ModelConfig
from src.data.credential_loader import load_credentials
from src.data.query_loader import load_queries
from src.rag.retriever import FixedCandidateRetriever
from src.rag.pipeline import Pipeline, INSTRUCTION, PROMPT_VERSION
from src.rag.context_policy import CONDITIONS
from src.llm.transformers_llm import TransformersLLM, MODEL_ID, REVISION, validate_settings

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queries', type=Path, default=ROOT/'data/research_stage1/queries.jsonl')
    parser.add_argument('--credentials', type=Path, default=ROOT/'data/research_stage1/credentials.json')
    parser.add_argument('--candidate-sets', type=Path, default=ROOT/'data/research_stage1/candidate_sets.jsonl')
    parser.add_argument('--revision', default=REVISION)
    parser.add_argument('--top-k', type=int, default=2)
    parser.add_argument('--max-new-tokens', type=int, default=128)
    parser.add_argument('--max-input-tokens', type=int, default=2048)
    parser.add_argument('--allow-download', action='store_true', help='Allow downloading files missing from the smoke-run cache')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/runs')
    args = parser.parse_args()
    validate_settings(args.revision, args.max_new_tokens, args.max_input_tokens)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    queries = load_queries(args.queries)
    records = load_credentials(args.credentials)
    retriever = FixedCandidateRetriever(records, queries, args.candidate_sets, args.top_k)
    model_config = ModelConfig(MODEL_ID, args.revision+'/fp32/eager', 0, 42, args.max_new_tokens)
    directory = args.output/'run-'/str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        'status': 'loading', 'mock': False, 'inference_performed': False,
        'purpose': 'development_only_not_paper_results', 'backend': 'transformers-direct-cuda-v1',
        'model': model_config.metadata(), 'prompt_version': PROMPT_VERSION, 'prompt_instruction': INSTRUCTION,
        'decoding': 'greedy; temperature metadata 0 means no sampling',
        'retriever': 'fixed-query-candidates-v1', 'top_k': args.top_k, 'filter_stage': 'post-retrieval-no-refill',
        'execution_order': 'input query order, A then B then C',
        'verification_versions': sorted({r['verification']['verifier_version'] for r in records}),
        'expected_results': len(queries)*3, 'completed_results': 0,
        'python': platform.python_version(), 'platform': platform.platform(),
        'started_at': datetime.now(timezone.utc).isoformat(),
        'input_sha256': {k: hashlib.sha256(p.read_bytes()).hexdigest() for k,p in
                         [('queries',args.queries),('credentials',args.credentials),('candidate_sets',args.candidate_sets)]},
        'code_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                       [Path(__file__).resolve(), ROOT/'src/llm/transformers_llm.py',ROOT/'src/rag/pipeline.py',ROOT/'src/rag/retriever.py',ROOT/'src/rag/context_policy.py']},
    }
    def save():
        temp = directory/'manifest.tmp'
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        temp.replace(directory/'manifest.json')
    save()
    print('Run directory:', directory, flush=True)
    print('Loading pinned Qwen model (FP32, eager, CUDA)...', flush=True)
    stage, query_id, condition = 'load_model', None, None
    try:
        llm = TransformersLLM(revision=args.revision, max_new_tokens=args.max_new_tokens,
                              max_input_tokens=args.max_input_tokens, local_files_only=not args.allow_download)
        manifest.update(runtime=llm.runtime_metadata, status='running')
        save()
        pipeline = Pipeline(retriever, llm, model_config, args.top_k)
        for query in queries:
            query_id = query['query_id']
            stage, condition = 'retrieve', None
            candidates = pipeline.retrieve(query)
            for condition in CONDITIONS:
                stage = 'inference'
                print(f'[{manifest["completed_results"]+1}/{manifest["expected_results"]}] {query_id} {condition}', flush=True)
                result = pipeline.run_query(query, condition, candidates)
                details = llm.last_generation
                result['execution']['backend_details'] = details
                result['execution']['token_usage'] = details['token_usage']
                stage = 'save_result'
                (directory/(result['run_id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                manifest['completed_results'] += 1
                manifest['inference_performed'] = True
                save()
        manifest.update(status='completed', ended_at=datetime.now(timezone.utc).isoformat())
        save()
    except (Exception, KeyboardInterrupt) as error:
        manifest.update(status='failed', failed_stage=stage, failed_query_id=query_id,
                        failed_condition=condition, error_type=type(error).__name__)
        save()
        print('Stopped; completed results preserved in:', directory, file=sys.stderr)
        raise
    print(f'Saved {manifest["completed_results"]} real results: {directory}', flush=True)

if __name__ == '__main__':
    main()
