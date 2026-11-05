"""Fetch BE careers (or replay saved JSON) and preserve an auditable snapshot."""
import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from dotenv import load_dotenv
from src.clients.backend_client import BackendClient, validate_subject_did
from src.data.backend_adapter import save_backend_export


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--base-url", help="BE origin, e.g. http://127.0.0.1:3000")
    source.add_argument("--input-json", type=Path, help="Replay a saved careers response without network")
    parser.add_argument("--subject-did", required=True)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/backend_exports")
    args = parser.parse_args()
    try:
        validate_subject_did(args.subject_did)
        if args.input_json:
            payload = json.loads(args.input_json.read_text(encoding="utf-8-sig"))
            source_info = {"mode": "offline_response"}
        else:
            base_url = args.base_url or os.getenv("BE_API_BASE_URL", "http://127.0.0.1:3000")
            with BackendClient(base_url, timeout=args.timeout) as client:
                payload = client.careers(args.subject_did)
                source_info = {"mode": "http", "base_url": client.base_url,
                               "endpoint": "/api/v1/subjects/{did}/careers"}
    except httpx.HTTPStatusError as error:
        parser.exit(2, f"BE request failed: HTTP {error.response.status_code}. Check the BE server and DID.\n")
    except httpx.RequestError as error:
        parser.exit(2, f"BE connection failed ({type(error).__name__}). Check BE_API_BASE_URL and server availability.\n")
    except (ValueError, OSError) as error:
        parser.exit(2, f"Cannot read BE response ({type(error).__name__}). Check input and configuration.\n")
    directory = args.output / str(uuid4())
    try:
        report = save_backend_export(payload, args.subject_did, directory, source=source_info)
    except ValueError:
        parser.exit(2, f"BE contract validation failed. Audit saved: {directory}\n")
    print("Received:", report["received_count"])
    print("Accepted:", report["accepted_count"])
    print("Quarantined:", report["rejected_count"])
    print("BE-reported status counts:", report["accepted_status_counts"])
    print("Saved:", directory)
    print("Verification basis: BE status only; not paper evaluation data.")
    if not report["accepted_count"]:
        parser.exit(2, "No usable records. Check validation_report.json before preparing model input.\n")


if __name__ == "__main__":
    main()
