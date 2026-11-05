"""Read-only consistency checks for the five Work 2B drafts."""

from prepare_work2b_case001 import (
    AI_ROOT,
    AUDIT_PATH,
    read_json,
    require,
    sha256,
)


DRAFT_DIR = AI_ROOT / "data" / "paper_experiment_v2" / "dev_drafts"
EXPECTED = [
    ("none", None, None, None),
    (
        "forged_signature",
        "cred-ob2-signed-forged-005",
        "signature",
        "fail",
    ),
    (
        "revoked",
        "cred-ob3-di-revoked-006",
        "revocation",
        "fail",
    ),
    (
        "expired",
        "cred-ob3-vcjwt-expired-007",
        "expiration",
        "fail",
    ),
    (
        "untrusted_issuer",
        "cred-ob2-hosted-untrusted-008",
        "issuer_trust",
        "fail",
    ),
]


def unique_index(rows, key, label):
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows), f"{label}: duplicate {key}")
    return result


def check_reported_result(record, name, expected):
    rows = [
        row for row in record["verification"]["checks"]
        if row["check"] == name
    ]
    require(
        len(rows) == 1 and rows[0]["result"] == expected,
        f"Unexpected reported result: {name}={expected}",
    )


def main():
    audit_bytes, audit = read_json(AUDIT_PATH)
    export_path = AI_ROOT / (
        "outputs/vc_exports/"
        "7cd1b4d4-1040-45f1-a5db-d6f1150a4e86/credentials.json"
    )
    export_bytes, records = read_json(export_path)

    require(
        audit["summary"] == {"total": 8, "eligible": 3, "excluded": 5},
        "Unexpected Work 2A summary",
    )
    require(
        sha256(export_bytes) == audit["input_export"]["sha256"],
        "Export hash mismatch",
    )
    require(
        len(export_bytes) == audit["input_export"]["byte_count"],
        "Export byte count mismatch",
    )
    require(audit["paper_ready"] is False, "Unexpected audit readiness")

    source_by_id = unique_index(records, "record_id", "Export")
    audit_by_id = unique_index(audit["records"], "record_id", "Audit")
    require(set(source_by_id) == set(audit_by_id), "Audit/export ID mismatch")
    require(len(records) == 8, "Unexpected export count")
    require(
        sum(row["eligible"] is True for row in audit["records"]) == 3,
        "Unexpected eligible count",
    )

    clean_matches = [
        row for row in records
        if row["source"]["external_id"] == "cred-ob2-signed-002"
    ]
    require(len(clean_matches) == 1, "Missing or duplicate baseline source")
    clean = clean_matches[0]
    clean_id = clean["record_id"]

    expected_names = {
        f"dev-backend-{number:03d}.draft.json"
        for number in range(1, 6)
    }
    actual_names = {
        path.name for path in DRAFT_DIR.glob("dev-backend-*.draft.json")
    }
    require(actual_names == expected_names, "Unexpected or missing draft files")

    snapshots = {}
    cases = []
    for number in range(1, 6):
        path = DRAFT_DIR / f"dev-backend-{number:03d}.draft.json"
        payload, case = read_json(path)
        snapshots[path] = payload
        cases.append(case)

    base = cases[0]
    base_path = DRAFT_DIR / "dev-backend-001.draft.json"
    base_hash = sha256(snapshots[base_path])
    all_fact_ids = set()
    summaries = []

    for number, (case, specification) in enumerate(zip(cases, EXPECTED), 1):
        case_id = f"dev-backend-{number:03d}"
        noise_kind, noise_external_id, check_name, check_result = specification

        require(case["case_id"] == case_id, f"{case_id}: ID mismatch")
        require(
            case["draft_version"] == "work2b-case-draft/0.1"
            and case["target_case_version"] == "paper-case/2.0",
            f"{case_id}: version mismatch",
        )
        require(case["synthetic"] is True, f"{case_id}: synthetic flag")
        require(case["split"] == "dev", f"{case_id}: split")
        require(
            case["family_id"] == "fixture-student-001",
            f"{case_id}: family mismatch",
        )
        require(case["profile"] == base["profile"], f"{case_id}: profile mismatch")
        require(
            case["profile"]["subject_did"] == "did:fixture:student-001",
            f"{case_id}: subject mismatch",
        )
        require(case["jd"] == base["jd"], f"{case_id}: JD mismatch")
        require(case["request"] == base["request"], f"{case_id}: request mismatch")
        require(
            sha256(case["jd"]["text"].encode("utf-8")) == case["jd"]["sha256"],
            f"{case_id}: JD hash mismatch",
        )
        require(
            case["gold_status"] == "pending"
            and case["gold_proposal"]["status"] == "pending",
            f"{case_id}: Gold must remain pending",
        )
        require(
            case["evaluation_ready"] is False and case["paper_ready"] is False,
            f"{case_id}: readiness mismatch",
        )
        require(
            bool(case["target_schema_blockers"]),
            f"{case_id}: missing schema blockers",
        )
        require(
            case["evidence_limitations"] == audit["unavailable_evidence"],
            f"{case_id}: evidence limitations changed",
        )

        provenance = case["provenance"]
        require(
            provenance["audit_file_sha256"] == sha256(audit_bytes),
            f"{case_id}: audit binding mismatch",
        )
        require(
            provenance["input_export_sha256"] == sha256(export_bytes),
            f"{case_id}: export binding mismatch",
        )
        if number > 1:
            require(
                provenance["parent_case_file_sha256"] == base_hash,
                f"{case_id}: parent binding mismatch",
            )

        expected_ids = [clean_id]
        noise_ids = []
        if noise_external_id:
            matches = [
                row for row in records
                if row["source"]["external_id"] == noise_external_id
            ]
            require(len(matches) == 1, f"{case_id}: noise source mismatch")
            noise_record = matches[0]
            noise_id = noise_record["record_id"]
            noise_ids = [noise_id]
            expected_ids.append(noise_id)
            check_reported_result(noise_record, check_name, check_result)
            require(
                audit_by_id[noise_id]["eligible"] is False,
                f"{case_id}: noise unexpectedly eligible",
            )
            require(
                f"check_not_accepted:{check_name}:{check_result}"
                in audit_by_id[noise_id]["reasons"],
                f"{case_id}: missing exclusion reason",
            )

        require(
            case["source_records"] == [source_by_id[rid] for rid in expected_ids],
            f"{case_id}: source records changed",
        )
        require(
            case["record_audits"] == [audit_by_id[rid] for rid in expected_ids],
            f"{case_id}: audit records changed",
        )
        for record in case["source_records"]:
            require(
                record["subject_did"] == case["profile"]["subject_did"],
                f"{case_id}: cross-subject record",
            )
            require(
                record["verification"]
                == audit_by_id[record["record_id"]]["reported_verification"],
                f"{case_id}: reported verification mismatch",
            )

        eligible_ids = [
            rid for rid in expected_ids if audit_by_id[rid]["eligible"] is True
        ]
        require(eligible_ids == [clean_id], f"{case_id}: unexpected eligibility")
        require(
            case["eligible_record_ids"] == eligible_ids
            and case["candidate_record_ids"] == expected_ids,
            f"{case_id}: candidate/eligible mismatch",
        )

        ratio = len(noise_ids) / len(expected_ids)
        require(
            case["noise"] == {
                "kind": noise_kind,
                "target_ratio": ratio,
                "raw_record_ids": noise_ids,
            },
            f"{case_id}: noise definition mismatch",
        )
        expected_exposure = {
            "noise_count": len(noise_ids),
            "total_count": len(expected_ids),
            "ratio": ratio,
        }
        require(
            case["observed_noise_exposure"] == {
                "raw": expected_exposure,
                "candidate": expected_exposure,
            },
            f"{case_id}: noise exposure mismatch",
        )

        contexts = {
            "A_no_rag": [],
            "B_naive_rag": expected_ids,
            "C_verified_rag": eligible_ids,
        }
        if number > 1:
            require(
                case["planned_context_record_ids"] == contexts,
                f"{case_id}: context plan mismatch",
            )
        # Case 001 has no stored context plan; derive it for this check.

        gold = case["gold_proposal"]
        fact_id = f"{case_id}-f01"
        expected_fact = {
            "fact_id": fact_id,
            "entity_id": clean["achievement"]["id"],
            "attribute": "achievement_name",
            "value": clean["achievement"]["name"],
            "unit": None,
            "time_scope": None,
            "source_ids": [clean_id],
        }
        require(
            gold["candidate_facts"] == [expected_fact],
            f"{case_id}: candidate fact mismatch",
        )
        require(fact_id not in all_fact_ids, f"{case_id}: duplicate fact ID")
        all_fact_ids.add(fact_id)
        require(
            gold["proposed_allowed_fact_ids"]
            == gold["proposed_required_fact_ids"]
            == [fact_id],
            f"{case_id}: proposed fact references mismatch",
        )
        require(
            gold["independent_reference_facts"] == []
            and gold["independent_reference_status"] == "not_supplied"
            and gold["closed_world"] is False,
            f"{case_id}: independent evidence status changed",
        )

        summaries.append(
            f"{case_id}: PASS | records={len(expected_ids)} "
            f"| eligible={len(eligible_ids)} | noise={ratio:.1f}"
        )

    mixed = cases[4]["noise_design_notes"]
    require(mixed["single_factor"] is False, "Case 005 must remain mixed-factor")
    require(
        mixed["reported_exclusion_factors"] == [
            {"check": "issuer_trust", "result": "fail"},
            {"check": "signature", "result": "skip"},
        ],
        "Case 005 factor annotations mismatch",
    )
    check_reported_result(cases[4]["source_records"][1], "signature", "skip")

    require(AUDIT_PATH.read_bytes() == audit_bytes, "Audit changed during check")
    require(export_path.read_bytes() == export_bytes, "Export changed during check")
    for path, payload in snapshots.items():
        require(path.read_bytes() == payload, f"Draft changed during check: {path}")

    for summary in summaries:
        print(summary)
    print("Draft consistency: PASS (5/5)")
    print("Families: 1 / Split: dev")
    print("Gold: pending (5/5)")
    print("Source and audit preservation: PASS")
    print("Case 005: mixed exclusion factors preserved")
    print("Target schema: NOT VALIDATED")
    print("Independent reference evidence: NOT SUPPLIED")
    print("Evaluation ready: False / Paper ready: False")
    print("Files written: 0")


if __name__ == "__main__":
    main()