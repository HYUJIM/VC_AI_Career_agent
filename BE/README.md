# BE 파트 - VC 검증 및 AI 데이터 서빙 백엔드 서버

본 프로젝트의 프론트엔드에서 제출한 자격증명(VC)을 DXWorks API를 통해 검증하고, 그 결과를 DB에 저장한 뒤, AI 에이전트가 이해할 수 있는 정제된 형태(JSON)로 제공하는 **BFF(Backend For Frontend) 서버**입니다.

## 1. 아키텍처 흐름도 (Flowchart)

```mermaid
sequenceDiagram
    participant FE as 프론트엔드 (사용자)
    participant BE as 백엔드 (BE)
    participant DX as DXWorks API (검증/발급)
    participant DB as PostgreSQL DB
    participant AI as AI 에이전트

    %% 자격증명 검증 시나리오
    rect rgb(240, 248, 255)
    Note over FE, DB: [시나리오 1] 자격증명 제출 및 검증 (POST /api/v1/verify)
    FE->>BE: 1. 자격증명 토큰 제출 (vpToken 또는 vcJwt)
    BE->>DX: 2. 토큰 검증 요청
    DX-->>BE: 3. 검증 결과 및 페이로드 반환 (Valid)
    BE->>BE: 4. JWT 페이로드 파싱 (이름, 배지명, 성취기준 등 추출)
    BE->>DB: 5. DB에 검증된 스펙 커리어 정보 저장 (INSERT/UPSERT)
    BE-->>FE: 6. 검증 완료 응답
    end

    %% AI 전처리 데이터 요청 시나리오
    rect rgb(255, 245, 238)
    Note over AI, DB: [시나리오 2] AI의 이력서 작성 데이터 요청 (GET /api/v1/subjects/:did/careers)
    AI->>BE: 1. 특정 유저(DID)의 검증된 스펙 요청
    BE->>DB: 2. DB에서 유저 정보 및 수료증 목록 조회
    DB-->>BE: 3. 데이터 반환
    BE->>BE: 4. AI용 JSON 스키마로 포맷팅 (전처리)
    BE-->>AI: 5. 정제된 스펙 데이터 응답
    AI->>AI: 6. 환각(Hallucination) 없는 AI 이력서 작성
    end
```

## 2. 요구 사항 및 초기 설정

- Python 3.9 이상 권장
- PostgreSQL 15 이상 (로컬 도커 활용 권장)

처음 레포지토리를 클론받은 직후 아래 명령어들을 실행하세요.

```bash
cd BE
pip install -r requirements.txt
cp .env.example .env
```
> **주의:** `.env` 파일 안의 `DXWORKS_API_KEY` 값은 발급받은 실제 API 키를 입력해야 검증 및 발급이 정상 작동합니다.


## 3. 실행 방법 (DB 및 서버 켜기)

이 프로젝트는 PostgreSQL DB를 도커(Docker) 컨테이너로 손쉽게 실행할 수 있습니다.

### 1) 로컬 DB (Docker) 켜기 및 끄기
```bash
# 1. DB 켜기 (백그라운드 실행, 최초 실행 시 테이블 자동 생성)
docker compose up -d

# 2. DB 단순 종료 (데이터는 로컬 볼륨에 안전하게 보존됨)
docker compose down

# 3. DB 완전 초기화 (데이터 싹 삭제, 빈 DB로 리셋하고 싶을 때)
docker compose down -v
```

### 2) 백엔드 서버 (FastAPI) 켜기
코드를 수정할 때마다 자동으로 재시작되는 개발 모드를 권장합니다.
```bash
# 개발 모드 (포트 3000번 강제 지정)
python -m uvicorn main:app --reload --port 3000

# 빌드 및 운영 모드
python -m uvicorn main:app --host 0.0.0.0 --port 3000
```
- **기본 포트:** `3000` (Base URL: `http://localhost:3000`)
- **API 문서 (Swagger UI):** 서버를 켜고 `http://localhost:3000/docs` 접속 시 FastAPI가 자동 생성한 인터랙티브 API 문서를 확인하고 직접 테스트해 볼 수 있습니다.


## 4. API 시나리오 및 Postman 연동 테스트

포스트맨(Postman) 등에서 아래 순서대로 호출하며 메인 시나리오를 테스트할 수 있습니다.

