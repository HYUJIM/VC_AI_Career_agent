"""Create a new, offline source-grounded JSON/HTML example without inference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.backend_adapter import load_backend_export
from src.rag.backend_education_display_v2 import build_display, render_html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/backend_education_display_v2')
    args = parser.parse_args()
    source = args.export.read_bytes()
    response = build_display(load_backend_export(args.export))
    directory = args.output / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    files = {'source_export.json': source,
             'source_display.json': (json.dumps(response, ensure_ascii=False, indent=2) + '\n').encode('utf-8'),
             'index.html': render_html(response).encode('utf-8')}
    for name, content in files.items():
        (directory / name).write_bytes(content)
    manifest = {'schema_version': 'be-education-display-run/2.0',
                'integration_status': response['integration_status'],
                'inference_performed': False, 'human_adjudication': False,
                'input_sha256': hashlib.sha256(source).hexdigest(),
                'file_sha256': {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}}
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(directory)


if __name__ == '__main__':
    main()
