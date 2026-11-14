"""DXWorks 어댑터: 요청·응답 해석과 오류 매핑 (httpx MockTransport, 네트워크 없음).

외부 API의 요청·응답 형식을 다루는 테스트는 이 파일뿐이다. 값은 모두 가상의 값이다.
"""
import json
import logging
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


@pytest.mark.parametrize("holder", [{}, {"holderDid": None}, {"holderDid": ""}, {"holderDid": "   "},
                                    {"holderDid": 0}, {"holderDid": False}, {"holderDid": [DID]},
                                    {"holderDid": {"id": DID}}])
async def test_verify_presentation_without_usable_holder_did_has_no_holder(holder):
    # 홀더 DID가 없거나 비었거나 문자열이 아니면 None으로 둔다 (API가 본인 VP가 아닌 것으로 보고 403으로 거절한다).
    body = {"data": {"verified": True, "claims": {"name": "가상학생"}, **holder}}
    result = await adapter(lambda r: httpx.Response(200, json=body)).verify_presentation("vp")
    assert result.verified is True
    assert result.holder_did is None


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


@pytest.mark.parametrize("body", [[], "ok", 1])
async def test_non_object_json_success_keeps_upstream_status(body):
    # 응답을 받았으면 상태 코드를 남긴다. upstream_status None은 "응답 없음"(연결 실패 등)에만 쓰인다.
    from app.services.reverify import is_verifier_outage

    with pytest.raises(ProviderError) as exc:
        await adapter(lambda r: httpx.Response(200, json=body)).verify_badge("a.b.c")
    assert exc.value.upstream_status == 200 and not exc.value.unavailable
    assert not is_verifier_outage(exc.value)  # 형식이 틀린 2xx는 그 자격증명 하나만 제외 (HTML·null 응답과 같게)


async def test_missing_api_key_is_unavailable():
    with pytest.raises(ProviderError) as exc:
        await adapter(lambda r: httpx.Response(500), api_key="").issue_badge("tpl", DID)
    assert exc.value.unavailable


async def test_missing_host_is_unavailable():
    with pytest.raises(ProviderError) as exc:
        await adapter(lambda r: httpx.Response(500), host="").verify_badge("a.b.c")
    assert exc.value.unavailable and "DXWORKS_API_HOST" in exc.value.message


# ── 전송 보안: https만, 주변 프록시·인증서 환경변수 무시 ─────────────────

@pytest.mark.parametrize("host", ["http://dx.invalid", "http://10.0.0.5:8080", "ftp://dx.invalid", "dx.invalid",
                                  "https://", "http://[::1", "http://localhost.evil.invalid", "https://xn--a.com"])
async def test_non_https_host_is_refused_before_sending(host):
    # 회사 API 키와 사용자 VC-JWT를 평문(또는 알 수 없는 스킴)으로 보내지 않는다. 설정 누락과 같은 503.
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(201, json={"data": {"id": "c1", "jwt": "a.b.c"}})

    for call in (lambda a: a.issue_badge("tpl", DID), lambda a: a.verify_badge("a.b.c"),
                 lambda a: a.verify_presentation("vp")):
        with pytest.raises(ProviderError) as exc:
            await call(adapter(handler, host=host))
        # 메시지(운영 로그)에는 고칠 설정 이름만 넣고 호스트 값은 넣지 않는다.
        assert exc.value.unavailable and exc.value.message == "DXWORKS_API_HOST는 https:// 주소여야 합니다"
    assert sent == []


@pytest.mark.parametrize("host", ["http://127.0.0.1:8999", "http://localhost:8999", "http://[::1]:8999",
                                  "HTTPS://DX.INVALID"])
async def test_loopback_http_and_https_hosts_are_allowed(host):
    # 로컬 테스트 서버(같은 기기·태스크)는 http로 부를 수 있다. 스킴 대소문자는 가리지 않는다.
    seen = []

    def handler(request):
        seen.append(request.url.scheme)
        return httpx.Response(201, json=badge_response())

    assert (await adapter(handler, host=host).verify_badge("a.b.c")).valid
    assert len(seen) == 1


async def test_https_host_with_path_prefix_keeps_prefix():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(201, json=badge_response())

    await adapter(handler, host="https://dx.invalid/gateway/").verify_badge("a.b.c")
    assert seen == ["https://dx.invalid/gateway/api/v1/verifier/ob3/verify"]


