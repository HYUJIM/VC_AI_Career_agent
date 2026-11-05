# v2 계약과 빈 양식

*.schema.json: Draft 2020-12 입력 구조. case/result/review/judge를 분리한다.
*.blank.json: 미작성 양식. null은 채워야 할 칸이며 실행 가능한 정답이 아니다. case/review/judge 양식은 작성 전 스키마 검증에 의도적으로 실패한다. schema의 중첩 properties를 보고 records/facts/claims/reviewers를 작성한다. 합성 테스트 값은 tests에만 있으며 본 데이터가 아니다.

freeze/ledger 양식은 계획용이며 현재 자동 검증 대상이 아니다. ledger.planned_runs 각 행: run_id, case_id, family_id, condition, seed, order_index. attempts 각 행: run_id, attempt_id, status, error, finish_reason, result_path, result_sha256, candidate_record_ids, context_record_ids, raw/candidate/context noise_count 및 total_count, input/output_tokens. quarantine: original_id, raw_sha256, reason. freeze.artifacts: path, byte_sha256; approvals: role, reviewer_id, approved_at, decision. 본 실행 전에 Work 2에서 ledger/freeze의 실행 스키마와 전역 일관성 검사를 구현한다.

최소 Python API: validate_schema(value, kind), validate_case(case), summarize_response(case, result, review), exposure(record_ids, noise_ids). 모델/네트워크 호출 없음. summarize_response는 canonical JSON SHA256으로 원답변과 사람 검토를 묶는다. 원본 파일의 바이트 해시는 lock manifest에 별도 보존한다. 응답별 집계만 제공하며 조건 간 통계/전체 실험 승인 기능이 아니다. eligible_record_ids와 gold_status의 의미적 적합성은 사람이 검토해야 한다. VC 필수 check 정책은 문서 제안이며 여기서 자동 실행하지 않는다.

AI 폴더에서: `python -B -m pytest -q -p no:cacheprovider tests/test_paper_evaluation_v2.py`
