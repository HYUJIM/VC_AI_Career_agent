import json
from pathlib import Path
from src.utils.schema_validator import validate

def validate_records(records):
    if not isinstance(records, list):
        raise ValueError("Expected a JSON array of normalized CredentialRecord objects")
    seen = set()
    checks = {"schema", "did_resolution", "signature", "issuer_trust", "revocation", "anchor_match", "expiration"}
    for record in records:
        validate(record, "credential-record")
        if record["record_id"] in seen:
            raise ValueError("Duplicate record_id")
        seen.add(record["record_id"])
        if {c["check"] for c in record["verification"]["checks"]} != checks:
            raise ValueError("Verification must contain seven distinct checks")
    return records

def load_credentials(path):
    return validate_records(json.loads(Path(path).read_text(encoding="utf-8-sig")))

def load_from_api(client, subject_did):
    listing = client.credentials(subject_did, status="all")
    return validate_records([client.record(r["record_id"]) for r in listing["records"]])


def load_from_api_with_report(client, subject_did):
    """Quarantine malformed records without changing strict loader behavior.

    HTTP errors, duplicate identities and cross-subject responses remain fatal.
    The caller must persist the report before using accepted records.
    """
    from jsonschema import ValidationError
    listing = client.credentials(subject_did, status="all")
    accepted, rejected, seen = [], [], set()
    for item in listing["records"]:
        requested_id = item["record_id"]
        if requested_id in seen:
            raise ValueError("Duplicate API record_id")
        seen.add(requested_id)
        record = client.record(requested_id)
        if isinstance(record, dict):
            if record.get("record_id", requested_id) != requested_id:
                raise ValueError("API record_id mismatch")
            if record.get("subject_did", subject_did) != subject_did:
                raise ValueError("Cross-subject API response prohibited")
        try:
            validate_records([record])
        except ValidationError as error:
            rejected.append({"record_id": requested_id, "reason": "schema_validation",
                             "message": error.message, "instance_path": list(error.absolute_path),
                             "schema_path": list(error.absolute_schema_path), "original_record": record})
        except ValueError as error:
            rejected.append({"record_id": requested_id, "reason": "record_validation",
                             "message": str(error), "original_record": record})
        else:
            accepted.append(record)
    validate_records(accepted)
    return accepted, {"subject_did": subject_did, "requested_status": "all",
                      "policy": "quarantine-malformed-before-both-B-and-C-v1",
                      "received_count": len(listing["records"]), "accepted_count": len(accepted),
                      "rejected_count": len(rejected), "rejected_records": rejected}