async def test_ambient_proxy_settings_and_redirects_are_not_used(monkeypatch):
    # HTTPS_PROXY 같은 주변 환경변수가 있어도 DXWorks 호출은 그 프록시를 타지 않는다 (ai_service와 같은 설정).
    # (transport를 넣으면 httpx가 환경 프록시를 읽지 않으므로 만들어진 클라이언트의 설정으로 확인한다.)
    created = []
    real_client = httpx.AsyncClient

    class RecordingClient(real_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setattr(httpx, "AsyncClient", RecordingClient)
    await adapter(lambda r: httpx.Response(201, json=badge_response())).verify_badge("a.b.c")
    await adapter(lambda r: httpx.Response(201, json={"data": {"id": "c1", "jwt": "a.b.c"}})).issue_badge("tpl", DID)
    assert len(created) == 2 and all(c.trust_env is False and c.follow_redirects is False for c in created)


async def test_redirect_response_is_not_followed():
    # 리다이렉트를 따라가면 요청 본문(VC-JWT)이 다른 주소로 갈 수 있다. 3xx는 오류로 끝낸다.
    sent = []

    def handler(request):
        sent.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://elsewhere.invalid/collect"})

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).verify_badge("a.b.c")
    assert sent == ["https://dx.invalid/api/v1/verifier/ob3/verify"]
    assert exc.value.upstream_status == 307


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirect_with_json_body_is_an_error_not_a_result(status):
    # 회귀: 예전에는 400 미만이면 본문을 결과로 읽어, JSON 본문의 3xx가 검증 결과(판정 모양이면 저장)·신원 확인·발급 성공이 됐다.
    # 이제 2xx만 결과로 읽는다. 오류 문구·upstream_status는 4xx와 같고, 검증기 장애(전체 502)로 보지는 않는다.
    from app.services.reverify import is_verifier_outage

    sent = []

    def handler(request):
        sent.append(request.url.path)
        if request.url.path.endswith("/credentials"):
            body = {"data": {"id": "c1", "jwt": "a.b.c"}}
        else:
            body = {**badge_response(), "verified": True, "holderDid": DID, "claims": {"name": "가상학생"}}
        return httpx.Response(status, json=body, headers={"Location": "https://elsewhere.invalid/collect"})

    dx = adapter(handler)
    for call in (lambda: dx.verify_badge("a.b.c"), lambda: dx.verify_presentation("vp"),
                 lambda: dx.issue_badge("tpl", DID)):
        with pytest.raises(ProviderError) as exc:
            await call()
        assert exc.value.upstream_status == status and not exc.value.unavailable
        assert exc.value.message.startswith(f"DXWorks 오류 ({status}): ")
        assert not is_verifier_outage(exc.value)
    assert len(sent) == 3  # 리다이렉트를 따라가지 않는다 (호출마다 원래 주소로 한 번)


async def test_redirect_error_envelope_is_read_like_other_errors():
    def handler(request):
        return httpx.Response(308, json={"error": {"code": "MOVED", "message": "use /api/v2"}})

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).issue_badge("tpl", DID)
    assert exc.value.message == "DXWorks 오류 (MOVED): use /api/v2" and exc.value.upstream_status == 308


@pytest.mark.parametrize("status", [200, 201, 400, 503])
async def test_deeply_nested_json_body_is_a_provider_error_not_recursion_error(status):
    # 회귀: 너무 깊게 중첩된 본문은 json 해석이 RecursionError를 내 500으로 샜다. 이제 JSON이 아닌 응답과 같게 처리한다
    # (2xx는 그 자격증명 하나만 제외, 4xx는 4xx 오류, 5xx는 검증기 장애).
    from app.services.reverify import is_verifier_outage

    deep = ("[" * 5000 + "]" * 5000).encode()
    dx = adapter(lambda r: httpx.Response(status, content=deep, headers={"Content-Type": "application/json"}))
    for call in (lambda: dx.verify_badge("a.b.c"), lambda: dx.verify_presentation("vp"),
                 lambda: dx.issue_badge("tpl", DID)):
        with pytest.raises(ProviderError) as exc:
            await call()
        assert exc.value.upstream_status == status and not exc.value.unavailable
        assert is_verifier_outage(exc.value) is (status >= 500)


def test_redirect_with_json_body_is_502_and_changes_nothing(client, register, db):
    # 실제 어댑터로 API까지: 검증·발급 API가 JSON 본문의 3xx로 답하면 502 고정 응답이다. 재검증이 저장된 verified를
    # invalid로 바꾸지 않고(예전: 200 'Re-verification failed: invalid'로 저장), 모의 발급도 한도에 세지 않는다.
    from app.integrations import get_issuer, get_verifier
    from main import app
    from tests.conftest import make_vc_jwt

    user = register()
    credential_id = f"urn:uuid:{uuid.uuid4()}"
    state = {"status": 201}

    def handler(request):
        if request.url.path.endswith("/credentials"):
            return httpx.Response(state["status"], json={"data": {"id": "c1", "jwt": "a.b.c"}})
        if state["status"] != 201:
            return httpx.Response(state["status"], json={"message": "Moved Permanently"},
                                  headers={"Location": "https://elsewhere.invalid/api/v2"})
        body = badge_response()
        body["checks"]["recipient"]["subjectId"] = body["badge"]["subjectId"] = user["did"]
        body["checks"]["credentialId"]["id"] = credential_id
        return httpx.Response(201, json=body)

    real = adapter(handler)
    app.dependency_overrides[get_verifier] = lambda: real
    app.dependency_overrides[get_issuer] = lambda: real
    token = make_vc_jwt(user["did"], credential_id=credential_id)
    saved = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"])
    assert saved.status_code == 200 and saved.json()["success"] is True
    record_id = saved.json()["data"]["record_id"]

    state["status"] = 301
    reverified = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"])
    verified = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"])
    state["status"] = 308
    issued = client.post("/api/mock-issuer/issue", json={"holderDid": user["did"]}, headers=user["headers"])
    for res in (reverified, verified, issued):
        assert res.status_code == 502
        assert res.json() == {"detail": "Credential service error", "code": "PROVIDER_ERROR"}
    row = db.execute("SELECT status FROM credentials WHERE record_id = %s", (record_id,)).fetchone()
    assert row["status"] == "verified"
    assert db.execute("SELECT count(*) AS n FROM mock_issuances WHERE user_id = %s", (user["id"],)).fetchone()["n"] == 0


