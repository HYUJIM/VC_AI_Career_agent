# BE 교육 계약 v2 제안 — 2026-10-08

상태: **proposed / pending BE agreement**. 작성 주체: 에이전트. BE 승인·운영 적용·사람 정답 검토는 완료되지 않았다. v1 계약과 사례, 규칙, 실행 결과는 변경하지 않는다.

## 조사 근거와 제품 연결 위치

AI HEAD 및 로컬 origin/AI: bfb9fa1a8743dca474b8a4fc1089226e85f4028e. 실제 원격 조회는 하지 않았다. AI 작업 트리는 ai.zip만 미추적이었다. BE HEAD: e0ad118f38a192a253a8fa20b90a77b93909d5c4, 변경 없음.

BE `main.py` → `app/api/user.py`의 GET `/api/v1/subjects/{did}/careers` → `app/services/database.py`에서 원문을 조회한다. verified_credentials라는 배열 이름과 달리 조회 SQL에는 status 조건이 없다. 이는 실제 검증 증거가 아니다.

AI `src/clients/backend_client.py` → `src/data/backend_adapter.py` → 저장 export → `src/rag/backend_education.py`가 현재 원문 표시 입력 경로다. `scripts/run_backend_preview.py`는 MockLLM용 CLI이며 서비스 UI가 아니다. VC `src/server/app.ts`는 credentials/skills/verify/health 라우트를 연결한다. 확인한 코드에는 교육 표시 소비 화면이나 AI 교육 서비스 API가 없다. 따라서 이번 연결은 기존 export/구조화 경로를 재사용하는 별도 실행 예제이며 **제품 미연결**이다. 별도 FE 저장소는 조사하지 못했다.

## 제안 envelope와 공통 규칙

기존 careers 응답과 raw_data를 보존하고 각 career에 선택적 `education_v2` 객체를 추가하는 방안을 제안한다. 위치와 명칭은 BE 합의 대기다. v2 생산자가 객체를 제공하면 아래 공통 키와 필드 상태는 필수이며 값 미제공은 객체 생략 대신 명시한다. 기존 생산자가 객체를 생략한 경우 v2 미지원으로 구분한다.

| 키 | 필수 여부와 의미 | 예시 / 제한 |
|---|---|---|
| schema_version | 객체 제공 시 필수 | `be-education/2.0-proposed` |
| record_id / subject_did | 필수, 상위 career/요청 사용자와 일치 | 불일치·UUID 중복은 전체 입력 차단 |
| original_name | 필수, achievement_name 원본 | 번역·요약·공백 정규화 없이 보존 |
| source | 필수, 원본 파일/응답 식별자·기관·출처 종류·해시 방식 | synthetic_seed / institution_record / user_upload / unknown; 출처 미확인을 실제 기관으로 바꾸지 않음 |
| education_kind | 상태 객체 필수 | education / qualification / award / course_grade / other |
| completion_status | 상태 객체 필수 | completed / in_progress / planned / not_completed; BE verified와 독립 |
| course_duration | 상태 객체 필수 | 비음수 value + year/month/week/day, 과정의 달력 기간 |
| attendance_start / attendance_end | 상태 객체 필수 | 실제 수강 date 또는 date-time; 정밀도·시간대 보존, 시작≤종료 |
| actual_learning_amount | 상태 객체 필수 | 비음수 value + hour/minute/credit/session; 실제 이수량 |
| planned_learning_amount | 상태 객체 필수 | 계획 총량, 실제 이수량으로 표시하지 않음 |
| learning_rate | 상태 객체 필수 | value + unit + per(day/week); 총량으로 계산하지 않음 |

모든 필드는 `{status, value, unit, evidence, method}` 구조를 사용한다. 범주/날짜 필드의 unit은 null. 수치에는 유한 비음수 숫자만 허용하며 명시된 0은 보존한다. 서로 다른 실제 이수 단위가 공존하면 배열로 표현할지 합의가 필요하다. 제안 초기안은 단일 값만 허용하고 다중 값은 ambiguous로 보류한다. 합산·기간→시간 환산·발급일→수강일 대체를 하지 않는다.

| status | 의미 | 값 정책 |
|---|---|---|
| provided | 출처에 명시되어 추적 가능한 값 | value 필수, evidence 1개 이상; 사람/암호 검증 완료를 뜻하지 않음 |
| not_provided | 명시한 검사 범위에 값이 없음 | value/unit=null, 검사 범위 기록 |
| not_applicable | 발급기관/근거 문서가 해당 없음 명시 | value/unit=null, 사유·근거 필수 |
| unconfirmed | 값의 의미/출처가 아직 미확인 | value/unit=null, 후보는 evidence에만 보존 |
| ambiguous | 계획·실제·주당량의 관계 또는 의미 모호 | value/unit=null |
| conflicting | 같은 속성에 양립 불가능한 근거 | value/unit=null, 상충 근거 모두 보존 |
| unassessable | 파서 지원 밖 또는 접근 불가능 | value/unit=null, 실패 범위·사유 기록 |

