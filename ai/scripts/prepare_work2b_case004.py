"""Build case 004 using an existing reported expiration failure."""

import copy
import json

from prepare_work2b_case001 import (
    AI_ROOT,
    AUDIT_PATH,
    read_json,
    require,
    sha256,
)


DRAFT_DIR = AI_ROOT / "data" / "paper_experiment_v2" / "dev_drafts"
BASE_PATH = DRAFT_DIR / "dev-backend-001.draft.json"
OUTPUT_PATH = DRAFT_DIR / "dev-backend-004.draft.json"
NOISE_EXTERNAL_ID = "cred-ob3-vcjwt-expired-007"


def main():
    require(not OUTPUT_PATH.exists(), f"출력 파일이 이미 있습니다: {OUTPUT_PATH}")

    base_bytes, base = read_json(BASE_PATH)
    audit_bytes, audit = read_json(AUDIT_PATH)
    export_path = AI_ROOT / (
        "outputs/vc_exports/"
        "7cd1b4d4-1040-45f1-a5db-d6f1150a4e86/credentials.json"
    )
    export_bytes, records = read_json(export_path)

    require(base["case_id"] == "dev-backend-001", "기준 케이스가 다릅니다.")
    require(base["split"] == "dev", "개발용 케이스가 아닙니다.")
    require(base["gold_status"] == "pending", "기준 Gold 상태가 다릅니다.")
    require(
        base["family_id"] == "fixture-student-001",
        "기준 family가 다릅니다.",
    )
    require(
        base["provenance"]["audit_file_sha256"] == sha256(audit_bytes),
        "기준 케이스 생성 후 감사 파일이 변경되었습니다.",
    )
    require(
        base["provenance"]["input_export_sha256"]
        == audit["input_export"]["sha256"]
        == sha256(export_bytes),
        "입력 export 연결 해시가 일치하지 않습니다.",
    )
    require(
        len(export_bytes) == audit["input_export"]["byte_count"],
        "입력 export 바이트 수가 다릅니다.",
    )

    require(len(base["source_records"]) == 1, "기준 레코드 수가 다릅니다.")
    clean_record = base["source_records"][0]
    clean_id = clean_record["record_id"]
    require(
        base["candidate_record_ids"] == base["eligible_record_ids"] == [clean_id],
        "기준 후보 또는 적격 목록이 다릅니다.",
    )
    require(
        sum(row == clean_record for row in records) == 1,
        "기준 레코드와 export 원문이 다릅니다.",
    )

    matches = [
        row for row in records
        if row["source"]["external_id"] == NOISE_EXTERNAL_ID
    ]
    require(len(matches) == 1, "만료 레코드를 하나로 특정하지 못했습니다.")
    noise_record = matches[0]
    noise_id = noise_record["record_id"]
    require(clean_id != noise_id, "기준과 노이즈 ID가 같습니다.")

    audit_by_id = {row["record_id"]: row for row in audit["records"]}
    require(
        len(audit_by_id) == len(audit["records"]),
        "감사 결과에 중복 ID가 있습니다.",
    )
    require(
        clean_id in audit_by_id and noise_id in audit_by_id,
        "대응하는 감사 결과가 없습니다.",
    )
    clean_audit = audit_by_id[clean_id]
    noise_audit = audit_by_id[noise_id]

    require(clean_audit["eligible"] is True, "기준 레코드가 적격이 아닙니다.")
    require(noise_audit["eligible"] is False, "만료 레코드가 제외되지 않았습니다.")
    require(
        "check_not_accepted:expiration:fail" in noise_audit["reasons"],
        "감사 결과에 만료 실패 사유가 없습니다.",
    )
    require(
        noise_record["verification"]["status"] == "expired",
        "보고된 상태가 expired가 아닙니다.",
    )
    require(
        noise_record["expires_at"] == "2020-01-01T00:00:00Z",
        "기존 만료일이 예상과 다릅니다.",
    )

    checks = noise_record["verification"]["checks"]
    for name, expected in (
        ("signature", "pass"),
        ("revocation", "pass"),
        ("expiration", "fail"),
    ):
        matching_checks = [row for row in checks if row["check"] == name]
        require(
            len(matching_checks) == 1
            and matching_checks[0]["result"] == expected,
            f"예상한 보고값이 아닙니다: {name}={expected}",
        )

    expiration_check = next(
        row for row in checks if row["check"] == "expiration"
    )

    require(
        noise_record["subject_did"]
        == clean_record["subject_did"]
        == base["profile"]["subject_did"]
        == audit["subject_did"],
        "서로 다른 학생 자료가 섞였습니다.",
    )
    for record, row in (
        (clean_record, clean_audit),
        (noise_record, noise_audit),
    ):
        require(
            record["verification"] == row["reported_verification"],
            "원문과 감사의 검증 정보가 다릅니다.",
        )

    draft = copy.deepcopy(base)
    draft["case_id"] = "dev-backend-004"
    draft["source_records"] = copy.deepcopy([clean_record, noise_record])
    draft["record_audits"] = copy.deepcopy([clean_audit, noise_audit])
    draft["candidate_record_ids"] = [clean_id, noise_id]
    draft["eligible_record_ids"] = [clean_id]
    draft["noise"] = {
        "kind": "expired",
        "target_ratio": 0.5,
        "raw_record_ids": [noise_id],
    }
    draft["observed_noise_exposure"] = {
        "raw": {"noise_count": 1, "total_count": 2, "ratio": 0.5},
        "candidate": {"noise_count": 1, "total_count": 2, "ratio": 0.5},
    }
    draft["planned_context_record_ids"] = {
        "A_no_rag": [],
        "B_naive_rag": [clean_id, noise_id],
        "C_verified_rag": [clean_id],
    }

    gold = draft["gold_proposal"]
    require(len(gold["candidate_facts"]) == 1, "기준 사실 후보 수가 다릅니다.")
    fact_id = "dev-backend-004-f01"
    gold["candidate_facts"][0]["fact_id"] = fact_id
    gold["proposed_allowed_fact_ids"] = [fact_id]
    gold["proposed_required_fact_ids"] = [fact_id]
    gold["behavior_notes"]["B_naive_rag"] = (
        "데이터베이스 설계 인증을 기술하되 만료 테스트 자료를 "
        "현재 유효한 자격의 근거로 사용하지 않는 것이 예비 기대 행동이다. "
        "실제 모델 출력 예측이나 확정 Gold는 아니다."
    )
    gold["behavior_notes"]["C_verified_rag"] = (
        "기존 감사의 적격 목록으로 만료 레코드를 제외하면 "
        "첫 케이스와 동일한 데이터베이스 설계 인증만 남는다."
    )
    gold["review_notes"].append(
        "만료는 정책상 부적격을 뜻하며 과거 성취나 학습 경험이 거짓임을 뜻하지 않는다."
    )

    provenance = draft["provenance"]
    provenance["parent_case_path"] = str(BASE_PATH)
    provenance["parent_case_file_sha256"] = sha256(base_bytes)
    provenance.pop("original_id", None)
    provenance.pop("reported_raw_hash", None)
    provenance["source_record_bindings"] = [
        {
            "record_id": row["record_id"],
            "original_id": row["source"]["external_id"],
            "reported_raw_hash": row["raw_hash"],
        }
        for row in draft["source_records"]
    ]

    draft["reported_expiration_context"] = {
        "record_id": noise_id,
        "expires_at": noise_record["expires_at"],
        "check": copy.deepcopy(expiration_check),
        "verification_verified_at": noise_record["verification"]["verified_at"],
        "reexecuted": False,
        "note": (
            "기존 보고 시각을 보존한다. checked_at이나 verified_at을 "
            "정식 스키마의 reference_time으로 확정하지 않는다."
        ),
    }
    draft["development_notes"] = [
        "노이즈 비율의 분모는 케이스에 선택한 2건이며 전체 export 8건이 아니다.",
        "원문에 만료 테스트 단서가 있어 필터 동작 확인용으로 제한한다.",
        "signature=pass와 expiration=fail은 기존 보고값이며 재검증 결과가 아니다.",
        "현재 시각을 사용한 만료 재판정이나 새 검증 로그 생성은 하지 않았다.",
        "문맥 계획은 실제 검색 또는 모델 생성 결과가 아니다.",
        "동일 학생 파생 사례이므로 기존 family와 dev 분할을 유지한다.",
    ]

    require(draft["jd"] == base["jd"], "JD가 변경되었습니다.")
    require(draft["request"] == base["request"], "생성 요청이 변경되었습니다.")
    require(draft["gold_status"] == gold["status"] == "pending", "Gold 상태 오류")
    require(
        draft["source_records"] == [clean_record, noise_record],
        "원천 레코드 보존 검사 실패",
    )
    require(BASE_PATH.read_bytes() == base_bytes, "기준 초안이 변경되었습니다.")
    require(AUDIT_PATH.read_bytes() == audit_bytes, "감사 파일이 변경되었습니다.")
    require(export_path.read_bytes() == export_bytes, "export가 변경되었습니다.")

    with OUTPUT_PATH.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(draft, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")

    print(f"Created: {OUTPUT_PATH}")
    print("Source binding: PASS")
    print("Source records preserved: PASS")
    print("Reported signature / expiration: pass / fail")
    print(f"Reported expires_at: {noise_record['expires_at']}")
    print(f"Reported expiration checked_at: {expiration_check['checked_at']}")
    print("Case: dev-backend-004")
    print(f"Family: {draft['family_id']}")
    print("Records: 2 / Eligible: 1 / Noise: 1")
    print("Raw / candidate noise ratio: 0.5 / 0.5")
    print("Planned context counts: A=0 / B=2 / C=1")
    print("Gold: pending")
    print("Scope: filter development; explicit noise cues present")
    print("Target schema: NOT VALIDATED (draft with documented blockers)")
    print("Evaluation ready: False")
    print("Paper ready: False")


if __name__ == "__main__":
    main()