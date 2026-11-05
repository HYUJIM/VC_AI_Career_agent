# 2~4차 실험 재현 안내

저장소만 받아서 2~4차 실험을 다시 돌리는 방법입니다. 필요한 입력 데이터는 `ai/research_artifacts/`에 들어 있습니다.

## 1. 준비물

| 항목 | 요구 사항 |
|---|---|
| GPU | NVIDIA GPU, VRAM 8GB 이상 (FP32 1.5B 모델이 약 7GB 사용). CUDA 11.8을 지원하는 드라이버 |
| Python | 3.11 (확인된 환경: conda 환경 `vc-ai`) |
| Node.js | 20 이상 (3·4차의 VC 서명 검증에 사용) |
| 디스크 | 모델 약 3.5GB + 결과물 수백 MB |

CPU만으로는 실행되지 않습니다. 실행기가 CUDA를 요구하며, CPU로 자동 전환하지 않습니다.

## 2. 설치

```bash
git clone https://github.com/HYUJIM/VC_AI_Career_agent.git
```

```bash
cd VC_AI_Career_agent && git checkout AI
```

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

설치 확인 (`True`가 나와야 합니다):

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

## 3. 모델

모델은 `Qwen/Qwen2.5-1.5B-Instruct`, 버전 `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`으로 고정되어 있습니다.
처음 실행할 때 준비·실행 스크립트에 `--allow-download`를 붙이면 이 버전을 받습니다. 받은 뒤에는 빼고 실행해도 됩니다.

## 4. 테스트 (GPU 불필요)

```bash
cd ai && python -m pytest -q tests/test_study02.py tests/test_study02_weighted.py tests/test_study03.py tests/test_study04_data.py
```

27개가 통과해야 합니다. 이후 명령은 모두 `ai` 폴더에서 실행합니다.

## 5. 실행

공통 규칙:
- 출력 폴더는 새로 만들어야 합니다. 스크립트는 기존 결과를 덮어쓰지 않습니다.
- 같은 입력은 같은 답을 냅니다(greedy, seed 42). 다만 GPU 종류나 드라이버가 다르면 일부 답이 달라질 수 있습니다.
- 아래 `OUT`은 원하는 새 폴더 경로로 바꾸세요. 저장소 밖을 권장합니다.

### 2차: 일반 문서 vs 미검증 VC vs 검증 VC (test 272건, 약 25분)

```bash
python scripts/study02_prepare.py --source research_artifacts/study01_reference --out OUT/study02 --split test --allow-download
```

```bash
python scripts/study02_run.py --split-dir OUT/study02/test --allow-download
```

```bash
python scripts/study02_evaluate.py --split-dir OUT/study02/test --source research_artifacts/study01_reference
```

```bash
python scripts/study02_weighted.py --eval-dir OUT/study02/test/eval --out OUT/study02/test/weighted
```

dev 점검은 `--split dev`로 같은 순서를 따릅니다.

### 3차: 저장된 verified 표시 vs VC 실검증 (test 448건, 약 40분)

```bash
python scripts/study03_prepare.py --source research_artifacts/study01_reference --out OUT/study03 --split test --allow-download
```

```bash
python scripts/study02_run.py --split-dir OUT/study03/test
```

```bash
python scripts/study03_evaluate.py --split-dir OUT/study03/test --source research_artifacts/study01_reference
```

준비 단계가 `VC/scripts/verify-study03.ts`로 서명 원본을 다시 검증합니다(`npx tsx` 사용).

### 4차: 1,000쌍 규모 (test 2,352건, 약 3시간 반)

```bash
python scripts/study03_prepare.py --source research_artifacts/study04_data --out OUT/study04 --split test --allow-download
```

```bash
python scripts/study02_run.py --split-dir OUT/study04/test
```

```bash
python scripts/study03_evaluate.py --split-dir OUT/study04/test --source research_artifacts/study04_data
```

데이터를 새로 만들고 싶다면 `python scripts/study04_build_data.py --out OUT/study04_newdata`를 쓰세요. 다만 서명 키와 기록 ID가 새로 생성되므로 답변이 조금 달라질 수 있습니다. 같은 조건으로 재현하려면 `research_artifacts/study04_data`를 쓰세요.

## 6. 결과물 위치

| 파일 | 내용 |
|---|---|
| `OUT/…/test/payloads.lock.json` | 고정된 입력과 조작 점검 결과 |
| `OUT/…/test/run/results/` | 답변 원문 |
| `OUT/…/test/eval/summary.json` | 지표 집계 (사례군 단위 95% CI 포함) |
| `OUT/…/test/eval/claims.csv` | 사람 검토용 판정표 |

## 7. 참고

- 설계 문서: `docs/paper_experiment2_design_v1.md`, `paper_experiment3_design_v1.md`, `paper_experiment4_design_v1.md`
- 모든 판정은 정규식 기반 자동 판정 초안입니다(`reviewer_status=pending`).
- 오래 걸리는 실행 중에는 PC가 절전 모드로 들어가지 않게 설정하세요.
