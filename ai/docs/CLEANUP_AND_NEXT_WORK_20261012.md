# 프로젝트 정리 및 다음 작업 — 2026-10-12

## 보존한 연구 자료

현재 연구 코드·스키마·설정·데이터·테스트 및 VC/report 공동자료는 기존 위치를 유지했다. v2와 v2.1은 공통 구현을 사용하므로 함께 보존한다. 기존 `outputs/paper_study/manual-pilot-v1`의 원본과 최신 [36건 실험 자료](../research_artifacts/20261011/README.md)를 다음 작업의 기준으로 사용한다.

기존 20261011-39974fff, 20261011-role-clarified, manual-pilot-v1 자료를 이름이나 날짜만 보고 혼합하지 않는다. 각 실행 잠금이 고정한 입력·설정을 기준으로 확인한다.

## 로컬 정리

과거 backend_education, backend_previews, backend_qwen, llm_smoke, minimal_pipeline, runs, test_handoff_20261008 출력 폴더 및 Python/pytest 캐시를 로컬 보관 폴더로 이동했다. 총16폴더·549파일이며 영구 삭제하지 않았다. 코드에서 참조하는 vc_exports, backend_exports 및 연구 출력은 유지했다. 이전 보고서의 옛 출력 경로가 필요하면 로컬 분류표로 보관 위치를 찾거나 복원한다.

전체 저장소(.git·숨김·ignored 포함), 이전 작업 폴더 및 인계문 4,986파일을 로컬 ZIP에 백업하고 CRC·SHA-256을 대조했다. 다음 작업용 사본439파일도 별도 모았다. 로컬 정리 모음 위치는 `%USERPROFILE%/Documents/Codex/2026-10-12/new-chat/outputs/VC_AI_CLEANUP_20261012`다. 그 안의 README, classification.csv, move_plan.json에 경로 대응과 복원 방법이 있다. 이 같은 디스크의 백업·보관본·중복 작업 사본은 Git에 넣지 않았다.

## 검증 및 다음 단계

정리 후 관련 Python 테스트167개와 VC 연구 테스트3개 통과. 원본과 다음 작업 사본의 36건 결과 검사 모두 성공36/실패0/누락0/길이제한3을 확인했다. 코드와 결과 원본 해시는 유지됐다. VC 테스트 실행 캐시만 갱신됐다.

본 실험과 사람 평가는 별도다. 사람 검토·선언·승인 사실을 임의로 생성하지 않는다. [프로토콜](paper_experiment_protocol_v2_1.md), [실행 도구 안내](PAPER_DEVELOPMENT_RUNNER.md), [연구 보고서](reports/20261011/README.md)를 바탕으로 다음 작업을 진행한다.
