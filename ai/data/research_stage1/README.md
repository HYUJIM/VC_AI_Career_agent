# 연구 개발용 Stage1

2명의 독립 가상 인물, 12개 모의 기록, 12개 질문. 본 평가 데이터가 아니다.
verification.status는 수동 정책 시나리오이고 모든 개별 검증 check는 skip이다.
실제 암호학적 검증 성공으로 해석하지 않는다. 원본 이력서와 회사 API 키는 포함하지 않는다.

queries.jsonl에는 기존 계약 호환용 정답이 포함된다. 모델 프롬프트에는 query_text만 전달한다.
evaluation은 사람 평가용이다. 실행기에 평가 파일 전체를 프롬프트로 전달하지 않는다.

Anaconda Prompt에서 프로젝트의 ai 폴더로 이동한 뒤:

```bat
conda activate vc-ai
cd /d C:\Users\bje10\VC_AI_Career_agent\ai
python -m pytest -q
python scripts/run_experiment.py --mock --queries data/research_stage1/queries.jsonl --credentials data/research_stage1/credentials.json --candidate-sets data/research_stage1/candidate_sets.jsonl --top-k 2
```

각 인물의 질문 순서별 기대 근거 수:

| 질문 유형 | A | B | C |
|---|---:|---:|---:|
| 정상 | 0 | 1 | 1 |
| 충돌 | 0 | 2 | 1 |
| 출처 불명 | 0 | 1 | 0 |
| 폐기 | 0 | 1 | 0 |
| 만료 | 0 | 1 | 0 |
| 해당 근거 없음 | 0 | 1 | 1 |

마지막 조건에는 무관한 정상 자료가 남는다. 문서가 남았다고 질문에 답할 수 있는 것은 아니다.
top-k는 가장 큰 후보 목록(2개) 이상이어야 한다. 고정 후보를 조용히 잘라내지 않는다.
후보 파일 생략 시 기존 목록 순서 검색을 그대로 사용한다. 두 모드 모두 의미 검색은 아니다.

실제 모델 서버와 설정이 준비된 뒤에만 --mock을 제거한다.
결과 execution에 실제 프롬프트, 원래 후보, 제외 사유, 생성 소요 시간을 저장한다.
토큰 사용량과 검색 점수는 현재 수집하지 않아 null이다. 0으로 간주하지 않는다.
검증 상태와 제외 사유는 실행 로그에만 있고 모델 프롬프트에는 없다.
기존 7항목 검증 계약과 공유 결과 스키마는 변경하지 않았다.

모델 추론 실패 시 이미 완료한 조건의 파일을 보존하고 manifest.status=failed로 남긴다.
자동 재시도/실패 결과의 성능 점수 변환은 아직 구현하지 않았다.
