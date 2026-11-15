"""실행 시점 재검증 / 포트폴리오(VC_ONLY) / 선택 공개 (실제 PostgreSQL + 가짜 발급·검증기, 가상 데이터)."""
import uuid

import pytest

from app.integrations.ports import ProviderError
from tests.conftest import make_vc_jwt

SERVICE = {"X-Service-Key": "test-service-key"}


def store_badge(client, user, *, name="테스트 수료 배지"):
    """사용자에게 배지 하나를 검증·저장하고 (record_id, vc_jwt)를 돌려준다."""
    token = make_vc_jwt(user["did"], name=name)
    res = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"])
    assert res.status_code == 200 and res.json()["success"], res.text
    return res.json()["data"]["record_id"], token


def evidence(client, user, **params):
    res = client.get(f"/api/v1/subjects/{user['did']}/evidence", params=params, headers=user["headers"])
    assert res.status_code == 200, res.text
    return res.json()


# ── 실행 시점 재검증 ──────────────────────────────────────────────────

def test_reverify_reflects_revocation_and_updates_db(client, register, fake_dx, db):
    user = register()
    record_id, token = store_badge(client, user)
    fake_dx.set_badge_result(token, valid=False, credential_status="REVOKED")

    res = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"])
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False and body["data"]["status"] == "revoked" and body["data"]["verified_at"]
    assert db.execute("SELECT status FROM credentials WHERE record_id = %s", (record_id,)).fetchone()["status"] == "revoked"


def test_reverify_other_users_credential_is_404(client, register):
    alice, bob = register(), register()
    record_id, _ = store_badge(client, alice)
    assert client.post(f"/api/v1/credentials/{record_id}/reverify", headers=bob["headers"]).status_code == 404


def test_reverify_requires_token(client, register):
    user = register()
    record_id, _ = store_badge(client, user)
    assert client.post(f"/api/v1/credentials/{record_id}/reverify").status_code == 401


def test_reverify_provider_error_is_502(client, register, fake_dx):
    user = register()
    record_id, _ = store_badge(client, user)
    fake_dx.error = ProviderError("DXWorks 연결 실패: ConnectError")
    assert client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"]).status_code == 502


@pytest.mark.parametrize("error,status,body", [
    (ProviderError("DXWORKS_API_HOST가 설정되지 않았습니다", unavailable=True), 503,
     {"detail": "Credential service is not configured", "code": "PROVIDER_NOT_CONFIGURED"}),
    (ProviderError("DXWorks 연결 실패: ReadTimeout"), 502, None),
    (ProviderError("DXWorks 오류 (500): boom", upstream_status=500), 502, None),
    (ProviderError("DXWorks 오류 (429): slow down", upstream_status=429), 502, None),
    (ProviderError("DXWorks 오류 (401): bad credentials", upstream_status=401), 502, None),
    (ProviderError("DXWorks 오류 (302): Found", upstream_status=302), 502, None),
], ids=["not-configured", "no-response", "500", "429", "401", "302"])
def test_reverify_during_a_verifier_outage_stays_5xx(client, register, fake_dx, db, error, status, body):
    # 검증기 장애는 지금처럼 502/503이다 (FE의 모아서 재검증은 여기서 멈춘다). 저장된 상태는 바뀌지 않는다.
    user = register()
    record_id, _ = store_badge(client, user)
    fake_dx.error = error
    res = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"])
    assert res.status_code == status
    assert res.json() == (body or {"detail": "Credential service error", "code": "PROVIDER_ERROR"})
    row = db.execute("SELECT status FROM credentials WHERE record_id = %s", (record_id,)).fetchone()
    assert row["status"] == "verified"


