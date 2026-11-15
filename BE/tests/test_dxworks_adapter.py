"""DXWorks 어댑터: 요청·응답 해석과 오류 매핑 (httpx MockTransport, 네트워크 없음).

외부 API의 요청·응답 형식을 다루는 테스트는 이 파일뿐이다. 값은 모두 가상의 값이다.
협력사 응답이 우리를 해치지 못하게 하는 한계(본문 크기·압축·전체 제한 시간·숫자·판정 형식·동시 호출 수)와
오류 문구에 되돌아온 요청 값 가리기도 여기서 본다.
"""
import asyncio
import gzip
import json
import logging
import time
import tracemalloc
import uuid
import zlib

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
    # 협력사 응답 원문·발급 ID는 어댑터 밖으로 내보내지 않는다 (쓰는 곳이 없다).
    assert not hasattr(issued, "raw") and not hasattr(issued, "credential_id")


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
    # 이제 2xx만 결과로 읽는다. 오류 문구·upstream_status는 4xx와 같다. 3xx는 서명 원본 하나에 대한 답이 아니므로(옮겨진 주소·
    # 차단 페이지) 검증기 장애로 본다: 직무 비교·포트폴리오 생성이 '검증된 자격증명 없음'으로 저장되지 않고 502가 된다.
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
        assert is_verifier_outage(exc.value)
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


# ── 협력사 응답 한계: 압축·본문 크기·전체 제한 시간·숫자 ──────────────────

def all_calls(dx):
    return (lambda: dx.verify_badge("a.b.c"), lambda: dx.verify_presentation("vp"), lambda: dx.issue_badge("tpl", DID))


class UnreadBody(httpx.AsyncByteStream):
    """읽으면 실패하는 응답 본문 (어댑터가 본문을 읽지 않고 거절하는지 확인용)."""

    async def __aiter__(self):
        raise AssertionError("response body must not be read")
        yield b""  # pragma: no cover


async def test_requests_ask_for_an_uncompressed_response():
    # httpx 기본값(gzip·deflate 등)을 보내면 작은 압축 본문이 수백 MiB로 풀릴 수 있다. 압축을 요청하지 않는다.
    seen = []

    def handler(request):
        seen.append(request.headers.get("Accept-Encoding"))
        if request.url.path.endswith("/credentials"):
            return httpx.Response(201, json={"data": {"jwt": "a.b.c"}})
        return httpx.Response(200, json={**badge_response(), "verified": True, "holderDid": DID})

    for call in all_calls(adapter(handler)):
        await call()
    assert seen == ["identity"] * 3


@pytest.mark.parametrize("encoding", ["br", "zstd", "compress", "gzip, identity", "gzip, gzip", "deflate, gzip",
                                      "utf-8", "none"])
@pytest.mark.parametrize("status", [200, 500])
async def test_unsupported_content_coding_is_refused_without_reading_it(status, encoding):
    # gzip·x-gzip·deflate 하나만 직접 푼다. 그 밖의 방식, 겹친 압축, 모르는 이름은 본문을 읽지 않고 거절한다.
    from app.services.reverify import is_verifier_outage

    def handler(request):
        return httpx.Response(status, headers={"Content-Encoding": encoding}, stream=UnreadBody())

    for call in all_calls(adapter(handler)):
        with pytest.raises(ProviderError, match="압축") as exc:
            await call()
        assert exc.value.upstream_status == status and not exc.value.unavailable
        assert is_verifier_outage(exc.value) is (status >= 500)


async def pieces(data: bytes, size: int = 64):
    """본문을 작은 조각으로 흘려보낸다 (실제 연결처럼 httpx가 본문을 미리 읽어 풀어 두지 않는다)."""
    for start in range(0, len(data), size):
        yield data[start:start + size]


def raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)  # zlib 머리말이 없는 deflate
    return compressor.compress(data) + compressor.flush()


@pytest.mark.parametrize("encoding,compress", [
    ("gzip", gzip.compress), ("GZIP", gzip.compress), (" x-gzip ", gzip.compress), ("deflate", zlib.compress),
    ("identity", bytes), ("", bytes),
], ids=["gzip", "GZIP", "x-gzip", "deflate", "identity", "none"])
async def test_compressed_partner_answer_is_decoded_within_the_cap(encoding, compress):
    # 회귀: 압축을 요청하지 않아도 gzip·deflate로 답하는 협력사·CDN 뒤에서는 모든 검증·발급이 실패했다 (본문을 읽지 않고 거절).
    # 이제 상한 안에서 직접 푼다: 정직한 판정·발급 응답은 그대로 읽힌다.
    seen = []

    def handler(request):
        seen.append(request.headers["Accept-Encoding"])
        if request.url.path.endswith("/credentials"):
            body = {"data": {"id": "c1", "jwt": "a.b.c"}}
        else:
            body = {**badge_response(), "verified": True, "holderDid": DID, "claims": {}}
        return httpx.Response(201, headers={"Content-Encoding": encoding},
                              content=pieces(compress(json.dumps(body).encode()), 7))

    dx = adapter(handler)
    badge = await dx.verify_badge("a.b.c")
    assert badge.valid and badge.recipient_did == DID and badge.credential_id == CREDENTIAL_ID
    assert (await dx.verify_presentation("vp")).holder_did == DID
    assert (await dx.issue_badge("tpl", DID)).vc_jwt == "a.b.c"
    assert seen == ["identity"] * 3  # 압축은 여전히 요청하지 않는다


@pytest.mark.parametrize("status", [200, 503])
@pytest.mark.parametrize("encoding,wbits", [("gzip", zlib.MAX_WBITS | 16), ("deflate", zlib.MAX_WBITS)])
async def test_compression_bomb_is_refused_at_the_cap_with_small_memory(status, encoding, wbits):
    # 260 KB쯤 되는 압축 본문이 256 MiB로 풀린다. 상한(1 MiB)까지만 풀고 끊어야 한다: 통째로 풀면 메모리가 수백 MiB 늘어난다.
    from app.integrations.dxworks import MAX_RESPONSE_BYTES

    compressor = zlib.compressobj(6, zlib.DEFLATED, wbits)
    block = b"A" * (1024 * 1024)
    bomb = compressor.compress(b'{"valid": false, "pad": "')
    for _ in range(256):
        bomb += compressor.compress(block)
    bomb += compressor.compress(b'"}') + compressor.flush()
    assert len(bomb) < MAX_RESPONSE_BYTES // 2  # 전송되는 크기는 상한보다 한참 작다
    sent = []

    async def body():
        async for piece in pieces(bomb, 16 * 1024):
            sent.append(len(piece))
            yield piece

    dx = adapter(lambda r: httpx.Response(status, headers={"Content-Encoding": encoding}, content=body()))
    tracemalloc.start()
    try:
        with pytest.raises(ProviderError, match="너무 큽니다") as exc:
            await dx.verify_badge("a.b.c")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert exc.value.upstream_status == status and exc.value.ambiguous is (status == 200)
    assert peak < 8 * 1024 * 1024  # 풀어낸 본문(상한 1 MiB)과 조각 몇 개뿐이다
    assert sum(sent) < len(bomb)  # 상한을 넘긴 조각에서 멈췄다 (끝까지 받지 않았다)


async def test_compressed_answer_is_capped_on_the_wire_too(monkeypatch):
    # 풀어낸 크기뿐 아니라 전송된 크기도 상한까지다. 압축하지 않고 담기만 한 gzip(compresslevel=0)은 전송 크기가 더 크다.
    import app.integrations.dxworks as dx

    verdict = json.dumps({"valid": False, "pad": "x" * 4000}).encode()
    packed = gzip.compress(verdict, compresslevel=0)
    assert len(packed) > len(verdict)
    monkeypatch.setattr(dx, "MAX_RESPONSE_BYTES", len(verdict))  # 풀어낸 크기는 상한 안이고 전송 크기만 넘는다
    with pytest.raises(ProviderError, match="너무 큽니다"):
        await adapter(lambda r: httpx.Response(200, headers={"Content-Encoding": "gzip"},
                                               content=pieces(packed))).verify_badge("a.b.c")
    monkeypatch.setattr(dx, "MAX_RESPONSE_BYTES", len(packed))  # 전송·해제 크기가 모두 상한 안이면 읽는다
    result = await adapter(lambda r: httpx.Response(200, headers={"Content-Encoding": "gzip"},
                                                    content=pieces(packed))).verify_badge("a.b.c")
    assert result.valid is False


