"""Review existing output without inference; never overwrite an earlier review."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.backend_adapter import load_backend_export
from src.rag.backend_education_review import evaluate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--export', required=True, type=Path)
    args = parser.parse_args(argv)
    def read(name): return json.loads((args.run / name).read_text(encoding='utf-8'))
    manifest, result = read('manifest.json'), read('result.json')
    if manifest.get('diagnostic_version') != 'be-education-three-records-v1' or not manifest.get('inference_performed'):
        raise ValueError('Requires a completed three-record diagnostic inference')
    if manifest.get('status') != 'completed': raise ValueError('Run is not completed')
    source_hash = hashlib.sha256(args.export.read_bytes()).hexdigest()
    if source_hash != manifest['export_sha256']: raise ValueError('Export hash mismatch')
    answer = (args.run / 'answer.txt').read_text(encoding='utf-8')
    if answer != result['generated_answer']: raise ValueError('Answer and result disagree')
    report = evaluate(answer, load_backend_export(args.export), result['generation']['finish_reason'])
    report['input_hashes'] = {name: hashlib.sha256((args.run / name).read_bytes()).hexdigest()
                            for name in ['answer.txt', 'manifest.json', 'result.json']}
    report['export_sha256'] = source_hash
    report['reviewer_sha256'] = hashlib.sha256((ROOT / 'src/rag/backend_education_review.py').read_bytes()).hexdigest()
    path = args.run / ('review_v2_' + str(uuid4()) + '.json')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print('Review:', report['overall_status'])
    print('Saved:', path)
    return path


if __name__ == '__main__': main()