`evidence`는 source_id, 원문 경로(JSON Pointer 또는 문서 페이지/영역), quote, 위치를 담는다. 문자열 위치는 Unicode code point의 0-based [start,end), 원문 문자열의 slice와 인용 일치가 필요하다. JSON의 숫자 값은 pointer의 typed value를 함께 남긴다. 파일 바이트 SHA256과 canonical JSON SHA256은 구별해 hash_algorithm/hash_scope를 기록한다. 수집 시각은 발급일이 아니다. 원문에 출처 기관을 확인할 수 없으면 issuer는 null과 사유를 남긴다. hash는 원문 동일성 도구이며 발급 진위 보증이 아니다.

## 합성 설명 예시 (실제 자료/정답 아님)

원문: `교육 과정 4개월, 실제 72시간 이수. 계획 총 80시간, 주당 8시간.`

- course_duration: provided / 4 / month, 인용 `교육 과정 4개월`.
- actual_learning_amount: provided / 72 / hour, 인용 `실제 72시간 이수`.
- planned_learning_amount: provided / 80 / hour, 인용 `계획 총 80시간`.
- learning_rate: provided / 8 / hour / per=week, 인용 `주당 8시간`.
- attendance_start/end: not_provided / null / null.

이는 제안 의미 설명이며 현재 v1 파서가 이 문장을 모두 처리한다는 뜻이 아니다. 원문 및 필드별 정확한 span은 제공자가 작성·검증해야 한다.

## 호환 처리와 전파 계획

| 구간 | 현재 동작 | v2 합의 후 필요한 변경 |
|---|---|---|
| BE DB → careers | raw_data JSONB와 기본 키 | 원문 유지, education_v2 제공 주체/경로 결정 |
| careers → adapter | achievement allowlist만 복사 | 별도 v2 adapter로 확장 객체 보존·검증; v1로 강제 통과 금지 |
| adapter → export | be-evidence/1.0, additionalProperties=false | 새 export schema/version, identity 일치 검사, 원문 범위 표시 |
| export → 표시 | 기존 v1 규칙의 기간/시간만 처리 | v2 구조값과 규칙 추출을 출처별 분리, 상충 시 보류 |
| 표시 → 제품 UI/API | 소비 경로 미확인 | FE 담당자가 경로 확인 후 별도 연결 및 통합 테스트 |

이번 표시 v2의 버전은 표현 계약 버전이다. BE 계약 v2 지원 버전과 같지 않다. 기존 v1 export를 읽고 v1 규칙 결과에 검토·보류·근거 표시만 추가한다. 원문 범위 밖 필드를 읽었다고 표시하지 않는다. 입력이 새 미지원 버전이면 기존 validator가 차단한다. BE 신규 필드를 추가해도 현재 adapter가 보존한다고 가정해서는 안 된다.

## 외부 확인 요청표 — 모두 pending, 전송하지 않음

| 담당 | 구체적 확인/자료 | 수락 기준 |
|---|---|---|
| BE | education_kind enum, Completion/Certificate 매핑, 수료 기준 | 서면 의미 정의와 양/음성 예시 |
| BE | education_v2 위치·버전 정책·필수 키·미지원 처리 | 실제 응답 예제와 하위 호환 검토 |
| BE/기관 | 달력 기간/실제 수강일/실제·계획·주당량 구분 | 필드별 발급 원문 근거 |
| BE | not_applicable/unconfirmed/conflicting 책임과 다중 이수량 표현 | 결측·상충 응답 예제 |
| BE | 출처·해시 방식·원문 접근·상태 의미 | 사용자 격리 및 원문 추적 가능 |
| 자료 제공자 | 새 기관 자료, 원문 사용 허용 범위, 익명화 방식 | 기존 시드/템플릿 변형과 독립임을 기록 |
| 사람 검토자 | 작성자/독립 검토자/쟁점 최종 판정자 | 실명 또는 추적 가능한 내부 ID와 일시 |
| FE/제품 담당 | 실제 화면·저장소·소비 API·사용자 인증 경로 | 연결 지점 지정 및 통합 테스트 가능 |

BE 계약 제안의 합의와 별도로 실제 VC 검증 및 논문 조건 C에는 별도 검증 증거·평가 프로토콜이 필요하다.
