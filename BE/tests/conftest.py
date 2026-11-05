"""BE 테스트 공통 설정.

- DB: TEST_DB_* 환경변수의 PostgreSQL(pgvector)에 테스트 전용 DB를 새로 만들고,
  docker-compose initdb와 같은 순서(스키마 → 시드 → 마이그레이션)로 SQL을 적용한다.
  로컬 예: docker run -d --name vc-be-test -e POSTGRES_PASSWORD=password -p 55432:5432 pgvector/pgvector:pg16
- 발급·검증 인프라: 실제 API 대신 포트(CredentialVerifier/CredentialIssuer)를 직접 구현한 FakeProvider를
  의존성 주입으로 넣는다. 외부 API의 요청·응답 형식은 tests/test_dxworks_adapter.py에서만 다룬다.
- 테스트 값(발급기관, DID, ID)은 모두 가상의 값이다.
"""
import base64
import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest

BE_DIR = Path(__file__).resolve().parents[1]
TEST_DB_NAME = os.getenv("TEST_DB_NAME", "vc_career_agent_test")

# app 모듈을 import하기 전에 환경을 고정한다 (.env보다 우선).
os.environ.update(
    {
        "DB_HOST": os.getenv("TEST_DB_HOST", "localhost"),
        "DB_PORT": os.getenv("TEST_DB_PORT", "55432"),
        "DB_USER": os.getenv("TEST_DB_USER", "postgres"),
        "DB_PASSWORD": os.getenv("TEST_DB_PASSWORD", "password"),
        "DB_NAME": TEST_DB_NAME,
        "JWT_SECRET": "test-secret-for-pytest-only-0123456789",
        "AUTH_REQUIRED": "false",
        "SERVICE_API_KEY": "test-service-key",
        "MOCK_ISSUER_ENABLED": "true",
        "DXWORKS_API_HOST": "https://dx.invalid",
        "DXWORKS_API_KEY": "test-key",
        "DXWORKS_BADGE_TEMPLATE_ID": "tpl-default",
        # 테스트 전용 마스터 키 (32바이트 0x01 반복). 실제 키가 아니다.
        "HOLDER_KEY_ENCRYPTION_KEY": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
    }
)

# docker-compose.yml의 docker-entrypoint-initdb.d 마운트 순서와 같다.
INIT_SQL = [
    "DB/schema/schema_PostgreSQL.sql",
    "DB/schema/schema_pgvector.sql",
    "DB/seeds/03_seed_data.sql",
    "DB/migrations/04_users_password_hash.sql",
    "DB/migrations/05_credentials_raw_token.sql",
    "DB/migrations/06_holder_keys.sql",
]


def _connect(dbname: str):
    return psycopg.connect(
        host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"], dbname=dbname, autocommit=True,
    )


def _apply_sql(path: Path) -> None:
    # psql 전용 메타 명령(\restrict 등)은 서버가 이해하지 못하므로 뺀다.
    # 시드 덤프가 search_path를 비우므로 파일마다 새 연결을 쓴다.
    sql = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("\\"))
    with _connect(TEST_DB_NAME) as conn:
        conn.execute(sql)


@pytest.fixture(scope="session", autouse=True)
def test_database():
    with _connect("postgres") as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    for rel in INIT_SQL:
        _apply_sql(BE_DIR / rel)
    yield
    with _connect("postgres") as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')


@pytest.fixture
def db():
    with psycopg.connect(
        host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"], dbname=TEST_DB_NAME, autocommit=True,
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
            raw={"valid": True, "provider": "fake"},
        )
        fields.update(self.badge_overrides.get(vc_jwt, {}))
        return ProviderBadgeResult(**fields)

    async def verify_presentation(self, vp_token: str):
        from app.domain.credential import PresentationResult

        if self.error:
            raise self.error
        # 테스트 편의: vpToken 값을 홀더 DID로 취급한다.
        return PresentationResult(verified=True, holder_did=vp_token, claims={"name": "가상학생"},
                                  raw={"verified": True, "holderDid": vp_token})

    async def issue_badge(self, template_id: str, holder_did: str):
        from app.domain.credential import IssuedBadge

        if self.error:
            raise self.error
        self.issued.append((template_id, holder_did))
        return IssuedBadge(vc_jwt=make_vc_jwt(holder_did), credential_id=str(uuid.uuid4()), raw={})


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
def auth_required(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()
    yield
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