BROKEN_COMPRESSION = {
    "garbage": ("gzip", b"not gzip at all"),
    "truncated": ("gzip", gzip.compress(json.dumps(badge_response()).encode())[:-9]),
    "empty": ("gzip", b""),
    # zlib 머리말 없는 deflate는 받지 않는다 (gzip·zlib 머리말만 가린다)
    "raw-deflate-without-zlib-header": ("deflate", raw_deflate(json.dumps(badge_response()).encode())),
}


@pytest.mark.parametrize("encoding,body", list(BROKEN_COMPRESSION.values()), ids=list(BROKEN_COMPRESSION))
@pytest.mark.parametrize("status", [200, 500])
async def test_undecodable_compressed_answer_is_a_non_json_answer(status, encoding, body):
    # 풀 수 없거나 중간에 끊긴 압축 본문은 JSON이 아닌 응답과 같게 처리한다 (판정으로 읽지 않는다. 500이 아니다).
    from app.services.reverify import is_verifier_outage

    def handler(request):
        return httpx.Response(status, headers={"Content-Encoding": encoding}, content=pieces(body, 5))

    for call in all_calls(adapter(handler)):
        with pytest.raises(ProviderError) as exc:
            await call()
        assert exc.value.upstream_status == status and not exc.value.unavailable
        assert is_verifier_outage(exc.value) is (status >= 500)
        if status == 200:
            assert exc.value.message == "DXWorks가 JSON이 아닌 응답을 반환했습니다" and exc.value.ambiguous


def test_capped_body_reads_plain_and_compressed_chunks_within_the_limit():
    # 순수 로직 (동기·비동기 호출자가 함께 쓴다): 전송된 바이트와 풀어낸 바이트를 각각 limit까지만 받는다.
    from app.integrations.capped_body import BodyTooLarge, CappedBody, UnsupportedEncoding

    data = b"x" * 1000
    for encoding, wire in (("", data), ("identity", data), ("gzip", gzip.compress(data)),
                           ("X-GZIP", gzip.compress(data)), ("deflate", zlib.compress(data))):
        body = CappedBody(encoding, 1000)  # 정확히 상한 크기는 받는다
        for start in range(0, len(wire), 3):
            body.feed(wire[start:start + 3])
        assert body.finish() == data
        body = CappedBody(encoding, 999)
        with pytest.raises(BodyTooLarge):
            body.feed(wire)
    for encoding in ("br", "zstd", "gzip, gzip", "gzip,deflate", "utf-8"):
        with pytest.raises(UnsupportedEncoding):
            CappedBody(encoding, 1000)
    with pytest.raises(BodyTooLarge):  # 전송된 크기 상한 (압축 여부와 무관)
        CappedBody("gzip", 10).feed(gzip.compress(b""))
    with pytest.raises(zlib.error):
        CappedBody("gzip", 1000).feed(b"plain text")
    truncated = CappedBody("deflate", 1000)
    truncated.feed(zlib.compress(data)[:-4])
    with pytest.raises(zlib.error):
        truncated.finish()


@pytest.mark.parametrize("status", [200, 201, 404, 503])
async def test_response_with_a_declared_length_over_the_cap_is_refused_without_reading_it(status):
    from app.integrations.dxworks import MAX_RESPONSE_BYTES

    assert MAX_RESPONSE_BYTES == 1024 * 1024

    def handler(request):
        return httpx.Response(status, headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)}, stream=UnreadBody())

    for call in all_calls(adapter(handler)):
        with pytest.raises(ProviderError, match="너무 큽니다") as exc:
            await call()
        assert exc.value.upstream_status == status


@pytest.mark.parametrize("status", [200, 500])
async def test_streamed_response_is_cut_at_the_cap(monkeypatch, status):
    # Content-Length 없이 흘려보내는 본문도 상한까지만 읽고 끊는다 (끝없이 보내도 메모리가 자라지 않는다).
    import app.integrations.dxworks as dx

    monkeypatch.setattr(dx, "MAX_RESPONSE_BYTES", 4096)
    sent = []

    async def long_body():
        # 상한의 10배에서 끝난다: 상한이 빠지면 끝없이 돌며 메모리를 쓰는 대신 아래 단언에서 실패한다.
        for _ in range(10 * 4096 // 1024):
            sent.append(1024)
            yield b"A" * 1024

    with pytest.raises(ProviderError, match="너무 큽니다") as exc:
        await adapter(lambda r: httpx.Response(status, content=long_body())).verify_badge("a.b.c")
    assert exc.value.upstream_status == status
    assert sum(sent) <= 4096 + 1024  # 상한을 넘긴 조각까지만 받았다


async def test_response_exactly_at_the_cap_is_still_read(monkeypatch):
    import app.integrations.dxworks as dx

    body = json.dumps(badge_response()).encode()
    monkeypatch.setattr(dx, "MAX_RESPONSE_BYTES", len(body))
    result = await adapter(lambda r: httpx.Response(201, content=body)).verify_badge("a.b.c")
    assert result.valid is True
    monkeypatch.setattr(dx, "MAX_RESPONSE_BYTES", len(body) - 1)
    with pytest.raises(ProviderError, match="너무 큽니다"):
        await adapter(lambda r: httpx.Response(201, content=body)).verify_badge("a.b.c")


async def test_body_already_read_by_the_transport_is_capped_whatever_length_it_declares(monkeypatch):
    # 전송 계층이 본문을 이미 다 읽어 둔 응답도 상한을 넘으면 거절한다 (Content-Length를 작게 적어 와도 실제 크기로 본다).
    import app.integrations.dxworks as dx

    monkeypatch.setattr(dx, "MAX_RESPONSE_BYTES", 4096)
    body = json.dumps({**badge_response(), "pad": "x" * 5000}).encode()
    with pytest.raises(ProviderError, match="너무 큽니다") as exc:
        await adapter(lambda r: httpx.Response(201, content=body, headers={"Content-Length": "10"})).verify_badge("a.b.c")
    assert exc.value.upstream_status == 201 and exc.value.ambiguous


async def test_slow_drip_response_hits_the_total_deadline_and_is_not_retried():
    # httpx의 timeout은 읽기 한 번마다 따로 잰다: 1바이트씩 흘려보내면 읽기는 매번 성공해 호출이 끝나지 않았다.
    # 이제 시도 한 번 전체가 timeout × 2를 넘으면 끊고, 서버가 이미 처리했을 수 있으므로 재시도하지 않는다.
    calls = []

    async def drip():
        while True:
            await asyncio.sleep(0.02)
            yield b" "

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=drip())

    dx = DXWorksAdapter("https://dx.invalid", "test-key", timeout=0.1, transport=httpx.MockTransport(handler))
    started = time.monotonic()
    with pytest.raises(ProviderError, match="전체 제한 시간 초과") as exc:
        await dx.issue_badge("tpl", DID)
    assert 0.15 <= time.monotonic() - started < 2
    assert len(calls) == 1 and exc.value.upstream_status is None
    assert exc.value.ambiguous  # 발급 요청은 이미 협력사에 닿았다


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
async def test_non_finite_numbers_in_a_response_are_a_provider_error(literal):
    # 회귀: 파이썬 json은 NaN·Infinity·1e400을 받아들이지만 JSON 응답으로 다시 내보낼 수 없어 신원 검증이 500이었다.
    from app.services.reverify import is_verifier_outage

    body = ('{"data": {"valid": true, "verified": true, "holderDid": "%s", "jwt": "a.b.c", "claims": {"n": %s}}}'
            % (DID, literal)).encode()
    dx = adapter(lambda r: httpx.Response(200, content=body, headers={"Content-Type": "application/json"}))
    for call in all_calls(dx):
        with pytest.raises(ProviderError, match="JSON이 아닌 응답") as exc:
            await call()
        assert exc.value.upstream_status == 200 and not is_verifier_outage(exc.value)