@pytest.mark.parametrize("error", [
    ProviderError("DXWorks 오류 (400): invalid", upstream_status=400),
    ProviderError("DXWorks 오류 (404): not found", upstream_status=404),
    ProviderError("DXWorks 오류 (422): bad token", upstream_status=422),
    ProviderError("DXWorks 응답 형식이 올바르지 않습니다", upstream_status=200),
], ids=["400", "404", "422", "2xx-format-error"])
def test_reverify_of_a_credential_the_verifier_rejects_individually_is_a_result(client, register, fake_dx, db, caplog,
                                                                                 error):
    # 회귀: 검증기가 이 서명 원본 하나를 거절(400·404·422)하거나 형식이 틀린 2xx로 답하면 한 건 재검증만 502였다
    # (근거 조회·포트폴리오는 그 자격증명만 제외). FE의 모아서 재검증은 5xx에서 멈추므로 그런 자격증명 하나가 매번 나머지를
    # '실행 안 됨'으로 남겼다. 이제 같은 분류(unverifiable / verifier_error)의 정상 응답 본문으로 200이다.
    user = register()
    record_id, _ = store_badge(client, user)
    stored = db.execute("SELECT status, last_verified_at FROM credentials WHERE record_id = %s",
                        (record_id,)).fetchone()
    fake_dx.error = error
    res = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"])
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"success", "message", "data"}
    assert (body["success"], body["message"]) == (False, "Re-verification failed: verifier_error")
    data = body["data"]
    assert (data["record_id"], data["status"], data["verified"], data["verified_at"]) == (
        record_id, "unverifiable", False, None)
    assert data["checks"] == [{"name": "signature", "result": "skip", "detail": "검증기 호출 실패"}]
    assert data["achievement_name"] == "테스트 수료 배지" and data["issuer_name"] == "예시대학교"
    # 저장된 상태·검증 시각은 그대로다 ('검증 불가'는 저장하지 않는다). 실패는 운영 로그에 남는다.
    assert db.execute("SELECT status, last_verified_at FROM credentials WHERE record_id = %s",
                      (record_id,)).fetchone() == stored
    assert any("Credential re-verification failed: record=" in r.getMessage() for r in caplog.records)
    # 근거 조회와 같은 분류다.
    assert evidence(client, user)["excluded"] == [
        {"record_id": record_id, "status": "unverifiable", "reason": "verifier_error"}]
    fake_dx.error = None
    assert client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"]).json()["success"] is True


@pytest.mark.parametrize("error,outage", [
    (ProviderError("DXWORKS_API_HOST가 설정되지 않았습니다", unavailable=True), True),
    (ProviderError("DXWorks 연결 실패: ReadTimeout"), True),
    (ProviderError("DXWorks 오류 (500): boom", upstream_status=500), True),
    (ProviderError("DXWorks 오류 (503): busy", upstream_status=503), True),
    (ProviderError("DXWorks 오류 (408): timeout", upstream_status=408), True),
    (ProviderError("DXWorks 오류 (429): slow down", upstream_status=429), True),
    # 401·403·3xx는 서명 원본 하나에 대한 답이 아니다 (API 키·IP 차단, 차단 페이지, 옮겨진 주소).
    (ProviderError("DXWorks 오류 (401): bad credentials", upstream_status=401), True),
    (ProviderError("DXWorks 오류 (403): Forbidden", upstream_status=403), True),
    (ProviderError("DXWorks 오류 (301): Moved Permanently", upstream_status=301), True),
    (ProviderError("DXWorks 오류 (302): Found", upstream_status=302), True),
    (ProviderError("DXWorks 오류 (399): ?", upstream_status=399), True),
    (ProviderError("DXWorks 오류 (400): invalid", upstream_status=400), False),
    (ProviderError("DXWorks 오류 (404): not found", upstream_status=404), False),
    (ProviderError("DXWorks 오류 (422): bad token", upstream_status=422), False),
    (ProviderError("DXWorks 응답 형식이 올바르지 않습니다", upstream_status=200), False),
    (ProviderError("DXWorks가 JSON이 아닌 응답을 반환했습니다", upstream_status=201), False),
])
def test_verifier_outage_classification(error, outage):
    from app.services.reverify import is_verifier_outage

    assert is_verifier_outage(error) is outage


@pytest.fixture
def verifier_calls(fake_dx, monkeypatch):
    """가짜 검증기에 들어온 배지 검증 호출을 세고, failing에 넣은 서명 원본에는 그 오류를 던진다."""
    calls, failing = [], {}
    original = fake_dx.verify_badge

    async def verify(vc_jwt):
        calls.append(vc_jwt)
        if vc_jwt in failing:
            raise failing[vc_jwt]
        return await original(vc_jwt)

    monkeypatch.setattr(fake_dx, "verify_badge", verify)
    return calls, failing


