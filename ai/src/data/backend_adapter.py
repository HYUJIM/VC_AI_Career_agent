"""Convert BE careers to evidence without fabricating VC verification checks."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import UUID

from jsonschema import ValidationError
from src.clients.backend_client import validate_subject_did
from src.utils.schema_validator import validate

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"
ADAPTER_VERSION = "be-careers-adapter/1.0"
EXPORT_VERSION = "be-evidence-export/1.0"
RECORD_VERSION = "be-evidence/1.0"


class BackendContractError(ValueError):
    """A batch cannot be safely assigned to one subject or record identity."""


def json_hash(value):
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _pick(value, names):
    return {key: deepcopy(value[key]) for key in names if key in value}


def _vc_subject(raw):
    subject = raw.get("credentialSubject")
    if isinstance(subject, list):
        if len(subject) != 1:
            raise ValueError("Expected exactly one credentialSubject")
        subject = subject[0]
    if not isinstance(subject, dict):
        raise ValueError("credentialSubject must be an object")
    return subject


def _check_subjects(item, subject_did):
    """Identity conflicts are fatal even if a row would otherwise be quarantined."""
    raw = item.get("raw_data")
    objects = [item]
    if isinstance(raw, dict):
        objects.append(raw)
        if isinstance(raw.get("vc"), dict):
            objects.append(raw["vc"])
    for obj in objects:
        declared = obj.get("subject_did")
        if declared is not None and declared != subject_did:
            raise BackendContractError("Cross-subject backend record")
        subject = obj.get("credentialSubject")
        subjects = subject if isinstance(subject, list) else [subject]
        for entry in subjects:
            if isinstance(entry, dict) and entry.get("id") is not None:
                if entry["id"] != subject_did:
                    raise BackendContractError("Cross-subject credentialSubject")


def _date(value):
    # Reuse the project's date-time format checker (including timezone).
    if value is not None:
        from jsonschema import Draft202012Validator, FormatChecker
        Draft202012Validator({"type": "string", "format": "date-time"},
                             format_checker=FormatChecker()).validate(value)
    return value


def _one_record(item, subject_did):
    validate(item, "backend-career", schema_dir=SCHEMAS)
    raw = item["raw_data"]
    raw = raw.get("vc", raw)
    if not isinstance(raw, dict):
        raise ValueError("raw_data.vc must be an object")
    if "credentialSubject" in raw:
        subject = _vc_subject(raw)
        achievement = subject.get("achievement")
        shape = "vc_payload"
    elif isinstance(raw.get("achievement"), dict):
        achievement = raw["achievement"]
        shape = "summary_object"
    else:
        raise ValueError("Unsupported raw_data: expected VC or achievement summary")
    if not isinstance(achievement, dict):
        raise ValueError("achievement must be an object")
    if achievement.get("name") not in (None, item["achievement_name"]):
        raise ValueError("achievement_name conflicts with raw_data")

    issuer_raw = raw.get("issuer", {})
    if isinstance(issuer_raw, str):
        issuer_raw = {"id": issuer_raw}
    if not isinstance(issuer_raw, dict):
        raise ValueError("issuer must be a string or an object")
    if issuer_raw.get("name") not in (None, item["issuer"]):
        raise ValueError("issuer conflicts with raw_data")
    issuer = {"name": item["issuer"]}
    issuer_id = issuer_raw.get("id", issuer_raw.get("did"))
    if isinstance(issuer_id, str) and issuer_id.startswith("did:"):
        issuer["did"] = issuer_id
    if isinstance(issuer_raw.get("url"), str):
        issuer["url"] = issuer_raw["url"]

    name_fields = ("id", "description", "criteria_narrative", "achievement_type", "tags")
    normalized_achievement = {"name": item["achievement_name"], **_pick(achievement, name_fields)}
    criteria = achievement.get("criteria", {})
    if "criteria_narrative" not in normalized_achievement and isinstance(criteria, dict):
        if "narrative" in criteria:
            normalized_achievement["criteria_narrative"] = criteria["narrative"]
    if "achievement_type" not in normalized_achievement and "achievementType" in achievement:
        normalized_achievement["achievement_type"] = achievement["achievementType"]

    record = {
        "schema_version": RECORD_VERSION, "record_id": item["record_id"],
        "subject_did": subject_did, "issuer": issuer, "achievement": normalized_achievement,
        "issued_at": item["issued_at"], "backend_status": item["status"],
        "provenance": {"source": "backend_careers", "raw_shape": shape,
                       "verification_basis": "backend_status_only",
                       "cryptographic_verification_performed": False,
                       "raw_data_sha256": json_hash(item["raw_data"])},
    }
    warnings = ["verification_details_not_available", "source_provider_not_available"]
    # Preserve supplied dates. Do not manufacture an expiry or use collection time as issuance.
    for key, alternatives in (("issued_at", ("issued_at", "issuanceDate", "validFrom")),
                              ("expires_at", ("expires_at", "expirationDate", "validUntil"))):
        values = [item[key]] if item.get(key) is not None else []
        values += [raw[k] for k in alternatives if raw.get(k) is not None]
        for value in values:
            _date(value)
        if values:
            instants = {datetime.fromisoformat(v.replace("Z", "+00:00")) for v in values}
            if len(instants) > 1:
                raise ValueError(f"Conflicting {key} values")
            record[key] = values[0]
        elif key == "expires_at":
            record[key] = None
            warnings.append("expiration_not_available")
    if "awarded_date" in item or "awarded_date" in raw:
        record["awarded_date"] = item.get("awarded_date", raw.get("awarded_date"))
    if "evidence" in raw:
        evidence = raw["evidence"]
        if isinstance(evidence, dict):
            evidence = [evidence]
        if not isinstance(evidence, list) or any(not isinstance(e, dict) for e in evidence):
            raise ValueError("evidence must contain objects")
        record["evidence"] = []
        for e in evidence:
            entry = _pick(e, ("name", "description", "url"))
            if "url" not in entry and isinstance(e.get("id"), str) and e["id"].startswith(("https://", "http://")):
                entry["url"] = e["id"]
            record["evidence"].append(entry)
    # No skill inference: BE's current response has no normalized skill contract.
    validate(record, "backend-evidence", schema_dir=SCHEMAS)
    return record, warnings


def adapt_backend_response(payload, subject_did):
    validate_subject_did(subject_did)
    if not isinstance(payload, dict) or not isinstance(payload.get("user_profile"), dict):
        raise BackendContractError("Missing user_profile")
    if payload["user_profile"].get("did") != subject_did:
        raise BackendContractError("Requested DID differs from user_profile.did")
    rows = payload.get("verified_credentials")
    if not isinstance(rows, list):
        raise BackendContractError("verified_credentials must be an array")
    # Verify identity across the entire batch before accepting any records.
    seen = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        _check_subjects(item, subject_did)
        rid = item.get("record_id")
        if isinstance(rid, str):
            try:
                identity = str(UUID(rid))
            except ValueError:
                identity = rid
            if identity in seen:
                raise BackendContractError("Duplicate backend record_id")
            seen.add(identity)
    accepted, rejected, notices = [], [], []
    for index, item in enumerate(rows):
        try:
            record, warnings = _one_record(item, subject_did)
        except (ValidationError, ValueError) as error:
            # Raw values are already retained in the response; avoid copying PII into errors.
            rejected.append({"index": index, "record_id": item.get("record_id") if isinstance(item, dict) else None,
                             "reason": "schema_validation" if isinstance(error, ValidationError) else str(error),
                             "instance_path": list(error.absolute_path) if isinstance(error, ValidationError) else []})
        else:
            accepted.append(record)
            notices.append({"record_id": record["record_id"], "warnings": warnings})
    report = {"adapter_version": ADAPTER_VERSION, "subject_did": subject_did,
              "received_count": len(rows), "accepted_count": len(accepted), "rejected_count": len(rejected),
              "accepted_status_counts": dict(Counter(r["backend_status"] for r in accepted)),
              "rejected_records": rejected, "record_warnings": notices,
              "verification_basis": "backend_status_only", "paper_evaluation_eligible": False}
    return accepted, report


def make_export(records, subject_did):
    result = {"schema_version": EXPORT_VERSION, "subject_did": subject_did, "records": records}
    validate_backend_export(result)
    return result


def validate_backend_export(value):
    if not isinstance(value, dict) or value.get("schema_version") != EXPORT_VERSION:
        raise BackendContractError("Expected a backend evidence export, not CredentialRecord[]")
    subject = validate_subject_did(value.get("subject_did"))
    records = value.get("records")
    if not isinstance(records, list):
        raise BackendContractError("Backend export records must be an array")
    seen = set()
    for record in records:
        validate(record, "backend-evidence", schema_dir=SCHEMAS)
        if record["subject_did"] != subject:
            raise BackendContractError("Cross-subject backend export")
        rid = str(UUID(record["record_id"]))
        if rid in seen:
            raise BackendContractError("Duplicate backend export record_id")
        seen.add(rid)
    return value


def load_backend_export(path):
    return validate_backend_export(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def save_backend_export(payload, subject_did, directory, *, source):
    """Audit survives contract failure and all-rejected responses; output is never overwritten."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    def write(name, value):
        path = directory / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return hashlib.sha256(path.read_bytes()).hexdigest()
    original_hash = write("raw_response.json", payload)
    manifest = {"adapter_version": ADAPTER_VERSION, "subject_did": subject_did,
                "source": source, "collected_at": datetime.now(timezone.utc).isoformat(),
                "purpose": "backend_connectivity_only", "paper_evaluation_eligible": False,
                "status": "validating", "file_sha256": {"raw_response.json": original_hash}}
    try:
        records, report = adapt_backend_response(payload, subject_did)
    except (BackendContractError, ValueError) as error:
        write("validation_report.json", {"status": "failed", "error_type": type(error).__name__, "reason": str(error)})
        manifest.update(status="failed", error_type=type(error).__name__)
        write("manifest.json", manifest)
        raise
    manifest["file_sha256"]["validation_report.json"] = write("validation_report.json", report)
    manifest["file_sha256"]["backend_evidence.json"] = write("backend_evidence.json", make_export(records, subject_did))
    manifest.update(status="ready" if records else "no_usable_records",
                    received_count=report["received_count"], accepted_count=len(records), rejected_count=report["rejected_count"])
    write("manifest.json", manifest)
    return report