def test_non_finite_claims_are_502_not_500(client, register, identity_enabled, caplog):
    from app.integrations import get_verifier
    from main import app
    from tests.conftest import make_vp_jwt

    user = register()
    body = ('{"data": {"verified": true, "holderDid": "%s", "claims": {"n": NaN}}}' % user["did"]).encode()
    app.dependency_overrides[get_verifier] = lambda: adapter(
        lambda r: httpx.Response(200, content=body, headers={"Content-Type": "application/json"}))
    with caplog.at_level(logging.WARNING):
        res = client.post("/api/v1/verify", json={"type": "identity", "token": make_vp_jwt(user["did"])},
                          headers=user["headers"])
    assert res.status_code == 502 and res.json() == {"detail": "Credential service error", "code": "PROVIDER_ERROR"}
    assert not any(r.getMessage() == "Unhandled error" for r in caplog.records)


# ── 판정 형식: valid가 참·거짓이 아닌 2xx는 판정이 아니다 ─────────────────

@pytest.mark.parametrize("body", [{}, {"data": {}}, {"valid": None}, {"valid": "true"}, {"valid": 1}, {"valid": 0},
                                  {"valid": []}, {"message": "maintenance"}, {"verification": {"proof": "VALID"}}])
@pytest.mark.parametrize("status", [200, 201])
async def test_verdict_without_a_boolean_valid_is_a_format_error(status, body):
    # 예전에는 valid가 True가 아니면 모두 거절(False)로 읽었다: 점검 안내 같은 2xx 한 번에 재검증이 저장된 verified를
    # invalid로 바꿔 썼다. 이제 JSON이 아닌 2xx와 같은 형식 오류다 (그 자격증명만 '검증 불가', 저장하지 않음).
    from app.services.reverify import is_verifier_outage

    with pytest.raises(ProviderError) as exc:
        await adapter(lambda r: httpx.Response(status, json=body)).verify_badge("a.b.c")
    assert exc.value.message == "DXWorks 응답 형식이 올바르지 않습니다"
    assert exc.value.upstream_status == status and not exc.value.unavailable and not is_verifier_outage(exc.value)


async def test_explicit_false_verdict_is_still_a_verdict():
    result = await adapter(lambda r: httpx.Response(200, json={"valid": False})).verify_badge("a.b.c")
    assert result.valid is False and result.signature_ok is None and result.issuer_trusted is None


def test_degraded_partner_answer_does_not_flip_stored_credentials_to_invalid(client, register, db):
    # 실제 어댑터로 API까지: 협력사가 200 {}로 답하는 동안 근거 조회는 그 자격증명을 '검증 불가'로만 표시하고
    # DB의 verified를 바꾸지 않는다. 한 건 재검증도 같은 분류(검증 불가 / verifier_error)로 200으로 답한다 (장애가 아니다).
    from app.integrations import get_verifier
    from main import app
    from tests.conftest import make_vc_jwt

    user = register()
    credential_id = f"urn:uuid:{uuid.uuid4()}"
    state = {"degraded": False}

    def handler(request):
        if state["degraded"]:
            return httpx.Response(200, json={})
        body = badge_response()
        body["checks"]["recipient"]["subjectId"] = body["badge"]["subjectId"] = user["did"]
        body["checks"]["credentialId"]["id"] = credential_id
        return httpx.Response(201, json=body)

    app.dependency_overrides[get_verifier] = lambda: adapter(handler)
    saved = client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"],
                        credential_id=credential_id)}, headers=user["headers"])
    assert saved.status_code == 200 and saved.json()["success"] is True
    record_id = saved.json()["data"]["record_id"]

    state["degraded"] = True
    evidence = client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"])
    assert evidence.status_code == 200
    assert evidence.json()["excluded"] == [{"record_id": record_id, "status": "unverifiable",
                                           "reason": "verifier_error"}]
    reverified = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"])
    assert reverified.status_code == 200
    assert (reverified.json()["success"], reverified.json()["message"]) == (
        False, "Re-verification failed: verifier_error")
    assert reverified.json()["data"]["status"] == "unverifiable"
    row = db.execute("SELECT status FROM credentials WHERE record_id = %s", (record_id,)).fetchone()
    assert row["status"] == "verified"


# ── 결과를 모르는 실패 표시 (모의 발급이 한도 기록을 남길지 정한다) ───────────

@pytest.mark.parametrize("respond,ambiguous", [
    (httpx.ConnectError("down"), False),  # 요청이 닿지 않았다
    (httpx.ConnectTimeout("slow connect"), False),
    (httpx.LocalProtocolError("illegal header value"), False),  # 보내기 전에 실패
    (httpx.ReadTimeout("slow response"), True),  # 보낸 뒤 응답 대기 중
    (httpx.WriteTimeout("slow write"), True),
    (httpx.ReadError("reset"), True),
    (httpx.RemoteProtocolError("server disconnected"), True),
    (httpx.Response(500, json={"error": {"message": "boom"}}), False),  # 협력사가 거절·실패로 답했다
    (httpx.Response(429, json={"error": {"message": "slow down"}}), False),
    (httpx.Response(404), False),
    (httpx.Response(302, headers={"Location": "https://elsewhere.invalid/"}), False),
    (httpx.Response(201, json={"data": {"id": "c1"}}), True),  # 2xx인데 jwt가 없다: 발급은 됐을 수 있다
    (httpx.Response(201, json={"data": {"jwt": ["a"]}}), True),
    (httpx.Response(201, content=b"<html>ok</html>"), True),
    (httpx.Response(200, json=[]), True),
], ids=["connect-error", "connect-timeout", "local-protocol-error", "read-timeout", "write-timeout", "read-error",
        "remote-protocol-error", "500", "429", "404", "302", "2xx-without-jwt", "2xx-jwt-not-a-string", "2xx-html",
        "2xx-list"])
async def test_issue_failures_say_whether_the_outcome_is_unknown(no_retry_delay, respond, ambiguous):
    def handler(request):
        if isinstance(respond, Exception):
            raise respond
        return respond

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).issue_badge("tpl", DID)
    assert exc.value.ambiguous is ambiguous and not exc.value.unavailable


async def test_configuration_failures_are_not_unknown_outcomes():
    for dx in (adapter(lambda r: httpx.Response(500), host=""), adapter(lambda r: httpx.Response(500), api_key=""),
               adapter(lambda r: httpx.Response(500), host="http://dx.invalid")):
        with pytest.raises(ProviderError) as exc:
            await dx.issue_badge("tpl", DID)
        assert exc.value.unavailable and not exc.value.ambiguous
    assert ProviderError("x").ambiguous is False  # 기본값


async def test_non_ascii_api_key_is_a_configuration_error_and_sends_nothing():
    # 회귀: 헤더에 넣을 수 없는 키는 httpx가 UnicodeEncodeError를 내 500이었다. 설정 오류(503)로 답한다.
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(201, json={"data": {"jwt": "a.b.c"}})

    with pytest.raises(ProviderError) as exc:
        await adapter(handler, api_key="키-값").issue_badge("tpl", DID)
    assert exc.value.unavailable and "DXWORKS_API_KEY" in exc.value.message and "키-값" not in exc.value.message
    assert sent == []


# ── 오류 문구에 되돌아온 요청 값 가리기 ─────────────────────────────────

API_KEY = "partner-api-key-0123456789-ABCDEFGHIJ"
SECRET_TOKEN = "eyJhbGciOiJFZERTQSJ9." + "c2VjcmV0LXBheWxvYWQt" * 40 + ".c2lnbmF0dXJl"
HOLDER = "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"


