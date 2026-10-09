# 논문 VC 검증 산출물 수용 계약 — proposed

읽기 확인 대상: outputs/vc_exports/7cd1b4d4-1040-45f1-a5db-d6f1150a4e86/credentials.json. 첫 OB2 자료는 status=verified이지만 signature=skip이다. 따라서 status 문자열만으로 논문용 서명 검증 성공을 선언할 수 없다. 기존 export와 BE/VC 코드는 변경하지 않는다.

| 기존 입력 | v2 자료 필드 | 조건 |
|---|---|---|
| record_id / source.external_id | records[].record_id / original_id | 원본 보존, 중복·다른 subject 차단 |
| subject_did | profile.subject_did | 모든 후보가 같은 주체 |
| raw_hash | records[].raw_sha256 | 원본 credential 바이트 해시인지 산출 방식부터 확인; normalized JSON 해시와 구별 |
| achievement / evidence / skills | records[].text, facts[].entity_id/attribute/value/unit | 원문 보존, 별도 정규화; skills 파생 매핑은 원문 사실과 구별 |
| issued_at / awarded_date / expires_at | facts의 개별 attribute | 발급일→이수일 변환 금지, null 보존 |
| verification.status/checks | records[].verification | 개별 check/result/detail/checked_at 보존 |
| 현재 export에 증빙 부족 | verifier_revision / policy_id / artifact_sha256 / suite / reference_time | 별도 verifier 실행 manifest 요구, 추정 금지 |

스키마 실패는 B/C 공통 V_raw 구성 전에 quarantine하고 수/이유/원본해시를 기록한다. 서명 실패·폐기·만료·신뢰 실패는 스키마 유효하다면 V_raw에 남기고 C 적격 목록에서 제거한다. 격리 성과를 C의 생성 성과로 귀속하지 않는다.

논문 signed-fixture 정책 제안: schema, did_resolution, signature, issuer_trust, revocation, expiration 필수 pass. 필수 check의 skip/missing/unknown은 적격으로 승격하지 않는다. anchor_match=skip은 정책에 명시적으로 허용된 경우만 가능하고 한계로 보고한다. 중복/상충 check는 수용 보류한다. reference_time 기준 만료·폐기 자료 스냅샷 및 DID/key/trust fixture 버전, verifier 코드/환경, 실제 서명 검증 원로그와 대상 credential 해시 연결이 필요하다. status와 필수 check 불일치는 수용 보류한다.

이는 아직 실행 어댑터가 아니다. Work 2에서 정책 승인 후 체크와 해시 검사를 구현한다. 현재 검증기의 checked_at을 실험 reference_time으로 임의 치환하지 않는다. hosted assertion은 signed-fixture 주 실험과 별도 층으로 두며 verified라는 이유로 합치지 않는다. eddsa-fixture-2024는 테스트 suite이며 기관 진위/표준 전체 적합성의 증명이 아니다. 정상 서명 내용 상충은 T 및 보류 정책으로 평가한다.
