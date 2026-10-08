# BE Qwen 단일 질문 실행

2026-10-08: 기존 저장소를 변경하지 않은 작업 복사본이다. 기존 연구 A/B/C 경로는 수정하지 않았다.

## 확인 결과

- 첨부 ZIP의 7개 파일과 복사본 입력이 바이트 단위로 일치한다.
- 재생성 prompt는 첨부 preview와 일치한다.
- 고정 Qwen revision: `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`.
- 전체 12건 입력: 1830토큰 / 제한 2048, 여유 218토큰.
- 근거가 빈 동일 질문 입력: 164토큰. 전체 근거 추가에 따른 차이: 1666토큰.
- 입력 제한은 출력 예산과 별개인 기존 코드 제한이다. 출력은 기존 128토큰으로 시작한다.
- 이번 질문에서는 근거 선택/절단이 필요 없다. 다른 질문은 다시 측정한다.
- 기존 54개 및 신규 5개 테스트: 59 passed.
- 토큰 측정 세션은 PyTorch DLL 보안 차단 때문에 USE_TORCH=0을 사용했다. 모델/토크나이저 파일이나 패키지는 변경하지 않았다.
- 실제 GPU 추론은 아직 수행하지 않았다. 답변, 출력 토큰, 생성 시간, GPU 메모리 결과는 없다.

## Windows Anaconda Prompt

```bat
conda activate vc-ai
cd /d C:\Users\bje10\.codex\.chatgpt-projects\g-p-6abbcc05c98481918cde46f03cf34db0\ai
set USE_TORCH=
python scripts/run_backend_qwen.py --export outputs/backend_exports/630ebd28-d305-4ef8-a898-3e2114071d2f/backend_evidence.json --question-file data/backend_demo/question.txt
```

저장된 export로 실행하므로 Docker/BE 서버를 시작할 필요 없다. 기본 모델 캐시만 읽는다. 실행 후 출력되는 Saved 폴더가 이번 실행 결과다. 실패한 경우에도 manifest.json에 실패 단계와 오류가 남는다. `status=completed`와 `inference_performed=true`를 확인한다. WinError 4551이 다시 발생하면 오류를 전달하고, 보안 정책을 해제하거나 패키지를 재설치하지 않는다.

토크나이저만 다시 점검하려면 아래를 별도 Prompt에서 실행한다.

```bat
set USE_TORCH=0
python scripts/run_backend_qwen.py --export outputs/backend_exports/630ebd28-d305-4ef8-a898-3e2114071d2f/backend_evidence.json --question-file data/backend_demo/question.txt --check-only
set USE_TORCH=
```

## 저장 내용

- manifest.json: 실행 상태, 입력 토큰, 고정 revision, 입력/프롬프트/실행기 해시, 전체 소요 시간, 모델 로드 시간 및 환경 정보.
- evidence.json: 질문, 전체 원본 레코드 선택 여부와 제외 사유, 실제 입력 근거.
- prompt.txt 및 rendered_prompt.txt: 원본 및 채팅 템플릿 적용 입력.
- result.json 및 answer.txt: 성공 시 답변, 입력/출력 토큰, CUDA 동기화 생성 시간, peak allocated/reserved bytes, finish_reason.
- peak 메모리는 PyTorch allocator 측정값이며 PC 전체 GPU 사용량과 동일하지 않다.

## 답변 검토 기준 (실행 전 정의)

질문: 제공된 자료에 나타난 교육 이름과 이수 시간을 근거 ID와 함께 정리해 주세요.

명확한 교육 수료 근거:

| 교육 | record_id | 시간 관련 해석 |
|---|---|---|
| 우아한테크코스 6기 수료 | 532baf02-c738-45ca-a1de-2d4442c8b04c | 이수 시간 미제공 |
| 클라우드 아키텍처 부트캠프 수료 | 2dff4dd7-2a03-4136-ab98-c5c048b4b179 | 이수 시간 미제공 |
| 백엔드 개발자 부트캠프 수료 | e353fcff-d264-4477-9937-b502c505c55e | 설명에는 6개월 과정, 시간 단위 이수량은 미제공 |

소프트웨어 공학 A+는 과목/성적 기록으로 구분할 수 있지만 교육 프로그램 수료로 단정하지 않는다. 자격증·수상은 교육 수료로 바꾸지 않는다. 발급일을 수료일로 치환하거나 6개월을 시간으로 환산하면 근거 없는 주장이다. expires_at=null은 무기한 유효의 증거가 아니다.

실제 결과 수신 후 교육명/인용 ID/근거 없는 시간/누락/문장 완결성을 대조한다. finish_reason=length이면 답변 잘림을 따로 평가하고, 필요하면 기존 허용 범위인 256토큰으로 별도 재실행한다. BE verified는 실제 VC 검증이나 논문 조건 C가 아니다.

## 2026-10-08 후속 상태
위 사전 점검 이후 실제 추론 4회와 결과 분석을 완료했다. 원래 저장소에 신규 코드와 결과를 반영했으며 앞으로 작업 디렉터리는 C:\Users\bje10\VC_AI_Career_agent\ai를 사용한다. 최신 인수인계는 VC_AI_BE_QWEN_HANDOFF_20261008.md를 참조한다. 기존 실행 전 상태 설명은 이 후속 상태로 갱신된다.
