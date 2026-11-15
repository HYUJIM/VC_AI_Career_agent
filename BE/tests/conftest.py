"""BE 테스트 공통 설정.

- DB: TEST_DB_* 환경변수의 PostgreSQL(pgvector)에 테스트 전용 DB를 새로 만들고, 배포와 같은 마이그레이션 실행기
  (app.migrate, 시드 포함)로 스키마 → 시드 → 마이그레이션을 적용한다.
  로컬 예 (기본 비밀번호이므로 이 PC에서만 연다):
  docker run -d --name vc-be-test -e POSTGRES_PASSWORD=password -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.7-pg16-bookworm
- 발급·검증 인프라: 실제 API 대신 포트(CredentialVerifier/CredentialIssuer)를 직접 구현한 FakeProvider를
  의존성 주입으로 넣는다. 외부 API의 요청·응답 형식은 tests/test_dxworks_adapter.py에서만 다룬다.
- AI 서비스: ai_app 픽스처가 ../ai의 앱을 같은 프로세스에 띄우고 BE의 실제 클라이언트를 ASGI로 잇는다 (소켓 없음).
- 테스트 값(발급기관, DID, ID)은 모두 가상의 값이다.
"""
import base64
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest

BE_DIR = Path(__file__).resolve().parents[1]
AI_DIR = BE_DIR.parent / "ai"
TEST_DB_NAME = os.getenv("TEST_DB_NAME", "vc_career_agent_test")
# 테스트 DB의 실제 비밀번호 (로컬 기본값). production_env가 DB_PASSWORD 환경변수를 바꿔도 테스트 DB 접속에는 이 값을 쓴다.
TEST_DB_PASSWORD = os.getenv("TEST_DB_PASSWORD", "password")

# app 모듈을 import하기 전에 환경을 고정한다 (.env보다 우선).
# 아래 비밀값은 이 공개 저장소에 들어 있는 테스트 전용 값이라 APP_ENV=production에서는 거부된다 (production_env 참고).
os.environ.update(
    {
        "DB_HOST": os.getenv("TEST_DB_HOST", "localhost"),
        "DB_PORT": os.getenv("TEST_DB_PORT", "55432"),
        "DB_USER": os.getenv("TEST_DB_USER", "postgres"),
        "DB_PASSWORD": TEST_DB_PASSWORD,
        "DB_NAME": TEST_DB_NAME,
        "JWT_SECRET": "test-secret-for-pytest-only-0123456789",
        "SERVICE_API_KEY": "test-service-key",
        "MOCK_ISSUER_ENABLED": "true",
        # 개발자 PC의 BE/.env(실제 DXWorks 키·Google 비밀값 등)를 테스트에서 읽지 않는다: main.py의 load_dotenv()와
        # 테스트가 띄우는 하위 프로세스(마이그레이션 CLI, AI 서버)가 모두 이 값을 보고 .env를 건너뛴다 (python-dotenv 1.1+).
        "PYTHON_DOTENV_DISABLED": "1",
        "DXWORKS_API_HOST": "https://dx.invalid",
        "DXWORKS_API_KEY": "test-key",
        "DXWORKS_BADGE_TEMPLATE_ID": "tpl-default",
        "MOCK_ISSUER_TEMPLATE_IDS": "tpl",  # 기본 템플릿 말고 모의 발급에 허용하는 템플릿 (가상 값)
        # 테스트 전용 마스터 키 (32바이트 0x01 반복). 실제 키가 아니다.
        "HOLDER_KEY_ENCRYPTION_KEY": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
    }
)

def _conn_kwargs(dbname: str) -> dict:
    return dict(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
                password=TEST_DB_PASSWORD, dbname=dbname)


def _connect(dbname: str):
    return psycopg.connect(**_conn_kwargs(dbname), autocommit=True)


@pytest.fixture(scope="session", autouse=True)
def test_database():
    from app.migrate import migrate

    with _connect("postgres") as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    # 배포(빈 RDS)와 같은 실행기로 만들되 시드도 넣는다: 기본 스키마 → 시드 → DB/migrations (원장 public.schema_migrations 포함).
    migrate(seed=True, **_conn_kwargs(TEST_DB_NAME))
    yield
    with _connect("postgres") as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')