### [Step 0] 웹 기반 유저 회원가입 및 로그인 (DID 자동 부여)
지갑 앱 없이 프론트엔드에서 회원가입하고 DID를 발급받아 세션에 저장하는 용도입니다.

**회원가입**
- **Method:** `POST`
- **URL:** `http://localhost:3000/api/v1/users/register`
- **Body (JSON):**
  ```json
  {
    "name": "홍길동",
    "email": "hong@test.com"
  }
  ```

**로그인**
- **Method:** `POST`
- **URL:** `http://localhost:3000/api/v1/users/login`
- **Body (JSON):**
  ```json
  {
    "email": "hong@test.com"
  }
  ```
> **Tip:** 위 API를 호출하면 응답 결과의 `data.did` 항목에 가짜 DID(예: `did:key:z6MkMock...`)가 반환됩니다. 이후 Step 1~3에서 이 DID를 사용하세요.

### [Step 1] 논문 테스트용 임시 발급 서버 (Mock Issuer)
- **Method:** `POST`
- **URL:** `http://localhost:3000/api/mock-issuer/issue`
- **Body (JSON):**
  ```json
  {
    "templateId": "f9499c5f-6611-45c6-b5a8-683a99040aab", 
    "holderDid": "Step 0에서 가입/로그인으로 발급받은 DID 입력"
  }
  ```
- **결과:** DXWorks 발급 API를 찔러서 생성된 서명된 JWT 문자열이 리턴됩니다. (이 값을 변수로 저장하세요.)

### [Step 2] 발급받은 배지를 검증하고 DB에 저장하기
- **Method:** `POST`
- **URL:** `http://localhost:3000/api/v1/verify`
- **Body (JSON):**
  ```json
  {
    "type": "badge",
    "token": "Step 1에서 받은 긴 JWT 문자열"
  }
  ```
- **결과:** 검증이 성공하면 PostgreSQL 스키마 규격에 맞춰 **실제 연결된 DB의 `credentials` 테이블**에 물리적으로 데이터가 저장됩니다.

### [Step 3] AI가 이력서 작성을 위해 유저 스펙 조회하기
- **Method:** `GET`
- **URL:** `http://localhost:3000/api/v1/subjects/{Step 0에서 발급받은 DID}/careers`
- **결과:** 방금 저장된 데이터가 DB 작업자의 새로운 스키마 규격에 맞게 정제되어 출력됩니다.
  ```json
  {
    "user_profile": {
      "id": "uuid...",
      "did": "did:key:...",
      "email": "hong@test.com",
      "name": "홍길동"
    },
    "verified_credentials": [
      {
        "record_id": "urn:uuid:...",
        "issuer": "경북대학교",
        "achievement_name": "KNU Career Demo Badge",
        "status": "verified",
        "issued_at": "2026-10-04T12:00:00.000Z",
        "raw_data": { ... }
      }
    ]
  }
  ```


## 5. [논문 테스트용] 대량의 더미 데이터 생성 스크립트

논문 및 AI 테스트를 위해 한 번에 수십 명의 유저와 수백 개의 자격증명(배지)을 DB에 삽입하고 싶다면, 내장된 데이터 시딩(Seeding) 스크립트를 사용하세요.

```bash
# BE 폴더 내부에서 실행 (DB 도커가 켜져 있어야 합니다)
python generate_test_data.py
```
- **동작 방식:** 이 스크립트는 실제 발급/검증 서버(DXWorks API)를 거치지 않습니다! 수백 번의 통신을 하면 네트워크 시간이 너무 오래 걸리기 때문에, "이미 검증이 끝난 완벽한 데이터"로 위장(Mocking)하여 PostgreSQL DB에 직접(Direct Insert) 밀어 넣습니다.
- **결과:** 
  1. `users` 테이블에 45명의 가상 유저가 무작위 생성됩니다.
  2. `credentials` 테이블에 인당 5~15개씩 랜덤 분배된 약 400~500여 개의 배지(토익, 부트캠프, 수상 내역 등 20종)가 꽂힙니다.
  3. `BE` 폴더 안에 **`test_users_did_list.csv`** 파일이 생성됩니다. 이 파일을 열어서 생성된 가짜 유저들의 이메일과 DID를 확인하고, **Step 3 API 테스트**에 활용하시면 됩니다.
> **Note:** 도커 볼륨을 완전히 삭제(`docker compose down -v`)하여 DB를 초기화했다면, 다시 이 스크립트를 돌려서 데이터를 채워 넣어야 합니다.