@pytest.mark.parametrize("status", [None, 500, 503, 429, 401, 403, 302])
def test_fan_out_stops_calling_the_verifier_after_the_first_outage(client, register, fake_dx, verifier_calls, caplog,
                                                                    status):
    # 회귀: 검증기가 이미 장애·제한 중이어도 자격증명 수만큼 끝까지 불렀다 (협력사 부하를 N배로 키움).
    # 이제 장애가 한 번 확인되면 아직 시작하지 않은 자격증명은 부르지 않고 같은 오류로 '검증 불가'가 된다.
    calls, _ = verifier_calls
    user = register()
    record_ids = [store_badge(client, user)[0] for _ in range(8)]
    calls.clear()
    fake_dx.error = ProviderError("DXWorks 오류: outage", upstream_status=status)

    body = evidence(client, user)
    assert body["evidence"] == [] and len(body["excluded"]) == 8
    assert {(e["status"], e["reason"]) for e in body["excluded"]} == {("unverifiable", "verifier_error")}
    assert len(calls) == 1  # 가짜 검증기는 바로 실패하므로 첫 호출에서 장애가 확인된다
    # 실제로 부른 자격증명만 실패 로그를 남긴다.
    logs = [r.getMessage() for r in caplog.records if r.name == "app.services.reverify"]
    assert len(logs) == 1 and "Credential re-verification failed: record=" in logs[0]

    # 저장하는 API는 지금처럼 502로 끝나고 아무것도 만들지 않는다.
    calls.clear()
    assert create_portfolio(client, user, record_ids).status_code == 502 and len(calls) == 1
    assert client.get("/api/v1/portfolios", headers=user["headers"]).json()["data"] == []
    # 저장된 상태는 바뀌지 않는다 (검증 불가는 저장하지 않는다).
    fake_dx.error = None
    assert len(evidence(client, user)["evidence"]) == 8


@pytest.mark.parametrize("status", [400, 404, 422])
def test_per_credential_rejections_do_not_stop_the_fan_out(client, register, verifier_calls, status):
    # 특정 서명 원본에 대한 400·404·422 거절은 장애가 아니다: 나머지는 지금처럼 모두 다시 검증한다.
    calls, failing = verifier_calls
    user = register()
    stored = [store_badge(client, user) for _ in range(6)]
    for _, token in stored[:2]:
        failing[token] = ProviderError(f"DXWorks 오류 ({status}): rejected", upstream_status=status)
    calls.clear()
    body = evidence(client, user)
    assert len(calls) == 6 and len(body["evidence"]) == 4
    assert sorted(e["record_id"] for e in body["excluded"]) == sorted(record_id for record_id, _ in stored[:2])
    created = create_portfolio(client, user, [record_id for record_id, _ in stored])
    assert created.status_code == 201 and created.json()["data"]["credential_count"] == 4


def test_evidence_and_share_stay_labeled_on_verifier_outage_and_log(client, register, fake_dx, caplog):
    user = register()
    record_id, token = store_badge(client, user)
    share_token = create_share(client, user, [record_id]).json()["data"]["token"]
    fake_dx.error = ProviderError("DXWorks 연결 실패: ConnectError")

    # 읽기 전용 조회는 지금처럼 200으로 '검증 불가' 표시만 한다 (저장하지 않으므로). 대신 로그를 남긴다.
    body = evidence(client, user)
    assert body["evidence"] == []
    assert body["excluded"] == [{"record_id": record_id, "status": "unverifiable", "reason": "verifier_error"}]
    view = client.get(f"/api/v1/public/shares/{share_token}")
    assert view.status_code == 200 and view.json()["credentials"][0]["status"] == "unverifiable"

    logs = [r.getMessage() for r in caplog.records if r.name == "app.services.reverify"]
    assert len(logs) == 2 and all(record_id in message for message in logs)
    assert not any(token in message or user["did"] in message for message in logs)  # 서명 원본·DID는 남기지 않는다