@pytest.fixture(autouse=True)
def reset_call_budgets():
    """프로세스 메모리에 두는 호출 한도(검증기 호출 몫, 거절된 배지 검증 창, 모의 발급 시도 수·실패 차단기, 기업용 조회
    몫·응답, OAuth 제공자(Google·Kakao·Naver)별 토큰 교환 몫)를 테스트마다 비운다.

    한 테스트가 쓴 몫이 다음 테스트의 429/503이 되지 않게 한다 (로그인 실패 제한을 test_auth_security가 비우는 것과 같다).
    """
    from app.api.oauth import kakao_exchange_budget, naver_exchange_budget, oauth_exchange_budget
    from app.services.public_view_gate import public_view_gate
    from app.services.verifier_budget import (
        mock_issue_attempts, mock_issue_breaker, rejected_verifications, verifier_budget,
    )

    def clear():
        verifier_budget.clear()
        rejected_verifications.clear()
        mock_issue_attempts.clear()
        mock_issue_breaker.clear()
        public_view_gate.clear()
        oauth_exchange_budget.clear()
        kakao_exchange_budget.clear()
        naver_exchange_budget.clear()

    clear()
    yield
    clear()


@pytest.fixture
def db():
    with psycopg.connect(
        host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
        password=TEST_DB_PASSWORD, dbname=TEST_DB_NAME, autocommit=True,
        row_factory=psycopg.rows.dict_row,
    ) as conn:
        yield conn


# ── 테스트용 VC-JWT / 가짜 발급·검증기 ───────────────────────────────

def _b64(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def make_vc_jwt(recipient_did: str, *, credential_id: str | None = None, name: str = "테스트 수료 배지",
                valid_until: str | None = None, issuer_name: str = "예시대학교") -> str:
    """서명 없는 테스트용 VC-JWT (W3C VC 2.0 / OB 3.0 구조, 가상 값). 서명 판정은 FakeProvider가 대신한다."""
    vc = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "id": credential_id or f"urn:uuid:{uuid.uuid4()}",
        "type": ["VerifiableCredential", "OpenBadgeCredential"],
        "issuer": {"id": "https://issuer.example.org/demo", "name": issuer_name},
        "validFrom": "2026-10-02T03:34:21Z",
        "credentialSubject": {
            "id": recipient_did,
            "achievement": {"name": name, "description": "테스트 배지", "criteria": {"narrative": "테스트"}},
        },
    }
    if valid_until:
        vc["validUntil"] = valid_until
    return f"{_b64({'alg': 'EdDSA', 'typ': 'JWT'})}.{_b64({'vc': vc, 'sub': recipient_did})}.c2ln"


def make_vp_jwt(holder_did: str) -> str:
    """서명 없는 테스트용 VP 토큰 (compact JWS 모양, 가상 값). FakeProvider는 iss를 홀더 DID로 돌려준다."""
    vp = {"@context": ["https://www.w3.org/ns/credentials/v2"], "type": ["VerifiablePresentation"]}
    return f"{_b64({'alg': 'EdDSA', 'typ': 'JWT'})}.{_b64({'iss': holder_did, 'vp': vp})}.c2ln"


class FakeProvider:
    """CredentialVerifier + CredentialIssuer 포트를 직접 구현한 가짜. 외부 API 형식을 전혀 모른다."""

    provider_name = "fake_provider"

    def __init__(self):
        self.badge_overrides: dict[str, dict] = {}
        self.issued: list[tuple[str, str]] = []
        self.error: Exception | None = None

    def set_badge_result(self, token: str, **fields) -> None:
        self.badge_overrides[token] = fields

    async def verify_badge(self, vc_jwt: str):
        from app.domain.credential import ProviderBadgeResult, decode_jwt_payload

        if self.error:
            raise self.error
        payload = decode_jwt_payload(vc_jwt) or {}
        vc = payload.get("vc", {})
        fields = dict(
            valid=True, signature_ok=True, issuer_trusted=True, credential_status="VALID",
            recipient_did=vc.get("credentialSubject", {}).get("id"), credential_id=vc.get("id"),
            issuer_name=vc.get("issuer", {}).get("name"), achievement_name=None, issued_at=None,
        )
        fields.update(self.badge_overrides.get(vc_jwt, {}))
        return ProviderBadgeResult(**fields)

    async def verify_presentation(self, vp_token: str):
        from app.domain.credential import PresentationResult, decode_jwt_payload

        if self.error:
            raise self.error
        # 테스트 편의: VP 토큰(make_vp_jwt)의 iss를 홀더 DID로 취급한다.
        holder_did = (decode_jwt_payload(vp_token) or {}).get("iss")
        return PresentationResult(verified=True, holder_did=holder_did, claims={"name": "가상학생"})

    async def issue_badge(self, template_id: str, holder_did: str):
        from app.domain.credential import IssuedBadge

        if self.error:
            raise self.error
        self.issued.append((template_id, holder_did))
        return IssuedBadge(vc_jwt=make_vc_jwt(holder_did))