def echoing(request):
    """요청의 Authorization 헤더와 본문 앞 200바이트를 오류 코드·문구에 그대로 되돌려 주는 협력사."""
    echo = f"bad request: auth={request.headers.get('Authorization')} body={request.content.decode()[:200]}"
    return httpx.Response(400, json={"error": {"code": f"E:{request.headers.get('Authorization')}", "message": echo}})


async def test_partner_error_text_does_not_carry_the_api_key_token_or_did():
    # 회귀: 협력사가 요청 값을 오류 문구에 되돌려 주면 회사 API 키와 사용자 VC-JWT 앞부분이 'Provider error' 로그에 남았다.
    dx = adapter(echoing, api_key=API_KEY)
    for call in (lambda: dx.verify_badge(SECRET_TOKEN), lambda: dx.verify_presentation(SECRET_TOKEN),
                 lambda: dx.issue_badge("tpl-demo", HOLDER)):
        with pytest.raises(ProviderError) as exc:
            await call()
        message = exc.value.message
        assert message.startswith("DXWorks 오류 (E:") and "[redacted]" in message and exc.value.upstream_status == 400
        # 전체 값은 물론, 협력사가 잘라서 되돌려 준 앞부분도 남지 않는다.
        for secret in (API_KEY, API_KEY[:16], SECRET_TOKEN[:40], "c2VjcmV0LXBheWxvYWQt", HOLDER, HOLDER[:24]):
            assert secret not in message
    assert "tpl-demo" in message  # 비밀이 아닌 값(템플릿 ID)은 그대로 둔다


async def test_redaction_keeps_ordinary_partner_text():
    def handler(request):
        return httpx.Response(404, json={"error": {"code": "TEMPLATE_NOT_FOUND",
                                                   "message": "template tpl-x not found in org 4711"}})

    with pytest.raises(ProviderError) as exc:
        await adapter(handler, api_key=API_KEY).issue_badge("tpl-x", HOLDER)
    assert exc.value.message == "DXWorks 오류 (TEMPLATE_NOT_FOUND): template tpl-x not found in org 4711"


async def test_partner_error_text_with_decoded_or_transformed_values_is_redacted_by_pattern():
    # 회귀: 보낸 값과 글자가 똑같은 것만 가렸다. 협력사가 토큰에서 꺼낸 DID, 페이로드 조각만, 대문자·URL 인코딩으로 바꾼 값을
    # 오류 문구에 넣으면 사용자의 DID와 토큰 본문이 'Provider error'·'Credential re-verification failed' 로그에 남았다.
    from tests.conftest import make_vc_jwt

    token = make_vc_jwt(HOLDER)  # 페이로드에 HOLDER DID가 들어 있다 (검증 요청에는 DID가 따로 실리지 않는다)
    payload_segment = token.split(".")[1]
    encoded_did = HOLDER.replace(":", "%3A")
    messages = {
        "decoded-did": f"subject {HOLDER} is not registered; payload {payload_segment[:120]}",
        "upper-case-did": f"subject {HOLDER.upper()} unknown",
        "url-encoded-did": f"holder {encoded_did} unknown",
        "did-without-prefix": f"key id {HOLDER[len('did:key:'):]} unknown",
        "other-did-method": "issuer did:web:issuer.example.org:users:alice is not trusted",
        "short-did-in-capitals": "issuer DID:Web:Issuer.Example.Org is not trusted",
        "payload-segment-only": f"cannot parse payload {payload_segment}",
        "header-and-payload": f"bad jwt {'.'.join(token.split('.')[:2])}",
        "long-base64url-run": "signature " + "A1b2C3d4" * 6 + " mismatch",
    }
    for name, text in messages.items():
        dx = adapter(lambda r, text=text: httpx.Response(400, json={"error": {"code": "INVALID", "message": text}}),
                     api_key=API_KEY)
        with pytest.raises(ProviderError) as exc:
            await dx.verify_badge(token)
        message = exc.value.message
        assert message.startswith("DXWorks 오류 (INVALID): ") and "[redacted]" in message, name
        for secret in (HOLDER, HOLDER.upper(), HOLDER[8:], HOLDER[8:40], payload_segment[:32], payload_segment[40:80],
                       "issuer.example.org", "Issuer.Example.Org", "A1b2C3d4A1b2"):
            assert secret not in message, (name, secret)
    # 오류 코드 자리에 들어와도 같다.
    dx = adapter(lambda r: httpx.Response(400, json={"error": {"code": HOLDER, "message": "m"}}), api_key=API_KEY)
    with pytest.raises(ProviderError) as exc:
        await dx.verify_badge(token)
    assert exc.value.message == "DXWorks 오류 ([redacted]): m"


@pytest.mark.parametrize("text", [
    "template tpl-x not found in org 4711",
    "token expired; unknown key id; error in request body",
    "INVALID_SIGNATURE: kid key-3 (alg EdDSA) at 2026-11-15T10:00:00Z",
    "candid:answer validation failed for field holderDid",  # 'did:'로 끝나는 낱말, 필드 이름
    "keyJar lookup failed",  # 낱말 안의 eyJ
    "request id 0123456789abcdef0123456789abcde rejected",  # 31자 (32자부터 가린다)
    # 32자 이상이어도 글자와 _·-로만 된 낱말 모양의 식별자(오류 코드·슬러그)는 운영자가 읽을 수 있게 그대로 둔다.
    "CREDENTIAL_STATUS_LIST_VERIFICATION_FAILED while checking the status list",
    "error_code=issuer_did_document_resolution_timeout_exceeded",
    "see /docs/errors/credential-status-list-verification-failed for details",
])
async def test_pattern_redaction_keeps_ordinary_partner_text(text):
    dx = adapter(lambda r: httpx.Response(400, json={"error": {"code": "E", "message": text}}), api_key=API_KEY)
    with pytest.raises(ProviderError) as exc:
        await dx.verify_badge(SECRET_TOKEN)
    assert exc.value.message == f"DXWorks 오류 (E): {text}"


@pytest.mark.parametrize("code", ["CREDENTIAL_STATUS_LIST_VERIFICATION_FAILED",
                                  "issuer_did_document_resolution_timeout_exceeded"])
async def test_long_error_codes_made_of_words_stay_readable_in_code_and_message(code):
    # 회귀: 32자 이상 이어진 base64url을 모두 가려, 협력사의 긴 오류 코드까지 'DXWorks 오류 ([redacted]): …'로 남았다
    # (운영자가 어떤 오류인지 알 수 없었다). 이제 숫자가 하나라도 든 조각만 가린다.
    assert len(code) >= 32
    dx = adapter(lambda r: httpx.Response(400, json={"error": {"code": code, "message": f"failed: {code}"}}),
                 api_key=API_KEY)
    with pytest.raises(ProviderError) as exc:
        await dx.verify_badge(SECRET_TOKEN)
    assert exc.value.message == f"DXWorks 오류 ({code}): failed: {code}"


