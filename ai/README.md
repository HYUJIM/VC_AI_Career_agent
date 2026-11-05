# AI 파트: VC 기반 AI 커리어 에이전트와 환각 연구

## 현재 상태 (2026-10-16)

VC 지갑의 경력·교육 기록을 AI가 읽고 채용공고(JD)에 맞춰 지원 문장을 쓸 때, 기록에 없는 내용을 만들거나(창조) 부풀리거나(과장) 바꾸는지(대체) 측정하고, VC 검증이 이를 막는지 실험합니다.

| 차수 | 비교 | 규모 | 결과 요약 | 문서 |
|---|---|---|---|---|
| 1차 | A 근거 없음 · B 전체 기록 · C 검증 기록 | 144건 | 검증 필터가 거짓 교육시간 전파를 막음 (교육시간 사후 분석) | [프로토콜 v2.1](docs/paper_experiment_protocol_v2_1.md) |
| 2차 | P 일반 문서 · U 미검증 VC · V 검증 VC (+ JD 압력) | 272건 | 거짓 전파 15/16 → 0/16, 대신 참인 정보도 14/16 → 0/16. VC 형식 효과 없음 | [설계](docs/paper_experiment2_design_v1.md) |
| 3차 | P · D 저장된 표시 신뢰 · VC 실행 시점 재검증 (+ 플래그·사후 대조), 변조 S0–S3 | 448건 | 변조 기록 처리 VC 160/160, D 0/160 | [설계](docs/paper_experiment3_design_v1.md) |
| 4차 | 3차 설계, VC 1,008개 + 같은 내용의 더미 1,008개 | 2,352건 | 변조 기록 처리 VC 840/840, D 0/840. 3차 결과 재현 | [설계](docs/paper_experiment4_design_v1.md) |

- 모델: `Qwen/Qwen2.5-1.5B-Instruct` (버전 `989aa79…` 고정), greedy, seed 42, 입력 2,048 / 출력 256 토큰, CUDA FP32
- 모든 판정은 정규식 기반 자동 판정 초안입니다(`reviewer_status=pending`). 사람 검토가 남아 있습니다.
- 데이터는 에이전트가 만든 합성 지원자이고, VC는 연구용 테스트 키와 fixture DID로 서명했습니다. 실제 기관 발급이 아닙니다.

## 빠른 시작

자세한 순서와 예상 시간은 **[docs/STUDIES_RUN.md](docs/STUDIES_RUN.md)** 에 있습니다.

```bash
conda create -n vc-ai python=3.11 -y
```

```bash
conda activate vc-ai
```

```bash
pip install -r ai/requirements.txt
```

```bash
pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu118
```

```bash
pip install -r ai/requirements-gpu.txt
```

```bash
cd VC && npm install && cd ..
```

GPU 없이 확인할 수 있는 테스트 (`ai` 폴더에서):

```bash
python -m pytest -q tests/test_study02.py tests/test_study02_weighted.py tests/test_study03.py tests/test_study04_data.py
```

모델 실행에는 NVIDIA GPU(VRAM 8GB 이상)가 필요합니다. 처음 실행할 때 스크립트에 `--allow-download`를 붙이면 고정 버전 모델을 받습니다.

## 주요 폴더

| 위치 | 내용 |
|---|---|
| `src/rag/study02.py`, `study02_eval.py` | 2차 입력 생성(P 일반 문서 변환, JD 압력), 창조·과장·대체 자동 판정기 |
| `src/rag/study03.py` | 3차 변조 시나리오, 시스템별 입력, 사후 대조기, 플래그 반영 채점 |
| `src/rag/study04_data.py` | 4차 합성 지원자 생성과 묶음 조립 |
| `scripts/study02_*.py`, `study03_*.py`, `study04_build_data.py` | 준비 · 실행 · 평가 · 가중치 분석 · 데이터 생성 |
| `research_artifacts/study01_reference/` | 2·3차 입력 데이터 (1차 묶음, 잠금 파일, 1차 답변, 실검증 결과) |
| `research_artifacts/study04_data/` | 4차 데이터 (VC 1,008개 + 더미 1,008개) |
| `data/study02/` | 가중치 설정 (`weights.recommended.json`이 기본값, 연구자 지정) |
| `docs/` | 실험 설계·프로토콜·재현 안내 |
| `requirements.txt`, `requirements-gpu.txt` | 기본 패키지, GPU 모델 실행 패키지 |

VC 실검증은 `../VC/scripts/verify-study03.ts`, 4차 서명은 `../VC/scripts/prepare-study04.ts`가 담당합니다.

## 연구 결과에서 말할 수 있는 것과 없는 것

- 말할 수 있음: 검증 결과에 따른 선별은 거짓 기록에서 오는 환각을 줄인다. 실행 시점 VC 검증은 저장 데이터가 변조돼도 환각 전파를 막고 참인 정보를 보존하며, 저장된 검증 표시를 믿는 시스템은 이를 막지 못한다.
- 말할 수 없음: VC 형식 자체가 AI를 더 정직하게 만든다(모델은 서명을 보지 못함). 적격 기록끼리의 충돌이나 JD에 맞춘 왜곡을 검증이 해결한다.
- 한계: 합성 데이터, 1.5B 모델 하나, DB 수준 변조만 다룬 위협 모델, 사람 검토 전 자동 판정.

## 초기 구현: Mock A/B/C 경로 (1차 이전)

연구 실험 이전에 만든 배선 테스트용 경로이며 그대로 남아 있습니다.

- 동일 질의·모델 설정·프롬프트 구조에서 컨텍스트만 바꿉니다. A는 검색 없음, B는 전체 후보, C는 같은 후보에서 verified만 남깁니다(빈 자리 재충원 없음).
- 입력: `data/experiment_queries.jsonl`(질의 10개), `data/mock/credentials.json`(가상 정규화 기록 6개). 모두 수작업 가상 데이터입니다.
- 실행 (GPU 불필요):

```bash
python scripts/run_experiment.py --mock --queries data/experiment_queries.jsonl --credentials data/mock/credentials.json --top-k 6
```

- 출력은 `outputs/runs/mock-<실행UUID>/`에 저장되며, 이 경로의 결과로는 환각 감소를 주장할 수 없습니다.
- OpenAI 호환 서버 연결은 `.env.example`을 `.env`로 복사해 설정합니다. 연구 실험(1~4차)은 이 경로가 아니라 로컬 Transformers 백엔드(`src/llm/transformers_llm.py`, [TRANSFORMERS_RUN.md](TRANSFORMERS_RUN.md))를 씁니다.
- `WalletClient`는 HTTP 호출만 담당하며, 실제 VC/BE 서버 연동은 별도 확인이 필요합니다.

## BE 데이터 연결 (2026-10-06 추가)

FastAPI 백엔드의 `/api/v1/subjects/{did}/careers`를 받는 별도 경로입니다.
실행법과 입력 규칙은 [BACKEND_INTEGRATION.md](BACKEND_INTEGRATION.md)를 참고하세요.
`export_backend_credentials.py`로 수집하고 `run_backend_preview.py`로 GPU 없이 확인합니다.
BE 응답의 상태값만으로 실제 VC 검증을 가정하지 않으며, 연구용 경로와 분리되어 있습니다.
