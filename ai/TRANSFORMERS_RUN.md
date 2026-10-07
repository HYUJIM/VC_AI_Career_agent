# 실제 Transformers 개발 실험

사용자 TITAN Xp에서 성공한 Qwen2.5-1.5B-Instruct 커밋
989aa7980e4cf806f80c7fef2b1adb7bc71aa306을 고정한다.
FP32, eager, greedy, seed=42, 최대 출력 128토큰을 사용한다.
GPU 추론은 사용자 PC에서 확인하며 이 변경의 자동 테스트는 가짜 백엔드로 실행 흐름만 검증한다.

이 실행기는 기존 Pipeline, 공통 로더와 FixedCandidateRetriever를 재사용한다.
기존 HTTP/Mock 실행기, .env, 공유 VC 스키마를 바꾸지 않는다.
모델은 1회 로딩하고 각 질문/조건에 대해 대화 이력이 없는 독립 입력을 만든다.
출력의 모형 효과는 아직 평가하지 않는다. 자격증명 상태는 여전히 모의 값이다.

## 실행

Stage1 패치(18개 테스트 통과)를 먼저 적용한 상태여야 한다.
추가 패키지 설치는 필요 없다. 사용자 확인 환경: torch 2.5.1+cu118,
transformers 4.51.3, safetensors 0.5.3. accelerate와 별도 서버를 사용하지 않는다.

```bat
conda activate vc-ai
cd /d C:\Users\bje10\VC_AI_Career_agent\ai
python -m pytest -q
python scripts/run_transformers_experiment.py
```

첫 smoke에서 사용한 모델 캐시를 기본으로 읽는다. 캐시 파일이 없다는 오류일 때만:

```bat
python scripts/run_transformers_experiment.py --allow-download
```

출력은 outputs/runs/run-/새UUID 아래의 manifest.json과 결과 36개다.
기존 실행 결과를 덮어쓰지 않는다. 각 요청의 진행 상황을 출력한다.
실패/중단 시 완료된 조건은 남으며 실패 질의·조건·단계를 manifest에 기록한다.
실패 후 재실행하면 새 폴더에서 처음부터 실행하며 자동 이어하기는 하지 않는다.

## 기록과 해석

- 결과에는 프롬프트, 필터 전 후보, 제외 사유, 실제 chat template 입력을 저장한다.
- input/output token 수, EOS/길이제한 종료 여부, CUDA 동기화 생성 시간과 메모리를 저장한다.
- 128토큰에 도달한 응답은 finish_reason=length로 표시한다. 답변이 잘렸을 수 있으므로 오답과 구분해 검토한다.
- 입력 2048토큰 초과는 조용히 자르지 않고 실패시킨다.
- manifest에 모델 커밋, 패키지 버전, GPU, 설정, 코드/데이터 해시를 남긴다.
- greedy를 나타내는 기존 model.temperature=0은 실제 sampling temperature=0을 전달한다는 뜻이 아니다.
- attention 설정의 use_sliding_window=false를 확인하며, 설정이 다르면 실행을 중단한다. 경고 문구만 없애려고 모델 설정을 바꾸지 않는다.
- 동일 파이프라인 지시문을 한 user message로 전달한다. smoke의 system/user 예제와 입력 문구는 다르며, 이 차이를 실행 메타데이터로 기록한다.
- 현재 실행 순서는 질의 순서대로 A/B/C다. 파일럿용이며, 본 실험의 실행 순서 무작위화와 통계 평가는 별도 단계다.

성공 시 마지막 Saved 36 real results 줄을 확인하고, 그 폴더를 압축하여 응답 검토에 사용한다.
이 자료에는 로컬 개발용 가상 데이터만 사용한다.