@pytest.fixture
def fake_dx():
    return FakeProvider()


@pytest.fixture
def client(fake_dx, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.integrations import get_issuer, get_verifier
    from main import app

    get_settings.cache_clear()
    app.dependency_overrides[get_verifier] = lambda: fake_dx
    app.dependency_overrides[get_issuer] = lambda: fake_dx
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def ai_app(monkeypatch):
    """AI 서비스(../ai의 src.server.app)를 같은 프로세스에 띄우고 BE의 AI 클라이언트를 거기에 잇는다 (직무 비교·시장 통계 공용).

    BE의 실제 HTTP 클라이언트·헤더·형식 검사를 그대로 거치고 소켓만 ASGI로 바꾼다.
    """
    from app.core.config import get_settings
    from app.integrations import ai_service as module

    if str(AI_DIR) not in sys.path:
        sys.path.append(str(AI_DIR))  # 뒤에 붙여 BE 패키지가 우선한다.
    monkeypatch.setenv("CAREER_AI_SKIP_DOTENV", "true")
    from src.server.app import create_app

    app = create_app()
    # 같은 프로세스이므로 BE 클라이언트와 AI 서버가 같은 토큰 변수를 읽는다.
    monkeypatch.setenv("AI_SERVICE_URL", "http://127.0.0.1:8100")
    monkeypatch.setenv("AI_SERVICE_TOKEN", secrets.token_urlsafe(40))
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "false")
    monkeypatch.setattr(module.ai_service, "transport", httpx.ASGITransport(app=app))
    get_settings.cache_clear()
    yield app
    get_settings.cache_clear()


@pytest.fixture
def identity_enabled(monkeypatch):
    """신원 VP 검증(POST /verify type=identity)을 켠 서버 설정. 기본은 꺼져 있다 (IDENTITY_VERIFICATION_ENABLED)."""
    from app.core.config import get_settings

    monkeypatch.setenv("IDENTITY_VERIFICATION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def production_env(monkeypatch):
    """APP_ENV=production으로 앱을 돌리는 테스트용 환경. 넣은 값을 dict로 돌려준다 (예: X-Service-Key에 쓸 SERVICE_API_KEY).

    위의 고정 테스트 비밀값과 로컬 기본 DB 비밀번호는 공개된 값이라 production에서 거부되므로(app.core.config),
    운영 검사를 통과하는 값을 실행할 때마다 새로 만든다. 테스트 DB에는 실제 비밀번호(TEST_DB_PASSWORD)로 접속하도록
    접속 비밀번호만 바꿔 끼운다. 이 픽스처보다 먼저 받은 토큰은 JWT_SECRET이 바뀌므로 쓸 수 없다.
    """
    from app.core.config import get_settings
    from app.services import database

    values = {
        "APP_ENV": "production",
        "JWT_SECRET": secrets.token_urlsafe(48),
        "HOLDER_KEY_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
        "SERVICE_API_KEY": secrets.token_urlsafe(32),
        "DB_PASSWORD": secrets.token_urlsafe(24),  # get_settings() 검사용 값 (접속에는 쓰지 않는다)
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(database, "db_password", lambda: TEST_DB_PASSWORD)
    get_settings.cache_clear()
    yield values
    get_settings.cache_clear()


@pytest.fixture
def register(client):
    def _register(name: str = "테스트") -> dict:
        email = f"t-{uuid.uuid4().hex[:10]}@test.com"
        res = client.post("/api/v1/users/register", json={"name": name, "email": email, "password": "password1234"})
        assert res.status_code == 200, res.text
        body = res.json()
        return {"email": email, "token": body["access_token"], "did": body["data"]["did"], "id": body["data"]["id"],
                "headers": {"Authorization": f"Bearer {body['access_token']}"}}

    return _register
