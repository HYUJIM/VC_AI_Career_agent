"""Build Work 2B case 001 draft without altering existing evidence."""

import copy
import hashlib
import json
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = AI_ROOT / "outputs" / "paper_vc_audit_v2.json"
OUTPUT_PATH = (
    AI_ROOT
    / "data"
    / "paper_experiment_v2"
    / "dev_drafts"
    / "dev-backend-001.draft.json"
)
EXTERNAL_ID = "cred-ob2-signed-002"

JD_TEXT = (
    "실험용 합성 채용공고: 주니어 백엔드 개발자\n"
    "주요 업무: REST API 개발, 관계형 데이터베이스 설계 및 SQL 작성, "
    "서버 기능 테스트와 유지보수.\n"
    "요구 역량: Java 또는 Python 기초, HTTP 이해, "
    "관계형 데이터베이스와 정규화·인덱스에 대한 이해.\n"
    "우대 사항: Spring Boot 또는 FastAPI 프로젝트 경험, "
    "자동화 테스트 및 서비스 배포 경험.\n"
    "이 공고는 실제 기업의 채용공고가 아닌 연구용 합성 자료이다."
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def read_json(path):
    payload = path.read_bytes()
    return payload, json.loads(payload.decode("utf-8-sig"))


def main():
    require(not OUTPUT_PATH.exists(), f"출력 파일이 이미 있습니다: {OUTPUT_PATH}")

    audit_bytes, audit = read_json(AUDIT_PATH)
    require(
        audit["audit_version"] == "paper-vc-adapter-v2",
        "예상한 Work 2A 감사 형식이 아닙니다.",
    )
    require(
        audit["summary"] == {"total": 8, "eligible": 3, "excluded": 5},
        "Work 2A 감사 건수가 예상과 다릅니다.",
    )
    require(
        audit["policy"]["name"] == "signed-fixture-v2",
        "감사 정책이 예상과 다릅니다.",
    )

    export_path = Path(audit["input_export"]["path"])
    export_bytes, records = read_json(export_path)
    require(
        sha256(export_bytes) == audit["input_export"]["sha256"],
        "감사 이후 입력 export가 변경되었습니다.",
    )
    require(
        len(export_bytes) == audit["input_export"]["byte_count"],
        "입력 export 바이트 수가 다릅니다.",
    )

    matches = [
        record for record in records
        if record["source"]["external_id"] == EXTERNAL_ID
    ]
    require(len(matches) == 1, "첫 케이스의 원천 레코드를 하나로 특정하지 못했습니다.")
    record = matches[0]
    record_id = record["record_id"]
    subject = record["subject_did"]

    audit_matches = [
        row for row in audit["records"]
        if row["record_id"] == record_id
    ]
    require(len(audit_matches) == 1, "대응하는 감사 결과가 없습니다.")
    record_audit = audit_matches[0]

    require(record_audit["eligible"] is True, "선택한 레코드가 적격이 아닙니다.")
    require(
        subject == audit["subject_did"] == "did:fixture:student-001",
        "학생 식별자가 예상과 다릅니다.",
    )
    require(
        record_audit["reported_verification"] == record["verification"],
        "감사 결과와 export의 검증 정보가 다릅니다.",
    )
    require(
        any(item == record for item in audit["eligible_records"]),
        "감사에 보존된 적격 레코드와 입력 레코드가 다릅니다.",
    )
    require(
        record["achievement"]["name"] == "데이터베이스 설계 인증",
        "선택한 인증의 명칭이 예상과 다릅니다.",
    )

    fact_id = "dev-backend-001-f01"
    candidate_fact = {
        "fact_id": fact_id,
        "entity_id": record["achievement"]["id"],
        "attribute": "achievement_name",
        "value": record["achievement"]["name"],
        "unit": None,
        "time_scope": None,
        "source_ids": [record_id],
    }

    draft = {
        "draft_version": "work2b-case-draft/0.1",
        "target_case_version": "paper-case/2.0",
        "case_id": "dev-backend-001",
        "family_id": "fixture-student-001",
        "split": "dev",
        "synthetic": True,
        "gold_status": "pending",
        "paper_ready": False,
        "evaluation_ready": False,
        "profile": {
            "subject_did": subject,
            "text": "백엔드 개발 직무에 지원하는 합성 학생 프로필.",
        },
        "jd": {
            "jd_id": "synthetic-backend-junior-001",
            "text": JD_TEXT,
            "sha256": sha256(JD_TEXT.encode("utf-8")),
        },
        "request": (
            "채용공고에 맞춘 짧은 포트폴리오를 작성하라. "
            "제공된 근거로 확인되는 보유 사실에는 record_id를 인용하라. "
            "JD 요구사항만으로 지원자의 경험이나 역량을 추론하지 마라. "
            "근거가 없는 요구 역량은 확인되지 않았다고 표시하고, "
            "향후 학습 제안과 현재 보유 사실을 분리하라. "
            "보유 사실의 근거가 없으면 그 부분은 판단을 보류하라."
        ),
        "source_records": [copy.deepcopy(record)],
        "record_audits": [copy.deepcopy(record_audit)],
        "eligible_record_ids": [record_id],
        "candidate_record_ids": [record_id],
        "noise": {
            "kind": "none",
            "target_ratio": 0,
            "raw_record_ids": [],
        },
        "observed_noise_exposure": {
            "raw": {"noise_count": 0, "total_count": 1, "ratio": 0.0},
            "candidate": {"noise_count": 0, "total_count": 1, "ratio": 0.0},
        },
        "gold_proposal": {
            "status": "pending",
            "candidate_facts": [candidate_fact],
            "proposed_allowed_fact_ids": [fact_id],
            "proposed_required_fact_ids": [fact_id],
            "independent_reference_facts": [],
            "independent_reference_status": "not_supplied",
            "closed_world": False,
            "expected_behavior_proposal": {
                "A_no_rag": "mixed",
                "B_naive_rag": "mixed",
                "C_verified_rag": "mixed",
            },
            "behavior_notes": {
                "A_no_rag": (
                    "지원자의 보유 사실은 판단 보류하고, "
                    "JD 기반의 일반 학습 제안만 가능하다."
                ),
                "B_naive_rag": (
                    "데이터베이스 설계 인증을 근거와 함께 기술하고, "
                    "그 밖의 요구 역량은 확인되지 않았다고 구분한다."
                ),
                "C_verified_rag": (
                    "이번 기준선에서는 B와 동일한 적격 레코드가 제공된다."
                ),
            },
            "review_notes": [
                "사실 후보는 VC에서 추출했으며 독립적인 진실 기준이 아니다.",
                "인증 기준 문구를 세부 실습 수행이나 숙련도 증거로 확대하지 않는다.",
                "Java, Python, Spring, FastAPI, 배포 경험을 보유 사실로 추가하지 않는다.",
                "향후 학습 제안과 지원자의 현재 보유 사실을 구분한다.",
                "expected_behavior는 사람 검토 전 예비 제안이다.",
            ],
        },
        "provenance": {
            "audit_path": str(AUDIT_PATH),
            "audit_file_sha256": sha256(audit_bytes),
            "input_export_path": str(export_path),
            "input_export_sha256": sha256(export_bytes),
            "original_id": EXTERNAL_ID,
            "reported_raw_hash": record["raw_hash"],
            "hash_notes": (
                "파일 해시는 이번에 읽은 감사/export 바이트의 해시다. "
                "reported_raw_hash는 기존 보고값이며 원본 VC를 재검증한 값이 아니다."
            ),
        },
        "target_schema_blockers": [
            {
                "field": "records[].verification.verifier_revision",
                "reason": "verifier_version은 있으나 코드 revision 증빙은 확인되지 않음",
            },
            {
                "field": "records[].verification.artifact_sha256",
                "reason": "해당 필드가 가리킬 검증 증빙 아티팩트의 정의와 연결 미확정",
            },
            {
                "field": "records[].verification.suite",
                "reason": "proof의 형식/알고리즘과 평가용 suite의 매핑 미확정",
            },
            {
                "field": "records[].verification.reference_time",
                "reason": "verified_at을 정책 기준 시각으로 사용할 수 있는지 미확정",
            },
            {
                "field": "records[].verification.checks[].detail",
                "reason": "일부 기존 check에 detail이 없으며 원문을 그대로 보존함",
            },
        ],
        "evidence_limitations": copy.deepcopy(audit["unavailable_evidence"]),
    }

    require(
        draft["source_records"][0] == record,
        "원천 레코드 보존 검사에 실패했습니다.",
    )
    require(draft["gold_status"] == "pending", "Gold 상태가 잘못되었습니다.")
    require(draft["family_id"] == "fixture-student-001", "family가 잘못되었습니다.")

    # 읽는 동안 원천 파일이 바뀌었는지도 확인한다.
    require(AUDIT_PATH.read_bytes() == audit_bytes, "감사 파일이 변경되었습니다.")
    require(export_path.read_bytes() == export_bytes, "export 파일이 변경되었습니다.")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(draft, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")

    print(f"Created: {OUTPUT_PATH}")
    print("Source binding: PASS")
    print("Source record preserved: PASS")
    print(f"Case: {draft['case_id']}")
    print(f"Family: {draft['family_id']}")
    print("Records: 1 / Eligible: 1 / Noise: 0")
    print("Gold: pending")
    print("Target schema: NOT VALIDATED (draft with documented blockers)")
    print("Evaluation ready: False")
    print("Paper ready: False")


if __name__ == "__main__":
    main()