def test_verify_with_plain_http_host_is_503_and_sends_nothing(client, register, db):
    # 실제 어댑터로 API까지: 평문 http 호스트면 아무것도 보내지 않고 설정 오류(503 고정 응답)로 답하며, 저장하지 않는다.
    from app.integrations import get_verifier
    from main import app
    from tests.conftest import make_vc_jwt

    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(201, json=badge_response())

    user = register()
    app.dependency_overrides[get_verifier] = lambda: adapter(handler, host="http://dx.invalid")
    res = client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"])},
                      headers=user["headers"])
    assert res.status_code == 503 and sent == []
    assert res.json() == {"detail": "Credential service is not configured", "code": "PROVIDER_NOT_CONFIGURED"}
    assert db.execute("SELECT count(*) AS n FROM credentials WHERE user_id = %s", (user["id"],)).fetchone()["n"] == 0


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


# ── 오류 문구: 협력사 문구는 운영 로그 한 줄로만 ─────────────────────────

def test_provider_error_message_is_single_line_and_capped():
    exc = ProviderError("a\r\nb\x1b[2K\u2028c\u200ed\ud800" + "x" * 1000, upstream_status=502, unavailable=False)
    # 제어(Cc)·서식(Cf)·서로게이트(Cs) 문자와 줄 구분자는 공백으로 바꾸고 300자로 자른다.
    assert exc.message == "a  b [2K c d " + "x" * 287 and len(exc.message) == 300
    assert str(exc) == exc.message and exc.upstream_status == 502
    # 보통 문구(한글·괄호·콜론)는 그대로 둔다.
    assert ProviderError("DXWorks 오류 (500): 서버 점검 중").message == "DXWorks 오류 (500): 서버 점검 중"


async def test_partner_error_text_is_single_line_and_capped():
    # 협력사가 오류 문구에 줄바꿈·제어 문자를 넣어도 운영 로그 한 줄을 넘거나 위조하지 못한다.
    message = "bad\r\nWARNING:vc-be:Provider error: forged\x1b[2K\u2028" + "A" * 5000

    def handler(request):
        return httpx.Response(400, json={"error": {"code": "BAD\nCODE", "message": message}})

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).verify_badge("a.b.c")
    assert exc.value.message.startswith("DXWorks 오류 (BAD CODE): bad  WARNING:vc-be:Provider error: forged")
    assert not any(ch in exc.value.message for ch in "\r\n\x1b\u2028") and len(exc.value.message) <= 300
    assert exc.value.upstream_status == 400


def test_partner_error_text_stays_in_one_log_line_not_in_the_response(client, register, caplog):
    # 실제 어댑터로 API까지: 협력사 오류는 502 고정 응답이고, 협력사 코드·문구는 운영 로그 한 줄("Provider error: ...")에만
    # 남는다 (모의 발급은 기본 템플릿으로 협력사까지 간다).
    from app.integrations import get_issuer, get_verifier
    from main import app
    from tests.conftest import make_vc_jwt

    def handler(request):
        if request.url.path.endswith("/credentials"):
            return httpx.Response(404, json={"error": {"code": "TEMPLATE_NOT_FOUND",
                                                       "message": "template tpl-x not found in org 4711"}})
        return httpx.Response(401, json={"error": {"code": "INVALID_SIGNATURE", "message": "kid key-3\r\nforged"}})

    user = register()
    real = adapter(handler)
    app.dependency_overrides[get_verifier] = lambda: real
    app.dependency_overrides[get_issuer] = lambda: real
    with caplog.at_level(logging.WARNING):
        issued = client.post("/api/mock-issuer/issue", json={"holderDid": user["did"]}, headers=user["headers"])
        verified = client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"])},
                               headers=user["headers"])
    for res in (issued, verified):
        assert res.status_code == 502
        assert res.json() == {"detail": "Credential service error", "code": "PROVIDER_ERROR"}
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Provider error:")]
    assert len(lines) == 2
    assert "DXWorks 오류 (TEMPLATE_NOT_FOUND): template tpl-x not found in org 4711" in lines[0]
    assert "DXWorks 오류 (INVALID_SIGNATURE): kid key-3  forged" in lines[1]
    assert not any("\n" in line or "\r" in line for line in lines)
