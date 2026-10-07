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

## 2. 원클릭 개발 환경 구축 (Docker 기반 권장 ⭐️)

로컬에 파이썬(Python)이나 PostgreSQL을 직접 설치할 필요가 없습니다. Docker Desktop만 켜져 있다면, **단 두 줄의 명령어**로 파이썬 패키지 설치, DB 세팅, 440여 개 테스트 데이터 시딩, FastAPI 서버 실행까지 모든 환경이 자동으로 한 번에 구축됩니다.

### 🚀 최초 1회 실행 방법
```bash
# 1. BE 디렉터리 이동 및 환경변수 예시 복사
cd BE
cp -n .env.example .env

# 2. 도커로 전체 환경 자동 빌드 및 백그라운드 실행
docker compose up --build -d
```
> **⚠️ 주의 (API 키 설정):** 
> 실제 발급/검증 테스트(Step 1, Step 2)를 수행하려면 `.env` 파일의 `DXWORKS_API_KEY` 항목에 발급받은 실제 API 키를 입력해야 합니다. (더미 조회 테스트는 키 없이도 즉시 가능합니다.)

> **💡 이 명령어 하나로 자동 완료되는 항목들:**
> 1. **Python 3.11 런타임 & 의존성(`requirements.txt`)** 도커 컨테이너 내 자동 설치 (로컬 파이썬 버전 충돌 0%)
> 2. **PostgreSQL 16 + pgvector** 컨테이너 자동 구동
> 3. **440여 개 논문 테스트 데이터(`03_seed_data.sql`)** DB에 자동 시딩
> 4. **FastAPI 백엔드 서버(포트 3000)** 자동 실행
> 5. **핫 리로드(Hot Reload)** 연동: 로컬 소스 코드(`BE/`)를 수정하고 저장하면 컨테이너 내부 서버가 즉시 자동 반영됩니다.

---

## 3. 컨테이너 관리 명령어

```bash
# 실행 상태 확인
docker compose ps

# 백엔드 서버 실시간 로그 확인
docker compose logs -f backend

# 서버 및 DB 일시 종료 (데이터는 보존됨)
docker compose down

# DB 및 데이터 완전 초기화 (초기 시드 상태로 깨끗하게 리셋할 때)
docker compose down -v
docker compose up -d
```
* **API 문서 (Swagger UI):** [http://localhost:3000/docs](http://localhost:3000/docs)
* **헬스 체크:** [http://localhost:3000/health](http://localhost:3000/health)

<details>
<summary><b>(선택) 도커 대신 로컬 파이썬 환경에서 직접 띄우고 싶을 때</b></summary>

로컬 IDE 디버거 등을 위해 호스트에서 직접 실행하려는 경우:
1. DB만 도커로 켬: `docker compose up -d postgres`
2. 의존성 설치: `pip install -r requirements.txt` (Mac/Linux는 `python3 -m pip install -r requirements.txt`)
3. 서버 실행: `python -m uvicorn main:app --reload --port 3000`
</details>


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

> **💡 참고:** 현재 도커 최초 실행 시 `03_seed_data.sql`에 의해 **45명의 유저와 440여 개의 배지 데이터가 이미 DB에 자동 탑재**되어 있습니다. 새로운 무작위 데이터로 다시 생성하고 싶을 때만 아래 명령어를 실행하세요.

```bash
# 1) 도커 컨테이너를 통해 실행하는 경우 (로컬 파이썬 불필요, 권장 ⭐️)
docker compose exec backend python generate_test_data.py

# 2) 로컬 파이썬 환경에서 직접 실행하는 경우
python3 generate_test_data.py  # 또는 python generate_test_data.py
```
- **동작 방식:** 이 스크립트는 실제 발급/검증 서버(DXWorks API)를 거치지 않습니다! 수백 번의 통신을 하면 네트워크 시간이 너무 오래 걸리기 때문에, "이미 검증이 끝난 완벽한 데이터"로 위장(Mocking)하여 PostgreSQL DB에 직접(Direct Insert) 밀어 넣습니다.
- **결과:** 
  1. `users` 테이블에 45명의 가상 유저가 무작위 생성됩니다.
  2. `credentials` 테이블에 인당 5~15개씩 랜덤 분배된 약 400~500여 개의 배지(토익, 부트캠프, 수상 내역 등 20종)가 꽂힙니다.
  3. `BE` 폴더 안에 **`test_users_did_list.csv`** 파일이 생성됩니다. 
     - ⚠️ **주의:** 스크립트를 실행할 때마다 기존 CSV 파일은 지워지고, **방금 DB에 새롭게 들어간 최신 유저 정보로 덮어쓰기** 됩니다. 항상 가장 최근에 만들어진 CSV 파일의 DID를 복사해서 **Step 3 API 테스트**에 활용하시면 됩니다.
> **Note:** 논문 작성이 모두 끝나고 DB를 완전히 빈 통으로 초기화하고 싶다면 `docker compose down -v`를 치시면 됩니다.
