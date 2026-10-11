# 2026-10-11 개발 실험 자료

이 폴더는 이전 작업 폴더의 연구 자료를 원본 바이트 그대로 옮긴 보존 사본이다. `SHA256SUMS.json`에 파일별 해시를 기록했다. ZIP 중복본, 전체 프로젝트 백업, 캐시와 과거 데모 출력은 포함하지 않는다.

## 구성

- `outputs/dev36-experiment/`: 실제 생성 36건, 생성 메타데이터, 실행 잠금, 원장, 환경 정보, 보고서, 검토 양식.
- `outputs/dev36-experiment.lock.json`: 실행 준비 시점 잠금.
- `outputs/pilot-review-20261011/`: 이전 9건의 AI 검토 초안. 사람 확정 평가가 아니다.
- `work/`: 당시 생성·집계 보조 스크립트 원본 2개.

`private/`의 key JSON은 조건·기준 사실 대응표이며 인증 비밀키가 아니다. 최초 검토 시에는 review 패킷과 구분해 사용한다. 자료는 합성 개발 사례이며 실제 사람의 승인 기록을 새로 만들지 않았다.

## 상태

2 development families × 6유형 × A/B/C = 36답변. 저장 결과 검사: 성공36, 실패0, 누락0, 길이제한3. `paper_ready=false`, `semantic_metrics=null`, 사람 검토36건 대기. 이전9건과 이번36건을 독립 표본으로 합산하지 않는다. 인용 ID 존재는 인용 정확도를 뜻하지 않는다. 본 실험144건은 이 결과에 포함되지 않는다.

## 검사

`ai` 디렉터리에서 기존 프로젝트 Python 환경으로:

```powershell
python -B scripts/run_paper_experiment.py inspect --run research_artifacts/20261011/outputs/dev36-experiment
```

검사는 로컬 모델 tokenizer를 사용하지만 모델 추론을 새로 실행하지 않는다. 원본 실행 당시 경로·환경 정보를 재현 기록으로 유지했다. 과거 lock을 현재 경로에 맞춰 수정하지 않는다.

`summarize_dev36.py`는 현재 작업 디렉터리의 `outputs/dev36-experiment`에 보고서를 쓴다. `build_pilot_review.py`는 당시 컴퓨터의 절대경로를 사용한다. 두 파일 모두 역사적 원본이다. 재사용할 때 별도 작업 사본에서 입출력 경로를 지정하고, 보존한 원본 결과에 덮어쓰지 않는다.

## 다음 작업

`../../docs/paper_experiment_protocol_v2_1.md` 및 `../../docs/reports/20261011/README.md`와 함께 확인한다. `../../outputs/paper_study/manual-pilot-v1`의 world·서명 fixture·prepared와 다른 study 실행 묶음을 섞지 않는다. 실제 사람의 기준 사실 검토와 프로토콜 선언 등 본 실험 준비 요건을 먼저 확인한다.