def test_seed_credential_without_signed_original_is_unverifiable(client):
    login = client.post("/api/v1/users/login", json={"email": "user001@example.com", "password": "test1234"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    body = client.get(f"/api/v1/subjects/{login['data']['did']}/evidence", headers=headers).json()
    assert body["evidence"] == []  # 서명 원본이 없는 시드 데이터는 근거로 쓰지 않는다
    assert body["excluded"] and {e["reason"] for e in body["excluded"]} == {"no_signed_original"}


def test_evidence_strict_and_labeled(client, register, fake_dx):
    user = register()
    ok_id, _ = store_badge(client, user, name="정상 배지")
    bad_id, bad_token = store_badge(client, user, name="폐기될 배지")
    fake_dx.set_badge_result(bad_token, valid=False, credential_status="REVOKED")

    strict = evidence(client, user)
    assert [e["record_id"] for e in strict["evidence"]] == [ok_id]
    assert strict["excluded"] == [{"record_id": bad_id, "status": "revoked", "reason": "revoked"}]
    e = strict["evidence"][0]
    assert e["achievement_name"] == "정상 배지" and e["criteria"] == "테스트" and e["verified"] is True

    labeled = evidence(client, user, mode="labeled")
    assert {(x["record_id"], x["status"]) for x in labeled["evidence"]} == {(ok_id, "verified"), (bad_id, "revoked")}
    assert labeled["excluded"] == []


def test_evidence_fixed_candidates_and_access(client, register):
    alice, bob = register(), register()
    first, _ = store_badge(client, alice, name="첫째")
    store_badge(client, alice, name="둘째")
    only = evidence(client, alice, record_id=first)
    assert [e["record_id"] for e in only["evidence"]] == [first]

    other = client.get(f"/api/v1/subjects/{alice['did']}/evidence", headers=bob["headers"])
    assert other.status_code == 403
    by_service = client.get(f"/api/v1/subjects/{alice['did']}/evidence", headers=SERVICE)
    assert by_service.status_code == 200 and len(by_service.json()["evidence"]) == 2
    missing = client.get(f"/api/v1/subjects/{alice['did']}/evidence", params={"record_id": str(uuid.uuid4())},
                         headers=alice["headers"])
    assert missing.status_code == 404


# 논문 RAG-VC의 DB 변조 시나리오: DB 사본을 믿지 않고 서명 원본을 다시 검증한다.

def test_s1_forged_db_status_is_ignored(client, register, fake_dx, db):
    user = register()
    record_id, token = store_badge(client, user)
    fake_dx.set_badge_result(token, valid=False, credential_status="REVOKED")
    db.execute("UPDATE credentials SET status = 'verified' WHERE record_id = %s", (record_id,))  # 위조
    assert evidence(client, user)["evidence"] == []


def test_s2_tampered_db_text_is_not_used(client, register, db):
    user = register()
    record_id, _ = store_badge(client, user, name="원본 이름")
    db.execute("UPDATE credentials SET raw_data = jsonb_set(raw_data, '{credentialSubject,achievement,name}',"
               " '\"부풀린 이름\"') WHERE record_id = %s", (record_id,))
    [e] = evidence(client, user)["evidence"]
    assert e["achievement_name"] == "원본 이름"
    healed = db.execute("SELECT raw_data FROM credentials WHERE record_id = %s", (record_id,)).fetchone()
    assert healed["raw_data"]["credentialSubject"]["achievement"]["name"] == "원본 이름"  # 사본도 원본으로 복구


def test_s3_lowered_db_status_is_restored(client, register, db):
    user = register()
    record_id, _ = store_badge(client, user)
    db.execute("UPDATE credentials SET status = 'revoked' WHERE record_id = %s", (record_id,))
    assert [e["record_id"] for e in evidence(client, user)["evidence"]] == [record_id]


def test_swapped_signed_original_is_rejected(client, register, db):
    alice, bob = register(), register()
    alice_id, _ = store_badge(client, alice)
    _, bob_token = store_badge(client, bob)
    db.execute("UPDATE credentials SET raw_token = %s WHERE record_id = %s", (bob_token, alice_id))
    body = evidence(client, alice)
    assert body["evidence"] == []
    assert body["excluded"][0]["reason"] == "recipient_mismatch"


# ── 포트폴리오 (VC_ONLY) ──────────────────────────────────────────────

def create_portfolio(client, user, record_ids, **extra):
    payload = {"title": "백엔드 지원용", "target_role": "백엔드 개발자", "record_ids": record_ids,
               "source": "VC_ONLY", **extra}
    return client.post("/api/v1/portfolios", json=payload, headers=user["headers"])


def test_portfolio_uses_only_reverified_credentials(client, register, fake_dx):
    user = register("포트폴리오")
    ok_id, _ = store_badge(client, user, name="정상 배지")
    bad_id, bad_token = store_badge(client, user, name="폐기될 배지")
    fake_dx.set_badge_result(bad_token, valid=False, credential_status="REVOKED")

    res = create_portfolio(client, user, [ok_id, bad_id])
    assert res.status_code == 201, res.text
    p = res.json()["data"]
    assert p["source"] == "VC_ONLY" and p["generator"] == "template" and p["status"] == "draft"
    assert p["referenced_record_ids"] == [ok_id] and p["credential_count"] == 1
    assert p["excluded"] == [{"record_id": bad_id, "status": "revoked", "reason": "revoked"}]
    assert "정상 배지" in p["content_markdown"] and "폐기될 배지" not in p["content_markdown"]
    assert "백엔드 개발자" in p["content_markdown"] and "포트폴리오" in p["content_markdown"]


def test_portfolio_rejects_free_text_and_wrong_source(client, register):
    user = register()
    record_id, _ = store_badge(client, user)
    assert create_portfolio(client, user, [record_id], content_markdown="저는 10년차 개발자").status_code == 422
    assert create_portfolio(client, user, [record_id], source="USER_INPUT").status_code == 422


def test_portfolio_with_no_verified_credential_is_422(client, register, fake_dx):
    user = register()
    record_id, token = store_badge(client, user)
    fake_dx.set_badge_result(token, valid=False, credential_status="REVOKED")
    res = create_portfolio(client, user, [record_id])
    assert res.status_code == 422 and res.json() == {"detail": "No verified credentials selected"}


@pytest.mark.parametrize("error,status,body", [
    (ProviderError("DXWorks 연결 실패: ConnectError"), 502,
     {"detail": "Credential service error", "code": "PROVIDER_ERROR"}),
    (ProviderError("DXWORKS_API_HOST가 설정되지 않았습니다", unavailable=True), 503,
     {"detail": "Credential service is not configured", "code": "PROVIDER_NOT_CONFIGURED"}),
])
def test_portfolio_verifier_outage_is_5xx_not_422_and_saves_nothing(client, register, fake_dx, db, error, status,
                                                                     body):
    user = register()
    record_id, _ = store_badge(client, user)
    fake_dx.error = error
    res = create_portfolio(client, user, [record_id])
    # 검증기 장애를 사용자 탓(422 No verified credentials selected)으로 돌리지 않는다.
    # 응답은 main.py의 일반 오류 본문이다 (내부 메시지·환경 변수 이름은 로그에만 남는다).
    assert res.status_code == status and res.json() == body and error.message not in res.text
    assert db.execute("SELECT count(*) AS n FROM portfolios WHERE user_id = %s", (user["id"],)).fetchone()["n"] == 0


@pytest.mark.parametrize("status", [400, 404, 422, 200])
def test_portfolio_when_every_selected_credential_fails_at_the_verifier(client, register, verifier_calls, db, status):
    # 회귀: 협력사가 우리 호출을 통째로 거절하는 상태(차단·점검)가 4xx로 보이면 자격증명마다의 거절로 읽어
    # 사용자 탓인 422(No verified credentials selected)로 답했다. 서명 원본이 둘 이상인데 하나도 빠짐없이 검증기 오류면
    # 장애로 보고 502로 끝낸다. 하나뿐이면 지금처럼 그 자격증명의 거절이다.
    calls, failing = verifier_calls
    user = register()
    stored = [store_badge(client, user) for _ in range(3)]
    for _, token in stored:
        failing[token] = ProviderError(f"DXWorks 오류 ({status}): rejected", upstream_status=status)

    res = create_portfolio(client, user, [record_id for record_id, _ in stored])
    assert res.status_code == 502 and res.json() == {"detail": "Credential service error", "code": "PROVIDER_ERROR"}
    assert db.execute("SELECT count(*) AS n FROM portfolios WHERE user_id = %s", (user["id"],)).fetchone()["n"] == 0

    one = create_portfolio(client, user, [stored[0][0]])
    assert one.status_code == 422 and one.json() == {"detail": "No verified credentials selected"}
    # 하나라도 검증기가 답했으면(통과든 폐기든) 장애가 아니다.
    del failing[stored[2][1]]
    mixed = create_portfolio(client, user, [record_id for record_id, _ in stored])
    assert mixed.status_code == 201 and mixed.json()["data"]["referenced_record_ids"] == [stored[2][0]]
    assert {e["reason"] for e in mixed.json()["data"]["excluded"]} == {"verifier_error"}


def test_unsigned_rows_do_not_count_towards_the_all_failed_rule(client, register, verifier_calls, db):
    # 서명 원본이 없는 자격증명(시드 데이터 등)은 검증기를 부르지 않으므로 '모두 실패' 판단에 넣지 않는다.
    calls, failing = verifier_calls
    user = register()
    (signed_id, token), (unsigned_id, _) = store_badge(client, user), store_badge(client, user)
    db.execute("UPDATE credentials SET raw_token = NULL WHERE record_id = %s", (unsigned_id,))
    failing[token] = ProviderError("DXWorks 오류 (400): rejected", upstream_status=400)
    res = create_portfolio(client, user, [signed_id, unsigned_id])
    assert res.status_code == 422 and res.json() == {"detail": "No verified credentials selected"}


def test_portfolio_with_someone_elses_credential_is_404(client, register):
    alice, bob = register(), register()
    record_id, _ = store_badge(client, alice)
    assert create_portfolio(client, bob, [record_id]).status_code == 404


def test_portfolio_crud_and_ownership(client, register):
    alice, bob = register(), register()
    record_id, _ = store_badge(client, alice)
    pid = create_portfolio(client, alice, [record_id]).json()["data"]["id"]

    listed = client.get("/api/v1/portfolios", headers=alice["headers"]).json()["data"]
    assert [p["id"] for p in listed] == [pid]
    assert client.get(f"/api/v1/portfolios/{pid}", headers=alice["headers"]).status_code == 200
    assert client.get(f"/api/v1/portfolios/{pid}", headers=bob["headers"]).status_code == 404

    patched = client.patch(f"/api/v1/portfolios/{pid}", json={"title": "  새 제목 ", "status": "published"},
                           headers=alice["headers"])
    assert patched.status_code == 200
    assert patched.json()["data"]["title"] == "새 제목" and patched.json()["data"]["status"] == "published"
    # 본문은 수정할 수 없다 (VC_ONLY)
    assert client.patch(f"/api/v1/portfolios/{pid}", json={"content_markdown": "조작"},
                        headers=alice["headers"]).status_code == 422
    assert client.patch(f"/api/v1/portfolios/{pid}", json={"title": "x"}, headers=bob["headers"]).status_code == 404

    assert client.delete(f"/api/v1/portfolios/{pid}", headers=bob["headers"]).status_code == 404
    assert client.delete(f"/api/v1/portfolios/{pid}", headers=alice["headers"]).status_code == 200
    assert client.get(f"/api/v1/portfolios/{pid}", headers=alice["headers"]).status_code == 404


def test_portfolio_update_rewrites_body_header(client, register):
    user = register("제목수정")
    record_id, _ = store_badge(client, user, name="근거 배지")
    created = create_portfolio(client, user, [record_id]).json()["data"]
    pid, before = created["id"], created["content_markdown"]
    url, headers = f"/api/v1/portfolios/{pid}", user["headers"]

    body = client.patch(url, json={"title": "프론트엔드 지원용"}, headers=headers).json()["data"]["content_markdown"]
    assert body.splitlines()[0] == "# 프론트엔드 지원용"
    assert "- 희망 직무: 백엔드 개발자" in body
    # 근거 섹션은 그대로 둔다
    assert body.split("## 요약", 1)[1] == before.split("## 요약", 1)[1]

    body = client.patch(url, json={"target_role": "프론트엔드 개발자"}, headers=headers).json()["data"]["content_markdown"]
    assert "- 희망 직무: 프론트엔드 개발자" in body and "백엔드 개발자" not in body
    assert body.splitlines()[0] == "# 프론트엔드 지원용"

    body = client.patch(url, json={"target_role": None}, headers=headers).json()["data"]["content_markdown"]
    assert "희망 직무" not in body

    body = client.patch(url, json={"target_role": "데이터 엔지니어"}, headers=headers).json()["data"]["content_markdown"]
    assert body.count("- 희망 직무:") == 1 and "- 희망 직무: 데이터 엔지니어" in body
    assert body.splitlines()[:5] == ["# 프론트엔드 지원용", "", "- 이름: 제목수정", "- 희망 직무: 데이터 엔지니어", ""]

    # 상태만 바꾸면 본문은 그대로
    published = client.patch(url, json={"status": "published"}, headers=headers).json()["data"]
    assert published["content_markdown"] == body


def test_portfolio_requires_user_token(client):
    assert client.get("/api/v1/portfolios").status_code == 401
    assert client.get("/api/v1/portfolios", headers=SERVICE).status_code == 401


# ── 선택 공개 + 기업용 조회 ───────────────────────────────────────────

def create_share(client, user, record_ids, **extra):
    return client.post("/api/v1/shares", json={"record_ids": record_ids, **extra}, headers=user["headers"])


def test_share_shows_only_selected_credentials_without_login(client, register):
    user = register("공유자")
    shared_id, shared_token = store_badge(client, user, name="공개할 배지")
    store_badge(client, user, name="숨길 배지")

    created = create_share(client, user, [shared_id], label="예시회사").json()["data"]
    assert created["share_path"] == f"/share/{created['token']}"

    view = client.get(f"/api/v1/public/shares/{created['token']}")  # 로그인 없음
    assert view.status_code == 200
    body = view.json()
    assert body["holder"] == {"name": "공유자", "did": user["did"]}  # 이메일은 노출하지 않는다
    assert [c["achievement_name"] for c in body["credentials"]] == ["공개할 배지"]
    assert body["credentials"][0]["vc_jwt"] == shared_token and body["credentials"][0]["status"] == "verified"
    assert "label" not in body


def test_share_view_reverifies_at_view_time(client, register, fake_dx):
    user = register()
    record_id, token = store_badge(client, user)
    share_token = create_share(client, user, [record_id]).json()["data"]["token"]
    fake_dx.set_badge_result(token, valid=False, credential_status="REVOKED")
    body = client.get(f"/api/v1/public/shares/{share_token}").json()
    assert body["credentials"][0]["status"] == "revoked" and body["credentials"][0]["verified"] is False


def test_share_list_hides_token_and_counts_views(client, register, db):
    user = register()
    record_id, _ = store_badge(client, user)
    created = create_share(client, user, [record_id], label="A사").json()["data"]
    client.get(f"/api/v1/public/shares/{created['token']}")
    client.get(f"/api/v1/public/shares/{created['token']}")

    [listed] = client.get("/api/v1/shares", headers=user["headers"]).json()["data"]
    assert "token" not in listed and listed["label"] == "A사" and listed["view_count"] == 2
    stored = db.execute("SELECT token_hash FROM shares WHERE id = %s", (created["id"],)).fetchone()["token_hash"]
    assert stored != created["token"] and len(stored) == 64  # 원문 대신 해시만 저장


def test_revoked_expired_and_unknown_links_are_404(client, register, db):
    user = register()
    record_id, _ = store_badge(client, user)
    revoked = create_share(client, user, [record_id]).json()["data"]
    expired = create_share(client, user, [record_id]).json()["data"]

    assert client.delete(f"/api/v1/shares/{revoked['id']}", headers=user["headers"]).status_code == 200
    db.execute("UPDATE shares SET expires_at = NOW() - interval '1 minute' WHERE id = %s", (expired["id"],))
    for token in (revoked["token"], expired["token"], "no-such-token"):
        res = client.get(f"/api/v1/public/shares/{token}")
        assert res.status_code == 404 and res.json() == {"detail": "Share link not found or expired"}


def test_share_ownership_and_validation(client, register):
    alice, bob = register(), register()
    record_id, _ = store_badge(client, alice)
    assert create_share(client, bob, [record_id]).status_code == 404
    assert create_share(client, alice, [record_id], expires_in_days=0).status_code == 422
    assert create_share(client, alice, [record_id], expires_in_days=91).status_code == 422
    share_id = create_share(client, alice, [record_id]).json()["data"]["id"]
    assert client.delete(f"/api/v1/shares/{share_id}", headers=bob["headers"]).status_code == 404
    assert client.post("/api/v1/shares", json={"record_ids": [record_id]}).status_code == 401
