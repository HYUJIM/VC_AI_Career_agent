"""DXWorks 어댑터: 요청·응답 해석과 오류 매핑 (httpx MockTransport, 네트워크 없음).

외부 API의 요청·응답 형식을 다루는 테스트는 이 파일뿐이다. 값은 모두 가상의 값이다.
"""
import json
import uuid

import httpx
import pytest

from app.integrations.dxworks import DXWorksAdapter
from app.integrations.ports import ProviderError

DID = "did:key:z6MkTestHolder"
CREDENTIAL_ID = f"urn:uuid:{uuid.UUID(int=1)}"


def adapter(handler, api_key="test-key", host="https://dx.invalid"):
    return DXWorksAdapter(host, api_key, transport=httpx.MockTransport(handler))


def badge_response(status: str = "VALID", *, with_summary: bool = True, trusted: bool = True) -> dict:
    valid = status == "VALID" and trusted
    body = {
        "valid": valid,
        "badge": {"name": "테스트 수료 배지", "issuerName": "예시대학교", "subjectId": DID,
                  "issuedAt": "2026-10-02T03:34:21.000Z"},
        "checks": {
            "recipient": {"subjectId": DID, "status": "displayed"},
            "issuer": {"issuerName": "예시대학교", "issuerId": "https://issuer.example.org/demo", "status": "ok"},
            "issuerTrust": {"status": "ok", "trusted": trusted},
            "credentialId": {"id": CREDENTIAL_ID, "status": "ok", "credentialStatus": status},
        },
    }
    if with_summary:
        body["verification"] = {"proof": "VALID", "status": status, "trust": "TRUSTED" if trusted else "UNTRUSTED",
                                "policyDecision": "ACCEPT" if valid else "REJECT"}
    return body


async def test_verify_badge_valid():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=badge_response())

    result = await adapter(handler).verify_badge("a.b.c")
    assert seen["body"] == {"vcJwt": "a.b.c"}
    assert seen["auth"] is None  # 검증 API에는 API 키를 보내지 않는다
    assert result.valid and result.signature_ok and result.issuer_trusted
    assert result.credential_status == "VALID"
    assert result.recipient_did == DID
    assert result.credential_id == CREDENTIAL_ID
    assert result.issuer_name == "예시대학교"
    assert result.achievement_name == "테스트 수료 배지"


async def test_verify_badge_revoked():
    result = await adapter(lambda r: httpx.Response(201, json=badge_response("REVOKED"))).verify_badge("a.b.c")
    assert result.valid is False
    assert result.signature_ok is True  # 서명은 정상, 상태만 폐기
    assert result.credential_status == "REVOKED"


async def test_verify_badge_without_summary_uses_item_checks():
    body = badge_response(with_summary=False, trusted=False)
    result = await adapter(lambda r: httpx.Response(201, json=body)).verify_badge("a.b.c")
    assert result.signature_ok is True
    assert result.issuer_trusted is False
    assert result.credential_status == "VALID"


async def test_summary_takes_precedence_over_item_checks():
    body = badge_response()
    body["verification"].update(proof="INVALID", trust="UNTRUSTED")
    result = await adapter(lambda r: httpx.Response(201, json=body)).verify_badge("a.b.c")
    assert result.signature_ok is False
    assert result.issuer_trusted is False


async def test_verify_presentation_unwraps_data_envelope():
    # 회귀: 예전 코드는 data 봉투를 풀지 않아 신원 검증이 항상 실패로 처리됐다.
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": {"verified": True, "holderDid": DID, "claims": {"name": "가상학생"}}})

    result = await adapter(handler).verify_presentation("vp")
    assert result.verified is True
    assert result.holder_did == DID
    assert result.claims == {"name": "가상학생"}


async def test_issue_badge_returns_jwt():
    def handler(request):
        assert json.loads(request.content) == {"templateId": "tpl", "holderDid": DID}
        return httpx.Response(201, json={"data": {"id": "c1", "jwt": "a.b.c"}})

    issued = await adapter(handler).issue_badge("tpl", DID)
    assert issued.vc_jwt == "a.b.c"
    assert issued.credential_id == "c1"


async def test_issue_badge_without_jwt_raises():
    issued_without_jwt = lambda request: httpx.Response(201, json={"data": {"id": "c1"}})  # noqa: E731
    with pytest.raises(ProviderError, match="jwt"):
        await adapter(issued_without_jwt).issue_badge("tpl", DID)


async def test_error_envelope_becomes_provider_error():
    def handler(request):
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "template missing"}})

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).issue_badge("tpl", DID)
    assert exc.value.upstream_status == 404
    assert "NOT_FOUND" in exc.value.message and "template missing" in exc.value.message


async def test_missing_api_key_is_unavailable():
    with pytest.raises(ProviderError) as exc:
        await adapter(lambda r: httpx.Response(500), api_key="").issue_badge("tpl", DID)
    assert exc.value.unavailable


async def test_missing_host_is_unavailable():
    with pytest.raises(ProviderError) as exc:
        await adapter(lambda r: httpx.Response(500), host="").verify_badge("a.b.c")
    assert exc.value.unavailable and "DXWORKS_API_HOST" in exc.value.message


async def test_connection_error_becomes_provider_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(ProviderError, match="연결 실패"):
        await adapter(handler).verify_badge("a.b.c")


@pytest.fixture
def no_retry_delay(monkeypatch):
    import app.integrations.dxworks as dx

    monkeypatch.setattr(dx, "_RETRY_DELAY_SECONDS", 0)


async def test_connect_failure_is_retried_once(no_retry_delay):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("slow connect")
        return httpx.Response(201, json=badge_response())

    result = await adapter(handler).verify_badge("a.b.c")
    assert result.valid and len(calls) == 2


async def test_read_timeout_is_not_retried(no_retry_delay):
    # 응답 대기 중 시간 초과는 서버가 이미 처리했을 수 있어(발급 중복 위험) 재시도하지 않는다.
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow response")

    with pytest.raises(ProviderError, match="ReadTimeout"):
        await adapter(handler).issue_badge("tpl", DID)
    assert len(calls) == 1


async def test_persistent_connect_failure_gives_up(no_retry_delay):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError, match="ConnectError"):
        await adapter(handler).verify_badge("a.b.c")
    assert len(calls) == 2