@pytest.mark.parametrize("name,value", [
    ("base64url-43-chars", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"),       # 32바이트 키·서명처럼 숫자가 섞인 값
    ("uuid", "7f3c2a10-5b7e-4c1d-9a55-0f6f2f1f9d11"),
    ("trace-id-32-hex", "4bf92f3577b34da6a3ce929d0e0e4736"),
    ("sha256-hex", "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"),
    ("word-code-with-a-digit", "CREDENTIAL_STATUS_LIST_VERIFICATION_FAILED_2"),  # 숫자가 하나라도 있으면 가린다
    ("only-digit-zero", "a" * 31 + "0"),  # 그 숫자가 0뿐이어도 가린다
])
async def test_long_runs_with_a_digit_are_still_redacted_in_code_and_message(name, value):
    assert len(value) >= 32
    dx = adapter(lambda r: httpx.Response(400, json={"error": {"code": value, "message": f"ref {value} failed"}}),
                 api_key=API_KEY)
    with pytest.raises(ProviderError) as exc:
        await dx.verify_badge(SECRET_TOKEN)
    assert exc.value.message == "DXWorks 오류 ([redacted]): ref [redacted] failed", name


# 따로 띄운 프로세스에서 길이 argv[1]의 문구 세 가지를 가리고 {"checks": [결과 확인 셋], "seconds": 걸린 초}를 한 줄로 찍는다.
_REDACTION_TIMING_PROBE = """
import json, sys, time
from app.integrations.dxworks import DXWorksAdapter
size, api_key = int(sys.argv[1]), sys.argv[2]
dx = DXWorksAdapter("https://dx.invalid", api_key)
letters = "a" * size                                        # 숫자 없는 한 덩어리
mixed = "abcdefghijklmnopqrstuvwxyzABCDE7 " * (size // 33)  # 숫자가 든 32자 조각 수천 개
late_digit = "a" * (size - 1) + "7"                         # 맨 끝에만 숫자
started = time.perf_counter()
checks = [dx._redact(letters, {}) == letters, dx._redact(mixed, {}).count("[redacted]") == mixed.count("7"),
          dx._redact(late_digit, {}) == "[redacted]"]
print(json.dumps({"checks": checks, "seconds": time.perf_counter() - started}))
"""


def test_long_run_redaction_takes_time_linear_in_the_message_length():
    # 숫자 확인은 식의 미리보기가 아니라 맞은 조각을 한 번 더 훑는 함수로 한다: 긴 문구도 바로 끝난다.
    # 제곱으로 느는 구현(미리보기 등)은 256 KiB에 몇 분이 걸리고, 그동안 정규식 엔진이 GIL을 놓지 않아 스레드의 join 제한
    # 시간도 듣지 않는다. 그래서 따로 띄운 프로세스에서 재고, 제한 시간 안에 끝나지 않으면 그 프로세스를 끝내고 실패한다
    # (CI 작업이 제한 시간까지 멈춰 있지 않게).
    import os
    import subprocess
    import sys

    from tests.conftest import BE_DIR

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        result = subprocess.run([sys.executable, "-B", "-c", _REDACTION_TIMING_PROBE, str(256 * 1024), API_KEY],
                                cwd=BE_DIR, env=env, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail("256 KiB 문구를 가리는 데 10초 넘게 걸렸다 (입력 길이의 제곱으로 느는 구현)")
    assert result.returncode == 0, result.stderr[-2000:]
    measured = json.loads(result.stdout.strip().splitlines()[-1])
    assert measured["checks"] == [True, True, True]
    assert measured["seconds"] < 1.0  # 실제로는 몇 ms다 (제곱으로 늘면 몇 분이 걸린다)


async def test_a_secret_echoed_twice_is_redacted_both_times():
    # 같은 값이 한 문구에 두 번 되돌아와도 모두 가린다 (모양 규칙에 걸리지 않는 짧은 키로 확인한다).
    from app.integrations.dxworks import _redact_echo

    key = "k/9+dev=key/0123+ab"  # 19자, base64url이 아닌 문자가 섞인 키
    assert _redact_echo(f"a {key} b {key} c {key[:16]}", key) == "a [redacted] b [redacted] c [redacted]"

    def handler(request):
        auth = request.headers["Authorization"]
        return httpx.Response(401, json={"error": {"code": "E", "message": f"bad {auth}; retry with {auth}"}})

    with pytest.raises(ProviderError) as exc:
        await adapter(handler, api_key=key).issue_badge("tpl", DID)
    assert exc.value.message == "DXWorks 오류 (E): bad Bearer [redacted]; retry with Bearer [redacted]"


def test_echoed_request_values_do_not_reach_the_logs(client, register, identity_enabled, caplog):
    # 실제 어댑터로 API까지: 배지 검증·신원 검증·모의 발급·근거 조회의 로그 어디에도 API 키·서명 원본·DID가 없다.
    from app.integrations import get_issuer, get_verifier
    from main import app
    from tests.conftest import make_vc_jwt, make_vp_jwt

    user = register()
    state = {"echo": False}

    def handler(request):
        if state["echo"]:
            return echoing(request)
        body = badge_response()
        body["checks"]["recipient"]["subjectId"] = body["badge"]["subjectId"] = user["did"]
        body["checks"]["credentialId"]["id"] = None
        return httpx.Response(201, json=body)

    real = adapter(handler, api_key=API_KEY)
    app.dependency_overrides[get_verifier] = lambda: real
    app.dependency_overrides[get_issuer] = lambda: real
    token = make_vc_jwt(user["did"])
    assert client.post("/api/v1/verify", json={"type": "badge", "token": token},
                       headers=user["headers"]).json()["success"] is True
    state["echo"] = True
    with caplog.at_level(logging.DEBUG):
        statuses = [
            client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"]).status_code,
            client.post("/api/v1/verify", json={"type": "identity", "token": make_vp_jwt(user["did"])},
                        headers=user["headers"]).status_code,
            client.post("/api/mock-issuer/issue", json={"holderDid": user["did"]}, headers=user["headers"]).status_code,
            client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"]).status_code,
        ]
    assert statuses == [502, 502, 502, 200]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text.count("Provider error: DXWorks 오류") == 3 and "Credential re-verification failed" in text
    for secret in (API_KEY, API_KEY[:16], token, token[:40], user["did"]):
        assert secret not in text


def test_decoded_did_and_payload_segment_do_not_reach_the_logs(client, register, caplog):
    # 실제 어댑터로 API까지: 협력사가 토큰에서 꺼낸 DID와 페이로드 조각을 오류 문구에 넣어도 배지 검증·한 건 재검증·근거 조회의
    # 로그('Provider error', 'Credential re-verification failed') 어디에도 사용자의 DID와 토큰 본문이 남지 않는다.
    from app.domain.credential import decode_jwt_payload
    from app.integrations import get_verifier
    from main import app
    from tests.conftest import make_vc_jwt

    user = register()
    state = {"echo": False}

    def handler(request):
        token = json.loads(request.content)["vcJwt"]
        vc = decode_jwt_payload(token)["vc"]
        subject = vc["credentialSubject"]["id"]
        if state["echo"]:
            text = f"subject {subject} is not registered; payload {token.split('.')[1][:120]}"
            return httpx.Response(400, json={"error": {"code": "SUBJECT_UNKNOWN", "message": text}})
        body = badge_response()
        body["checks"]["recipient"]["subjectId"] = body["badge"]["subjectId"] = subject
        body["checks"]["credentialId"]["id"] = vc["id"]
        return httpx.Response(201, json=body)

    app.dependency_overrides[get_verifier] = lambda: adapter(handler, api_key=API_KEY)
    token = make_vc_jwt(user["did"])
    saved = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"])
    assert saved.json()["success"] is True
    state["echo"] = True
    with caplog.at_level(logging.DEBUG):
        statuses = [
            client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"]).status_code,
            client.post(f"/api/v1/credentials/{saved.json()['data']['record_id']}/reverify",
                        headers=user["headers"]).status_code,
            client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"]).status_code,
        ]
    assert statuses == [502, 200, 200]  # 자격증명 하나에 대한 거절(400): 배지 검증은 502, 재검증·근거 조회는 '검증 불가'
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text.count("DXWorks 오류 (SUBJECT_UNKNOWN): subject [redacted] is not registered; payload [redacted]") == 3
    payload_segment = token.split(".")[1]
    for secret in (user["did"], user["did"][8:], payload_segment[:32], payload_segment[60:100]):
        assert secret not in text


# ── 동시 호출 상한 ─────────────────────────────────────────────────────

async def test_concurrent_partner_requests_are_capped():
    # 요청이 아무리 몰려도 협력사에 동시에 보내는 요청은 MAX_IN_FLIGHT개까지다 (검증·VP 검증·발급 공통, 어댑터 객체와 무관).
    from app.integrations.dxworks import MAX_IN_FLIGHT

    assert MAX_IN_FLIGHT == 16
    state = {"now": 0, "peak": 0, "total": 0}

    async def handler(request):
        state["now"] += 1
        state["total"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        if request.url.path.endswith("/credentials"):
            return httpx.Response(201, json={"data": {"jwt": "a.b.c"}})
        return httpx.Response(200, json={**badge_response(), "verified": True, "holderDid": DID})

    calls = []
    for _ in range(20):  # 요청마다 새 어댑터 (get_verifier와 같다)
        calls += [call() for call in all_calls(adapter(handler))]
    await asyncio.gather(*calls)
    assert state["total"] == 60 and state["peak"] == MAX_IN_FLIGHT


async def test_waiting_for_a_free_slot_is_bounded_and_sends_nothing(monkeypatch):
    # 자리가 나지 않으면(협력사가 느림) timeout × 2까지만 기다리고, 아무것도 보내지 않은 채 오류로 끝낸다.
    import app.integrations.dxworks as dx

    monkeypatch.setattr(dx, "MAX_IN_FLIGHT", 1)
    seen = []

    async def slow(request):
        seen.append(request.url.path)
        if len(seen) == 1:
            await asyncio.sleep(1.5)  # 첫 호출만 오래 걸린다
        return httpx.Response(201, json=badge_response())

    patient = DXWorksAdapter("https://dx.invalid", "test-key", timeout=5, transport=httpx.MockTransport(slow))
    hurried = DXWorksAdapter("https://dx.invalid", "test-key", timeout=0.05, transport=httpx.MockTransport(slow))
    first = asyncio.ensure_future(patient.verify_badge("a.b.c"))
    await asyncio.sleep(0.01)  # 첫 호출이 자리를 잡는다
    started = time.monotonic()
    with pytest.raises(ProviderError, match="대기 시간 초과") as exc:
        await hurried.issue_badge("tpl", DID)
    assert time.monotonic() - started < 1.2 and len(seen) == 1
    assert not exc.value.ambiguous and exc.value.upstream_status is None  # 보낸 것이 없다. 장애로 분류된다
    assert (await first).valid is True
    assert (await patient.verify_badge("a.b.c")).valid is True  # 자리는 돌려받았다


def test_in_flight_cap_works_in_every_event_loop(monkeypatch):
    # 세마포어를 모듈 전역 하나로 두면 처음 기다린 이벤트 루프에 묶여 다른 루프(테스트·재시작)에서 RuntimeError가 난다.
    import app.integrations.dxworks as dx

    monkeypatch.setattr(dx, "MAX_IN_FLIGHT", 1)

    async def handler(request):
        await asyncio.sleep(0.01)
        return httpx.Response(201, json=badge_response())

    async def burst():
        results = await asyncio.gather(*(adapter(handler).verify_badge("a.b.c") for _ in range(4)))
        return [r.valid for r in results]

    for _ in range(3):  # 루프를 새로 만들 때마다 (기다리는 호출이 생기도록 한 번에 4개)
        assert asyncio.run(burst()) == [True] * 4


def test_closed_event_loops_are_dropped_from_the_slot_map(monkeypatch):
    # 회귀: 기다린 호출이 있던 세마포어는 자기 루프를 붙잡고 있어, 닫힌 루프의 항목이 (약한 참조인데도) 끝없이 쌓였다.
    # 이제 새 루프의 세마포어를 만들 때 닫힌 루프의 항목을 지운다.
    import app.integrations.dxworks as dx

    monkeypatch.setattr(dx, "MAX_IN_FLIGHT", 1)

    async def handler(request):
        await asyncio.sleep(0.01)
        return httpx.Response(201, json=badge_response())

    async def burst():
        await asyncio.gather(*(adapter(handler).verify_badge("a.b.c") for _ in range(3)))  # 두 호출이 자리를 기다린다

    for _ in range(5):
        asyncio.run(burst())
    closed = [loop for loop in dx._in_flight if loop.is_closed()]
    assert len(closed) <= 1  # 마지막 루프의 항목만, 다음 루프가 세마포어를 만들 때까지 남는다


async def test_both_connect_attempts_share_one_exchange_deadline(monkeypatch):
    # 회귀: 시도마다 timeout × 2를 따로 쟀다. 느리게 실패하는 연결이 두 번이면 교환만 timeout × 4를 넘겼다.
    # 이제 두 시도가 상한 하나(timeout × 2)를 함께 쓴다 (호출 한 번의 최악은 자리 대기 timeout × 2 + 교환 timeout × 2).
    import app.integrations.dxworks as dx

    monkeypatch.setattr(dx, "_RETRY_DELAY_SECONDS", 0.01)
    timeout = 0.8
    calls = []

    async def slow_connect_failure(request):
        calls.append(1)
        await asyncio.sleep(timeout * 1.5)
        raise httpx.ConnectTimeout("handshake")

    slow = DXWorksAdapter("https://dx.invalid", "test-key", timeout=timeout,
                          transport=httpx.MockTransport(slow_connect_failure))
    started = time.monotonic()
    with pytest.raises(ProviderError, match="ConnectTimeout") as exc:
        await slow.issue_badge("tpl", DID)
    # 남은 시간에 시도 한 번이 온전히 들어가지 않으므로 다시 하지 않는다 (예전: 두 번 시도해 timeout × 3).
    # 아무것도 보내지 않았으니 결과를 아는 실패다.
    assert len(calls) == 1 and time.monotonic() - started < timeout * 2.5 and not exc.value.ambiguous

    calls.clear()

    async def fail_then_stall(request):
        calls.append(1)
        if len(calls) == 1:
            await asyncio.sleep(timeout * 0.75)
            raise httpx.ConnectError("refused")
        await asyncio.sleep(60)

    stalled = DXWorksAdapter("https://dx.invalid", "test-key", timeout=timeout,
                             transport=httpx.MockTransport(fail_then_stall))
    started = time.monotonic()
    with pytest.raises(ProviderError, match="전체 제한 시간 초과") as exc:
        await stalled.issue_badge("tpl", DID)
    elapsed = time.monotonic() - started
    # 빨리 실패한 연결은 한 번 다시 하되, 두 번째 시도는 남은 시간만 쓴다: 처음부터 timeout × 2에 끝난다
    # (예전: 두 번째 시도가 timeout × 2를 새로 받아 timeout × 2.75).
    assert len(calls) == 2 and timeout * 2 - 0.05 <= elapsed < timeout * 2.5 and exc.value.ambiguous


@pytest.mark.parametrize("failure", [
    OSError("socket failure"), OverflowError("bind(): port must be 0-65535."), ValueError("bad url part"),
    ExceptionGroup("unhandled errors in a TaskGroup", [OverflowError("port must be 0-65535.")]),
    httpx.InvalidURL("Invalid port"),
], ids=["os-error", "overflow-error", "value-error", "exception-group", "invalid-url"])
async def test_non_httpx_failure_before_sending_is_a_provider_error(no_retry_delay, failure):
    # 회귀: 범위 밖의 포트처럼 연결을 시작하는 단계에서 httpx 예외가 아닌 오류가 나면 어댑터 밖으로 그대로 나가 500이었다.
    from app.services.reverify import is_verifier_outage

    calls = []

    def handler(request):
        calls.append(1)
        raise failure

    for call in all_calls(adapter(handler)):
        with pytest.raises(ProviderError, match="연결 실패") as exc:
            await call()
        assert type(failure).__name__ in exc.value.message
        assert not exc.value.ambiguous and not exc.value.unavailable and exc.value.upstream_status is None
        assert is_verifier_outage(exc.value)
    assert len(calls) == 3  # 다시 시도하지 않는다


def test_non_httpx_failure_is_502_not_500(client, register, caplog):
    from app.integrations import get_verifier
    from main import app
    from tests.conftest import make_vc_jwt

    def handler(request):
        raise ExceptionGroup("unhandled errors in a TaskGroup", [OverflowError("port must be 0-65535.")])

    user = register()
    app.dependency_overrides[get_verifier] = lambda: adapter(handler)
    with caplog.at_level(logging.WARNING):
        res = client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"])},
                          headers=user["headers"])
    assert res.status_code == 502 and res.json() == {"detail": "Credential service error", "code": "PROVIDER_ERROR"}
    assert not any(r.getMessage() == "Unhandled error" for r in caplog.records)


async def test_cancellation_is_not_turned_into_a_provider_error():
    # 묶음 예외 가운데 취소처럼 Exception이 아닌 것은 삼키지 않는다 (ExceptionGroup만 연결 실패로 바꾼다).
    def handler(request):
        raise BaseExceptionGroup("cancelled", [asyncio.CancelledError()])

    with pytest.raises(BaseExceptionGroup):
        await adapter(handler).verify_badge("a.b.c")


@pytest.mark.parametrize("host,ok", [
    ("https://dx.invalid", True), ("https://dx.invalid/gateway", True), ("HTTPS://DX.INVALID:8443", True),
    ("http://127.0.0.1:8999", True), ("http://localhost:8999", True), ("http://[::1]:8999", True),
    # 포트는 없거나 1~65535
    ("https://dx.invalid:1", True), ("https://dx.invalid:65535", True), ("https://dx.invalid:443/gateway", True),
    ("https://dx.invalid:0", False), ("https://dx.invalid:65536", False), ("https://dx.invalid:99999", False),
    ("http://127.0.0.1:99999", False), ("http://127.0.0.1:-1", False), ("http://[::1]:70000", False),
    ("https://dx.invalid:99999999999999999999", False),
    ("http://dx.invalid", False), ("dx.invalid", False), ("https://", False), ("ftp://dx.invalid", False),
    ("https://user:pw@dx.invalid", False), ("https://user@dx.invalid", False),
    ("https://dx.invalid?x=1", False), ("https://dx.invalid/?", False), ("https://dx.invalid#frag", False),
    ("https://dx .invalid", False), ("https://dx.invalid/a b", False), ("https://dx.invalid\t", False),
    ("https://dx.invalid\r\nX-Injected: 1", False),
])
def test_host_setting_rule(host, ok):
    from app.integrations.dxworks import is_valid_host_setting

    assert is_valid_host_setting(host) is ok


# ── 호출 양 제한: 실제 어댑터에서 협력사로 나가는 요청 수 ─────────────────
# 한도 계산 자체는 tests/test_verifier_budget.py에서 본다. 여기서는 API → 실제 어댑터 → 전송 계층(MockTransport)까지 이어
# 붙여, 한도를 넘긴 요청과 모양이 틀린 토큰이 협력사로 한 건도 나가지 않는지를 나가는 요청 수로 확인한다.

class Partner:
    """실제 DXWorksAdapter가 보내는 HTTP 요청을 모두 세는 가짜 협력사."""

    def __init__(self):
        self.requests: list[tuple[str, bool]] = []  # (경로 끝, Authorization 헤더가 붙었는지)
        self.in_flight = self.peak = 0
        self.delay = 0.0
        self.error_status: int | None = None

    def sent(self, suffix: str = "") -> int:
        return len([path for path, _ in self.requests if path.endswith(suffix)])

    async def handler(self, request: httpx.Request) -> httpx.Response:
        from app.domain.credential import decode_jwt_payload

        self.requests.append((request.url.path, "Authorization" in request.headers))
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error_status is not None:
                return httpx.Response(self.error_status, json={"error": {"code": "E", "message": "refused"}})
            data = json.loads(request.content)
            if "vpToken" in data:
                holder = (decode_jwt_payload(data["vpToken"]) or {}).get("iss")
                return httpx.Response(200, json={"data": {"verified": True, "holderDid": holder, "claims": {}}})
            vc = (decode_jwt_payload(data["vcJwt"]) or {}).get("vc") or {}
            body = badge_response()
            body["checks"]["recipient"]["subjectId"] = body["badge"]["subjectId"] = vc["credentialSubject"]["id"]
            body["checks"]["credentialId"]["id"] = vc["id"]
            return httpx.Response(201, json=body)
        finally:
            self.in_flight -= 1


@pytest.fixture
def partner(client):
    from app.integrations import get_issuer, get_verifier
    from main import app

    fake = Partner()
    real = adapter(fake.handler)
    app.dependency_overrides[get_verifier] = lambda: real
    app.dependency_overrides[get_issuer] = lambda: real
    return fake


@pytest.fixture
def frozen_budget(monkeypatch):
    """운영과 같은 한도(호출자마다 분당 120번, 프로세스 분당 600번)에 손으로 움직이는 시계만 끼운다."""
    from app.services import verifier_budget as module

    clock = {"now": 1000.0}
    monkeypatch.setattr(module, "verifier_budget", module.VerifierBudget(clock=lambda: clock["now"]))
    return clock


def post_verify(client, user, token: str, kind: str = "badge"):
    return client.post("/api/v1/verify", json={"type": kind, "token": token}, headers=user["headers"])


def test_verify_replays_beyond_the_budget_never_reach_the_partner(client, register, partner, frozen_budget,
                                                                  identity_enabled):
    # 회귀: 계정 하나로 같은 토큰을 200번 보내면 협력사 요청도 200번 나갔다 (한도·중복 제거 없음).
    from tests.conftest import make_vc_jwt, make_vp_jwt

    user = register()
    token, vp = make_vc_jwt(user["did"]), make_vp_jwt(user["did"])
    statuses = [post_verify(client, user, token).status_code for _ in range(300)]
    assert statuses == [200] * 120 + [429] * 180
    assert partner.sent() == partner.sent("/verifier/ob3/verify") == 120
    # 한도를 넘긴 뒤에는 무엇을 보내도 협력사로 나가는 요청이 0건이다: 다른 토큰, 신원 검증(회사 API 키가 붙는 호출) 모두.
    for _ in range(50):
        assert post_verify(client, user, make_vc_jwt(user["did"])).status_code == 429
        assert post_verify(client, user, vp, "identity").status_code == 429
    last = post_verify(client, user, token)
    assert last.json() == {"detail": "Too many verification requests. Try again later."}
    assert int(last.headers["retry-after"]) >= 1
    assert partner.sent() == 120 and not any(with_key for _, with_key in partner.requests)

    frozen_budget["now"] += 10  # 초당 2번씩 다시 찬다
    statuses = [post_verify(client, user, vp, "identity").status_code for _ in range(30)]
    assert statuses == [200] * 20 + [429] * 10
    assert partner.sent("/holder-presentations/verify") == 20 and partner.sent() == 140


def test_malformed_tokens_never_reach_the_partner(client, register, partner, identity_enabled):
    # 회귀: 길이·NUL만 확인하고 어떤 문자열이든(주입 시도 문자열, alg=none, 1 MiB짜리 이모지) 그대로 협력사로 보냈다.
    # 신원 검증은 그 값에 회사 API 키까지 붙였다.
    from app.services.verifier_budget import verifier_budget
    from tests.test_credential_security import NOT_A_JWS

    user = register()
    for token in NOT_A_JWS.values():
        for _ in range(3):
            badge = post_verify(client, user, token)
            assert badge.status_code == 200 and badge.json()["message"] == "Badge verification failed."
            identity = post_verify(client, user, token, "identity")
            assert identity.status_code == 422 and identity.json() == {"detail": "Invalid token format"}
    for token in ("a\x00b", "   ", "a" * (256 * 1024 + 1)):  # 지금처럼 422
        assert post_verify(client, user, token).status_code == 422
    assert partner.requests == []
    assert not verifier_budget._buckets  # 협력사를 부르지 않은 요청은 호출 몫도 쓰지 않는다


def test_fan_out_beyond_the_budget_never_reaches_the_partner(client, register, partner, frozen_budget):
    # 회귀: 자격증명 N개인 사용자의 근거 조회·포트폴리오 생성은 요청마다 N번 불렀고, 20개를 동시에 보내면 20 × N번이었다.
    from app.services import verifier_budget as module
    from main import app
    from tests.conftest import make_vc_jwt

    user = register()
    record_ids = []
    for _ in range(40):
        saved = post_verify(client, user, make_vc_jwt(user["did"]))
        assert saved.status_code == 200 and saved.json()["success"] is True
        record_ids.append(saved.json()["data"]["record_id"])
    module.verifier_budget.clear()  # 여기부터 센다 (가득 찬 몫 120)
    partner.requests.clear()

    evidence_url = f"/api/v1/subjects/{user['did']}/evidence"
    for expected in (40, 80, 120):
        res = client.get(evidence_url, headers=user["headers"])
        assert res.status_code == 200 and len(res.json()["evidence"]) == 40 and partner.sent() == expected

    async def flood():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
            calls = [ac.get(evidence_url, headers=user["headers"]) for _ in range(20)]
            calls += [ac.get(evidence_url, headers={"X-Service-Key": "test-service-key"}) for _ in range(20)]
            calls += [ac.post("/api/v1/portfolios", headers=user["headers"],
                              json={"title": "t", "record_ids": record_ids, "source": "VC_ONLY"}) for _ in range(20)]
            calls += [ac.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"])
                      for record_id in record_ids]
            return await asyncio.gather(*calls)

    responses = asyncio.run(flood())
    by_status: dict[int, int] = {}
    for res in responses:
        by_status[res.status_code] = by_status.get(res.status_code, 0) + 1
    # 서비스 키는 자기 몫(120)으로 3번(40 × 3)까지만 통과한다. 사용자 몫은 바닥이라 사용자 요청 80개는 모두 429다.
    assert by_status == {200: 3, 429: 97}
    assert partner.sent() == 120 + 120  # 사용자 120 + 서비스 키 120. 그 밖에는 한 건도 나가지 않았다
    assert partner.peak <= 16


def test_parallel_requests_never_exceed_the_in_flight_cap(client, register, partner, frozen_budget):
    # 회귀: /verify 50개를 동시에 보내면 협력사 요청도 50개가 동시에 나갔다.
    from main import app
    from tests.conftest import make_vc_jwt

    users = [register() for _ in range(5)]  # 한 계정이 동시에 진행할 수 있는 배지 검증은 10개까지라 다섯 계정으로 나눈다
    partner.delay = 0.2  # 50개가 모두 겹치도록 충분히 길게

    async def burst():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
            return await asyncio.gather(*(
                ac.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"])},
                        headers=user["headers"]) for user in users for _ in range(10)))

    assert [res.status_code for res in asyncio.run(burst())] == [200] * 50
    assert partner.sent() == 50 and partner.peak == 16


def test_parallel_junk_tokens_from_one_account_reach_the_partner_at_most_ten_times(client, register, partner,
                                                                                   frozen_budget):
    # 회귀: 모양만 맞는 쓰레기 토큰 100개를 한 계정이 한꺼번에 보내면 100개 모두 협력사로 나갔다 (호출 몫 120 안).
    # 이제 통과하지 못한 배지 검증은 계정마다 10분에 10번까지이고, 부르기 전에 자리를 잡으므로 동시에 보내도 넘지 못한다.
    from main import app
    from tests.test_domain import make_raw_jwt

    user = register()
    partner.error_status = 400  # 협력사가 토큰 하나하나를 거절한다
    partner.delay = 0.05

    async def burst():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
            return await asyncio.gather(*(
                ac.post("/api/v1/verify", json={"type": "badge", "token": make_raw_jwt('{"n": %d}' % n)},
                        headers=user["headers"]) for n in range(100)))

    responses = asyncio.run(burst())
    by_status: dict[int, int] = {}
    for res in responses:
        by_status[res.status_code] = by_status.get(res.status_code, 0) + 1
    assert by_status == {502: 10, 429: 90} and partner.sent() == 10
    refused = next(res for res in responses if res.status_code == 429)
    assert refused.json() == {"detail": "Too many verification requests. Try again later."}
    assert 1 <= int(refused.headers["retry-after"]) <= 600
    # 그 뒤로는 무엇을 보내도 10분 동안 이 계정의 배지 검증은 협력사로 나가지 않는다.
    for _ in range(20):
        assert post_verify(client, user, make_raw_jwt("{}")).status_code == 429
    assert partner.sent() == 10


@pytest.mark.parametrize("status", [500, 429, 403])
def test_fan_out_stops_calling_a_failing_partner(client, register, partner, frozen_budget, caplog, status):
    # 회귀: 협력사가 이미 실패하거나 우리를 제한(429)·차단(403)하는 중에도 자격증명 수만큼 끝까지 불렀다.
    from app.services.reverify import MAX_CONCURRENCY
    from tests.conftest import make_vc_jwt

    user = register()
    record_ids = []
    for _ in range(30):
        record_ids.append(post_verify(client, user, make_vc_jwt(user["did"])).json()["data"]["record_id"])
    partner.requests.clear()
    partner.error_status = status
    with caplog.at_level(logging.WARNING, logger="app.services.reverify"):
        evidence = client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"])
    # 처음 실패가 돌아오기 전에 이미 시작한 호출(요청 하나의 동시 호출 수)까지만 나간다.
    assert 1 <= partner.sent() <= MAX_CONCURRENCY
    assert evidence.status_code == 200 and evidence.json()["evidence"] == []
    assert {e["reason"] for e in evidence.json()["excluded"]} == {"verifier_error"} and len(
        evidence.json()["excluded"]) == 30
    # 실제로 부른 자격증명만 실패 로그를 남긴다.
    failures = [r.getMessage() for r in caplog.records if r.name == "app.services.reverify"]
    assert len(failures) == partner.sent() and all("Credential re-verification failed: record=" in m for m in failures)

    partner.requests.clear()
    created = client.post("/api/v1/portfolios", headers=user["headers"],
                          json={"title": "t", "record_ids": record_ids, "source": "VC_ONLY"})
    assert created.status_code == 502 and 1 <= partner.sent() <= MAX_CONCURRENCY


@pytest.mark.parametrize("host", ["https://user:pw@dx.invalid", "https://user@dx.invalid", "https://dx.invalid?x=1",
                                  "https://dx.invalid/#frag", "https://dx .invalid", "https://dx.invalid:99999",
                                  "http://127.0.0.1:65536", "https://dx.invalid:0"])
async def test_host_with_credentials_query_or_fragment_is_refused_before_sending(host):
    # 회귀: user:pw@ 주소는 Basic 인증 헤더와 함께 나갔고, ?쿼리가 붙은 주소는 경로가 쿼리 뒤에 붙어 다른 주소를 불렀다.
    # 범위 밖의 포트는 연결 단계에서 httpx 예외가 아닌 오류를 내 500이 됐다.
    # 서버 시작 때(core.config) 거르는 값이지만, 어댑터를 직접 만들어도 같은 규칙으로 아무것도 보내지 않는다.
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(201, json={"data": {"id": "c1", "jwt": "a.b.c"}})

    for call in all_calls(adapter(handler, host=host)):
        with pytest.raises(ProviderError) as exc:
            await call()
        assert exc.value.unavailable and "DXWORKS_API_HOST" in exc.value.message
        assert "pw" not in exc.value.message and "dx.invalid" not in exc.value.message
    assert sent == []


@pytest.mark.parametrize("timeout", [0, -5.0, float("inf"), float("nan")])
async def test_unusable_timeout_is_a_configuration_error_and_sends_nothing(timeout):
    # 회귀: inf면 멈춘 응답을 끝없이 기다렸고, nan이면 ValueError가 어댑터 밖으로 나가 500이었다.
    # 서버 시작 때(core.config) 1~120초만 받지만, 어댑터를 직접 만들어도 시간 제한 없는 호출은 하지 않는다.
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(201, json=badge_response())

    dx = DXWorksAdapter("https://dx.invalid", "test-key", timeout=timeout, transport=httpx.MockTransport(handler))
    for call in all_calls(dx):
        with pytest.raises(ProviderError) as exc:
            await call()
        assert exc.value.unavailable and "DXWORKS_TIMEOUT_SECONDS" in exc.value.message
    assert sent == []
