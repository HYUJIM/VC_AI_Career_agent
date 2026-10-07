# VC 기반 RAG 실험: AI 1차 구현

## 목적과 현재 범위
동일 질의·모델 설정·프롬프트 구조로 A/B/C의 컨텍스트만 변경합니다.
A는 검색하지 않고, B는 전체 검색 후보를 사용하며, C는 같은 후보에서 verified만 남깁니다.
C는 검색 후 필터링하고 빈 자리를 다시 채우지 않습니다.
상태는 결과에 기록하지만 프롬프트에는 제공하지 않습니다.
Gold answer/record IDs/answerable/notes는 생성에 사용하지 않습니다.

## 환경 구축 (Anaconda Prompt, 저장소 루트)
```bat
conda activate vc-ai
cd ai
python -m pip install -r requirements.txt
```
PyTorch나 GPU 없이 아래 테스트가 가능합니다.
공용 스키마는 ../VC/src/schemas/ 원본을 직접 읽습니다. 수정·복제하지 않습니다.

## 실행
```bat
python scripts/validate_queries.py data/experiment_queries.jsonl
python -m pytest -q
python scripts/run_experiment.py --mock --queries data/experiment_queries.jsonl --credentials data/mock/credentials.json --top-k 6
```
6은 여섯 검증 상태를 포함시키는 데모 설정이며 논문용 top_k가 아닙니다.
10개 질의에 30개 결과가 생성됩니다. 한 질의만 실행하려면 JSONL의 한 줄만 별도 파일에 저장하여 --queries에 지정합니다.
출력: outputs/runs/mock-/실행UUID/ (결과 30개 + manifest.json).
반복 실행은 별도 디렉터리에 저장합니다.

## 입력 데이터
실험 질의 초안: data/experiment_queries.jsonl (10개).
가상 정규화 CredentialRecord: data/mock/credentials.json (6개 상태).
모든 샘플은 AI 배선 테스트용 수작업 가상 데이터이며 실제 VC 검증 결과가 아닙니다.
VC/fixtures/credentials의 원시 자격증명은 이 로더에 직접 넣을 수 없습니다.
실제 검증·정규화는 VC 파트가 수행합니다.
ListRetriever는 subject별 fixture 순서만 사용합니다. 의미 검색/DB retriever는 미구현입니다.
이 결과로 환각 감소나 검색 성능을 주장할 수 없습니다.

## 실제 모델 연결 (선택, 미실행)
.env.example을 .env로 복사하고 모든 모델 설정을 명시합니다.
LLM_PROVIDER=openai-compatible, LLM_BASE_URL은 로컬 서버의 /v1까지 지정합니다.
모델·양자화는 사용자가 선택하며 서버가 seed와 모든 생성 옵션을 지원하는지 확인해야 합니다.
같은 명령에서 --mock을 제거하면 해당 서버에 요청합니다. 서버 및 모델 설치는 포함하지 않습니다.
seed만으로 완전한 재현성이 보장되지는 않습니다.
실제 모델 서버 호출은 이번 검증에서 실행하지 않았습니다.

## API
WalletClient는 HTTP 호출만 담당합니다. status=all 목록을 조회한 뒤 상세 레코드를 읽습니다.
모의 HTTP 테스트만 통과했으며 실제 VC/BE 서버 연동은 별도 확인이 필요합니다.

## 테스트 실행법
python -m pytest -q
조건 필터, 정답 유출, subject 격리, 스키마, HTTP 클라이언트를 검사합니다.

## 미구현/미확정
Judge 및 평가 산식, 실제 임베딩 검색은 구현하지 않았습니다.
JudgeEvaluation 스키마는 점수를 필수 숫자로 요구하므로 산식 없이 가짜 0점 결과를 저장하지 않습니다.
실제 모델을 사용한 A/B/C 실행은 남아 있습니다.
# BE 데이터 연결 (2026-10-06 추가)

FastAPI 백엔드의 `/api/v1/subjects/{did}/careers`를 받는 별도 경로가 추가되었습니다.
실행법과 입력 규칙은 [BACKEND_INTEGRATION.md](BACKEND_INTEGRATION.md)를 참고하세요.
`export_backend_credentials.py`로 수집하고 `run_backend_preview.py`로 GPU 없이 확인합니다.
BE 응답의 상태값만으로 실제 VC 검증을 가정하지 않으며 기존 연구용 A/B/C 경로는 유지합니다.
