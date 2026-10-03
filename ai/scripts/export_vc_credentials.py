"""Export normalized VC records plus a mandatory exclusion audit report."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.clients.wallet_client import WalletClient
from src.data.credential_loader import load_from_api_with_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--subject-did', required=True)
    args = parser.parse_args()
    client = WalletClient(args.base_url)
    try:
        records, report = load_from_api_with_report(client, args.subject_did)
    finally:
        client.close()
    directory = Path('outputs/vc_exports') / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    report.update(exported_at=datetime.now(timezone.utc).isoformat(), base_url=args.base_url,
                  accepted_status_counts=dict(Counter(r['verification']['status'] for r in records)))
    # Audit first: never silently discard malformed input.
    (directory / 'validation_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'credentials.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Received:', report['received_count'])
    print('Accepted:', len(records))
    print('Quarantined:', report['rejected_count'])
    print('Status counts:', report['accepted_status_counts'])
    print('Saved:', directory)
    if not records:
        raise SystemExit('No usable records. See validation_report.json; do not run an experiment.')

if __name__ == '__main__':
    main()
