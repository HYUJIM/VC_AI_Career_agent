"""Build case 002 from case 001 and existing reported signature failure."""

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
OUTPUT_PATH = DRAFT_DIR / "dev-backend-002.draft.json"
NOISE_EXTERNAL_ID = "cred-ob2-signed-forged-005"


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
    require(base["gold_status"] == "pending", "기준 Gold 상태가 다릅니다.")
    require(base["split"] == "dev", "개발용 케이스가 아닙니다.")
    require(
        base["family_id"] == "fixture-student-001",
        "기준 family가 다릅니다.",
    )
    require(
        base["provenance"]["audit_file_sha256"] == sha256(audit_bytes),
        "첫 케이스 생성 후 감사 파일이 변경되었습니다.",
    )
    require(
        base["provenance"]["input_export_sha256"]
        == audit["input_export"]["sha256"]
        == sha256(export_bytes),
        "입력 export의 연결 해시가 일치하지 않습니다.",
    )
    require(
        len(export_bytes) == audit["input_export"]["byte_count"],
        "입력 export 바이트 수가 다릅니다.",
    )

    require(len(base["source_records"]) == 1, "기준 레코드 수가 다릅니다.")
    clean_record = base["source_records"][0]
    clean_id = clean_record["record_id"]
    require(
        base["eligible_record_ids"] == base["candidate_record_ids"] == [clean_id],
        "기준 후보 또는 적격 목록이 다릅니다.",
    )
    require(
        any(row == clean_record for row in records),
        "기준 레코드와 export 원문이 다릅니다.",
    )

    noise_matches = [
        row for row in records
        if row["source"]["external_id"] == NOISE_EXTERNAL_ID
    ]
    require(len(noise_matches) == 1, "서명 실패 레코드를 특정하지 못했습니다.")
    noise_record = noise_matches[0]
    noise_id = noise_record["record_id"]

    audit_by_id = {row["record_id"]: row for row in audit["records"]}
    require(
        len(audit_by_id) == len(audit["records"]),
        "감사 결과에 중복 ID가 있습니다.",
    )
    require(clean_id in audit_by_id, "기준 레코드의 감사 결과가 없습니다.")
    require(noise_id in audit_by_id, "노이즈 레코드의 감사 결과가 없습니다.")
    clean_audit = audit_by_id[clean_id]
    noise_audit = audit_by_id[noise_id]

    require(clean_audit["eligible"] is True, "기준 레코드가 적격이 아닙니다.")
    require(noise_audit["eligible"] is False, "노이즈 레코드가 제외되지 않았습니다.")
    require(
        "check_not_accepted:signature:fail" in noise_audit["reasons"],
        "보고된 서명 실패 사유가 없습니다.",
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
    draft["case_id"] = "dev-backend-002"
    draft["source_records"] = copy.deepcopy([clean_record, noise_record])
    draft["record_audits"] = copy.deepcopy([clean_audit, noise_audit])
    draft["candidate_record_ids"] = [clean_id, noise_id]
    draft["eligible_record_ids"] = [clean_id]
    draft["noise"] = {
        "kind": "forged_signature",
        "target_ratio": 0.5,
        "raw_record_ids": [noise_id],
    }
    draft["observed_noise_exposure"] = {
        "raw": {"noise_count": 1, "total_count": 2, "ratio": 0.5},
        "candidate": {"noise_count": 1, "total_count": 2, "ratio": 0.5},
    }

    # 원천 사실은 동일하며 케이스 내부 fact ID만 새로 부여한다.
    gold = draft["gold_proposal"]
    require(len(gold["candidate_facts"]) == 1, "기준 사실 후보 수가 다릅니다.")
    fact_id = "dev-backend-002-f01"
    gold["candidate_facts"][0]["fact_id"] = fact_id
    gold["proposed_allowed_fact_ids"] = [fact_id]
    gold["proposed_required_fact_ids"] = [fact_id]
    gold["behavior_notes"]["B_naive_rag"] = (
        "데이터베이스 설계 인증을 기술하되, 위조 테스트 배지를 "
        "지원자의 보유 역량 근거로 사용하지 않는 것이 예비 기대 행동이다. "
        "이는 모델의 실제 출력 예측이나 확정 Gold가 아니다."
    )
    gold["behavior_notes"]["C_verified_rag"] = (
        "기존 감사의 적격 목록으로 서명 실패 레코드를 제외하면 "
        "첫 케이스와 동일한 데이터베이스 설계 인증만 남는다."
    )
    gold["review_notes"].append(
        "노이즈의 보고된 서명 실패는 내용의 거짓 여부를 독립적으로 판정하지 않는다."
    )

    draft["provenance"]["parent_case_path"] = str(BASE_PATH)
    draft["provenance"]["parent_case_file_sha256"] = sha256(base_bytes)
    draft["provenance"].pop("original_id", None)
    draft["provenance"].pop("reported_raw_hash", None)
    draft["provenance"]["source_record_bindings"] = [
        {
            "record_id": row["record_id"],
            "original_id": row["source"]["external_id"],
            "reported_raw_hash": row["raw_hash"],
        }
        for row in draft["source_records"]
    ]

    draft["development_notes"] = [
        "노이즈 비율의 분모는 이 케이스에 선택한 2건이며 전체 export 8건이 아니다.",
        "원문 이름과 설명에 위조 테스트라는 단서가 있어 필터 동작 확인용으로 제한한다.",
        "서명과 VC 본문을 새로 만들거나 수정하지 않았다.",
        "B/C 문맥 계획은 고정 후보에 대한 필터 결과이며 실제 생성 실행 결과가 아니다.",
        "현재 2개 케이스는 동일 family의 파생 사례이며 독립 학생 2명이 아니다.",
    ]
    draft["planned_context_record_ids"] = {
        "A_no_rag": [],
        "B_naive_rag": [clean_id, noise_id],
        "C_verified_rag": [clean_id],
    }

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
    print("Case: dev-backend-002")
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