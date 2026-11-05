# 교육 3건 진단 v1

`--education-diagnostic`는 수동 검토한 이번 사용자/export의 교육 수료 3건을 선택한다. 자동 검색 성능, 일반적인 교육 분류, 실제 VC 검증 또는 논문 조건 C를 평가하지 않는다. 기존 A/B/C 경로는 변경하지 않았다.

선택: 우아한테크코스 6기 수료, 클라우드 아키텍처 부트캠프 수료, 백엔드 개발자 부트캠프 수료. 앞 두 건은 시간/기간 미제공, 마지막은 시간 단위 이수량 미제공이며 설명에 6개월 과정이 있다. 원문 제목과 설명, DID가 검토한 자료와 다르면 추론 전에 중단한다. 이는 이 고정 사례용 수동 기준이며 범용 기간 추출기가 아니다.

질문은 3줄 `교육명 | 이수 시간 | record_id` 형식을 요구한다. 검증기는 각 ID의 정확한 교육명 및 시간 표현을 비교한다. 누락, 중복, 미선택 ID, 근거 없는 기간, 형식 위반, EOS 이외 종료는 검토 실패다. 의미가 같은 다른 표현도 엄격한 형식 검사에서 실패할 수 있으므로 실패 이유를 구분해서 읽는다. 정답 기준은 reference.json에 저장하며 모델 입력에 정답표를 삽입하지 않는다. 모델은 기존 근거 snippet과 질문을 받는다.

```bat
python scripts/run_backend_qwen.py --export outputs/backend_exports/630ebd28-d305-4ef8-a898-3e2114071d2f/backend_evidence.json --education-diagnostic --max-new-tokens 256
```

`--check-only`를 덧붙이면 토큰 사전 점검만 한다. 모델/revision/FP32/eager 설정과 2048 입력, 256 출력 상한 유지.

새 UUID 폴더의 answer.txt/result.json에는 원래 답변을 보존한다. reference.json은 수동 검토 기준, review.json은 검토 오류 목록이다. manifest의 status=completed는 실행 완료이며, review_status=passed/failed가 답변 검토 결과다. 검토 실패도 실행 자체가 완료됐으면 프로세스는 정상 종료하므로 자동 소비자는 반드시 review_status를 확인해야 한다. 기존 결과와 Git 저장소는 덮어쓰지 않았다.

검증: 기존 59개 + 신규 진단 검증 10개, 총 69개 테스트 통과. 실제 고정 토크나이저 사전 점검 통과. 이 진단의 실제 GPU 추론은 아직 실행하지 않았다.

## 2026-10-08 후속 상태
위 실행 전 기록 이후 실제 진단 실행 완료: 2626dc1c-b06f-4031-92d0-a59694e1b613. 입력727/출력149, EOS. 엄격검사 failed, 별도 v2 검토 needs_review. 최종 테스트는 원래 저장소에서 80개 통과. 최신 상태와 다음 작업은 VC_AI_BE_QWEN_HANDOFF_20261008.md를 참조한다.
