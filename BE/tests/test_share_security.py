"""공유 링크(기업용 조회)·포트폴리오·근거 조회 보안 수정 (실제 PostgreSQL + 가짜 검증기, 가상 데이터).

- 기업용 조회 제한: 로그인 없는 공유 링크 조회는 학생의 DB 사본을 고쳐 쓰지 않는다. 같은 서명 원본은 60초 동안 검증기에
  다시 묻지 않고, 새 검증기 호출은 태스크마다 분당 300번까지(모자라면 검증기를 부르지 않고 503 + Retry-After).
  시간·한도는 테스트용 게이트(손으로 움직이는 시계, 분당 3번)로 바꿔서 본다. 동시에 연 조회도 몫보다 많이 부르지 않고,
  같은 링크를 동시에 여러 번 열면 서명 원본마다 한 번만 묻고 나머지는 그 응답을 기다린다(먼저 묻던 조회가 실패·취소돼도
  기다리던 조회는 끝난다. 한 조회 안에 같은 원본·같은 레코드가 두 번 있어도 끝나고, 기다리는 시간에는 상한이 있다). 학생 본인의 재검증·근거 조회·포트폴리오 생성·직무 비교는 이 제한을 쓰지 않는다
  (그쪽 한도는 tests/test_verifier_budget.py).
- 한 줄 텍스트: 포트폴리오 제목·희망 직무는 안쪽 줄바꿈·제어 문자를 받지 않는다(생성·수정 모두 422).
  작성기는 DB에 이미 있는 여러 줄 값(이름 포함)도 한 줄로 넣는다.
- 저장할 수 없는 문자: NUL·짝 없는 서로게이트가 든 공유 라벨·포트폴리오 제목·희망 직무·근거 조회 DID는 500이 아니라 422.
  짝 없는 서로게이트는 JSON 이스케이프로만 보낼 수 있어 본문을 json.dumps(ASCII)로 직접 만든다
  (TestClient의 json=은 짝 없는 서로게이트를 인코딩하지 못한다).
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app.core.text import SINGLE_LINE_MESSAGE
from app.integrations.ports import ProviderError
from tests.test_features import SERVICE, create_portfolio, create_share, store_badge

NUL, SURROGATE = "a\x00b", "a\ud800b"
BUSY = {"detail": "Share verification is busy. Try again later."}


def public_view(client, share_token: str):
    return client.get(f"/api/v1/public/shares/{share_token}")  # 로그인 없음


def unhandled(caplog) -> list[logging.LogRecord]:
    """500 추적('Unhandled error')과, 요청 검사를 지나 DB 단계 안전망('Rejected unstorable text')에 걸린 기록."""
    return [r for r in caplog.records if r.name == "vc-be" and (
        r.getMessage() == "Unhandled error" or r.getMessage().startswith("Rejected unstorable text"))]


# ── 기업용 조회: DB에 쓰지 않음, 최근 응답 재사용, 호출 한도 ─────────────

@pytest.fixture
def public_gate(monkeypatch):
    """기업용 조회 제한(public_view_gate)을 손으로 움직이는 시계로 바꾼다. 분당 한도는 3번."""
    from app.api import shares
    from app.services.public_view_gate import PublicViewGate

    clock = {"now": 1000.0}
    gate = PublicViewGate(calls_per_minute=3, clock=lambda: clock["now"])
    monkeypatch.setattr(shares, "public_view_gate", gate)
    return gate, clock


@pytest.fixture
def badge_calls(fake_dx, monkeypatch) -> list:
    """가짜 검증기에 들어온 배지 검증 호출(서명 원본)을 센다."""
    calls = []
    original = fake_dx.verify_badge

    async def counting(vc_jwt):
        calls.append(vc_jwt)
        return await original(vc_jwt)

    monkeypatch.setattr(fake_dx, "verify_badge", counting)
    return calls


def test_share_view_does_not_write_owner_rows(client, register, db):
    user = register()
    record_id, _ = store_badge(client, user)
    share_token = create_share(client, user, [record_id]).json()["data"]["token"]
    # DB 사본을 서명 원본과 다르게 해 둔다: 익명 조회가 이것을 '고쳐 쓰지' 않는지까지 본다.
    db.execute("UPDATE credentials SET status = 'revoked' WHERE record_id = %s", (record_id,))
    row_sql = "SELECT status, raw_data, last_verified_at FROM credentials WHERE record_id = %s"
    checks_sql = ("SELECT check_name, result, detail, checked_at FROM verification_checks WHERE record_id = %s"
                  " ORDER BY check_name")
    row, checks = db.execute(row_sql, (record_id,)).fetchone(), db.execute(checks_sql, (record_id,)).fetchall()

    view = public_view(client, share_token)
    assert view.status_code == 200 and view.json()["credentials"][0]["status"] == "verified"
    # 로그인 없는 조회는 학생 지갑(DB 사본)을 고쳐 쓰지 않는다. 열람 수만 센다.
    assert db.execute(row_sql, (record_id,)).fetchone() == row
    assert db.execute(checks_sql, (record_id,)).fetchall() == checks
    assert client.get("/api/v1/shares", headers=user["headers"]).json()["data"][0]["view_count"] == 1

    # 본인의 근거 조회는 지금처럼 다시 검증해 DB 사본을 서명 원본 기준으로 고친다.
    body = client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"]).json()
    assert [e["record_id"] for e in body["evidence"]] == [record_id]
    after = db.execute(row_sql, (record_id,)).fetchone()
    assert after["status"] == "verified" and after["last_verified_at"] != row["last_verified_at"]


def test_share_view_reuses_recent_verifier_answer(client, register, fake_dx, public_gate, badge_calls):
    _, clock = public_gate
    user = register()
    record_id, token = store_badge(client, user)
    share_token = create_share(client, user, [record_id]).json()["data"]["token"]
    badge_calls.clear()

    fake_dx.error = ProviderError("DXWorks 연결 실패: ConnectError")
    assert public_view(client, share_token).json()["credentials"][0]["status"] == "unverifiable"
    fake_dx.error = None  # 검증기 오류는 담아 두지 않으므로 다음 조회는 다시 묻는다
    first = public_view(client, share_token).json()["credentials"][0]
    assert first["status"] == "verified" and len(badge_calls) == 2

    fake_dx.set_badge_result(token, valid=False, credential_status="REVOKED")
    clock["now"] += 59
    # 60초 안에 다시 열면 검증기에 다시 묻지 않고 그때 결과(verified_at 포함)를 그대로 보여 준다.
    assert public_view(client, share_token).json()["credentials"][0] == first and len(badge_calls) == 2
    # 학생 본인의 재검증은 이 제한을 쓰지 않는다: 바로 검증기에 물어 폐기를 반영한다.
    own = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"]).json()
    assert own["data"]["status"] == "revoked" and len(badge_calls) == 3

    clock["now"] += 1  # 처음 물은 지 60초: 다시 묻는다
    later = public_view(client, share_token).json()["credentials"][0]
    assert later["status"] == "revoked" and later["verified_at"] != first["verified_at"] and len(badge_calls) == 4


def test_share_view_over_verifier_budget_is_503_without_calls(client, register, db, public_gate, badge_calls,
                                                              caplog):
    _, clock = public_gate
    user = register()
    first_ids = [store_badge(client, user)[0] for _ in range(2)]
    second_ids = [store_badge(client, user)[0] for _ in range(2)]
    unsigned_id, _ = store_badge(client, user)
    # 시드 데이터처럼 서명 원본이 없는 자격증명
    db.execute("UPDATE credentials SET raw_token = NULL WHERE record_id = %s", (unsigned_id,))
    first = create_share(client, user, first_ids).json()["data"]["token"]
    second = create_share(client, user, second_ids, label="B사").json()["data"]["token"]
    unsigned = create_share(client, user, [unsigned_id]).json()["data"]["token"]
    badge_calls.clear()

    assert public_view(client, first).status_code == 200 and len(badge_calls) == 2
    # 남은 몫(1)보다 많이(2) 필요하면 검증기를 하나도 부르지 않고 503 + Retry-After (분당 3번 → 1번 채우는 데 20초)
    with caplog.at_level(logging.WARNING, logger="app.api.shares"):
        busy = public_view(client, second)
    assert busy.status_code == 503 and busy.json() == BUSY
    assert busy.headers["retry-after"] == "20" and len(badge_calls) == 2
    [log] = [r.getMessage() for r in caplog.records if r.name == "app.api.shares"]
    assert "budget exceeded" in log and "retry_after=20" in log and second not in log  # 링크 토큰은 남기지 않는다

    # 학생 본인의 재검증은 기업용 조회 한도와 무관하다.
    own = client.post(f"/api/v1/credentials/{second_ids[0]}/reverify", headers=user["headers"])
    assert own.status_code == 200 and own.json()["success"] is True and len(badge_calls) == 3
    # 최근에 확인한 원본만 보여 주는 조회와 서명 원본이 없는 자격증명만 보여 주는 조회는 몫이 필요 없다.
    assert public_view(client, first).status_code == 200 and len(badge_calls) == 3
    view = public_view(client, unsigned)
    assert view.status_code == 200 and view.json()["credentials"][0]["status"] == "unverifiable"
    assert len(badge_calls) == 3

    clock["now"] += 20
    assert public_view(client, second).status_code == 200 and len(badge_calls) == 5
    # 503 응답도 열람 수에 들어간다 (링크를 연 사실은 같으므로).
    views = {s["label"]: s["view_count"] for s in client.get("/api/v1/shares", headers=user["headers"]).json()["data"]}
    assert views["B사"] == 2


def open_concurrently(share_tokens: list[str]) -> list[httpx.Response]:
    """기업용 조회 여러 개를 한 이벤트 루프에서 동시에 연다 (로그인 없음)."""
    from main import app

    async def open_all():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
            return await asyncio.gather(*(ac.get(f"/api/v1/public/shares/{token}") for token in share_tokens))

    return asyncio.run(open_all())


@pytest.fixture
def hold_verifier(fake_dx, public_gate, monkeypatch):
    """views개의 조회가 모두 몫을 요청한 뒤에야 검증기가 답하게 한다 (그 전에는 60초 동안 다시 쓸 응답이 없다).

    모든 조회가 같은 순간의 몫·진행 중인 호출을 두고 겨루게 된다. 몫 요청 기록(호출 수 목록)을 돌려준다.
    """
    gate, _ = public_gate

    def hold(views: int) -> list[int]:
        everyone_asked = asyncio.Event()
        asked, reserve, verify = [], gate.reserve, fake_dx.verify_badge

        def counting_reserve(calls):
            asked.append(calls)
            if len(asked) == views:
                everyone_asked.set()
            reserve(calls)

        async def slow_verify(vc_jwt):
            try:
                await asyncio.wait_for(everyone_asked.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass  # 몫을 요청하지 않은 조회가 있으면(코드가 바뀜) 더 기다리지 않고 아래 검사에 맡긴다
            return await verify(vc_jwt)

        monkeypatch.setattr(gate, "reserve", counting_reserve)
        monkeypatch.setattr(fake_dx, "verify_badge", slow_verify)
        return asked

    return hold


def test_concurrent_share_views_never_exceed_verifier_budget(client, register, badge_calls, hold_verifier):
    """동시에 연 서로 다른 링크 20개도 남은 몫(분당 3번)보다 많이 검증기를 부르지 않는다.

    몫은 검증기를 부르기 전에 한꺼번에 가져간다. 20개 조회가 모두 몫을 요청한 뒤에야 검증기가 답하게 해서 모든 조회가
    같은 몫을 두고 겨루게 한다: 3개만 검증기를 부르고 나머지는 503.
    """
    views = 20
    user = register()
    share_tokens = [create_share(client, user, [store_badge(client, user)[0]]).json()["data"]["token"]
                    for _ in range(views)]
    badge_calls.clear()
    asked = hold_verifier(views)

    statuses = sorted(res.status_code for res in open_concurrently(share_tokens))
    assert statuses == [200] * 3 + [503] * (views - 3)
    assert len(badge_calls) == 3 and asked == [1] * views


def test_concurrent_views_of_one_link_ask_the_verifier_once(client, register, badge_calls, hold_verifier):
    """회귀: 같은 링크를 동시에 열면 조회마다 몫을 가져가 같은 원본을 거듭 물었다 (몫이 바닥나 나머지와 다른 링크는 503).

    이제 먼저 묻는 조회 하나만 몫을 가져가 부르고, 나머지는 그 응답을 기다려 함께 쓴다.
    """
    views = 20
    user = register()
    record_ids = [store_badge(client, user)[0] for _ in range(2)]
    other_id, _ = store_badge(client, user)
    share_token = create_share(client, user, record_ids).json()["data"]["token"]
    other_token = create_share(client, user, [other_id]).json()["data"]["token"]
    badge_calls.clear()
    asked = hold_verifier(views)

    responses = open_concurrently([share_token] * views)
    assert [res.status_code for res in responses] == [200] * views
    assert len(badge_calls) == 2 and sorted(asked) == [0] * (views - 1) + [2]  # 원본마다 한 번, 몫도 한 번만
    first = responses[0].json()["credentials"]
    assert [c["status"] for c in first] == ["verified", "verified"]
    assert all(res.json()["credentials"] == first for res in responses)  # 같은 응답(verified_at 포함)을 함께 쓴다
    # 이 링크가 쓴 몫은 2뿐이라 남은 몫(1)으로 다른 링크도 열린다. 60초 안에 다시 열면 지금처럼 검증기를 부르지 않는다.
    assert public_view(client, other_token).status_code == 200 and len(badge_calls) == 3
    assert public_view(client, share_token).json()["credentials"] == first and len(badge_calls) == 3


def test_waiting_share_views_end_with_the_first_callers_failure(client, register, fake_dx, badge_calls, hold_verifier):
    """먼저 묻던 조회가 검증기 오류로 끝나면 기다리던 조회도 같은 실패('검증 불가')로 끝난다 (다시 부르지도, 멈추지도 않는다)."""
    views = 10
    user = register()
    record_id, _ = store_badge(client, user)
    share_token = create_share(client, user, [record_id]).json()["data"]["token"]
    badge_calls.clear()
    hold_verifier(views)
    fake_dx.error = ProviderError("DXWorks 오류 (400): bad token", upstream_status=400)

    responses = open_concurrently([share_token] * views)
    assert [res.status_code for res in responses] == [200] * views
    assert {res.json()["credentials"][0]["status"] for res in responses} == {"unverifiable"}
    assert len(badge_calls) == 1
    # 실패는 담아 두지 않는다: 다음 조회는 다시 묻는다.
    fake_dx.error = None
    assert public_view(client, share_token).json()["credentials"][0]["status"] == "verified" and len(badge_calls) == 2


def _row(vc_jwt: str, record_id: str = "00000000-0000-0000-0000-0000000000aa") -> dict:
    """reverify_rows에 넘기는 credentials 행 (DB 없이 게이트 동작만 볼 때)."""
    return {"record_id": record_id, "raw_token": vc_jwt, "raw_data": {}, "issuer_name": "예시대학교",
            "achievement_name": "테스트 수료 배지", "issued_at": None, "expires_at": None,
            "subject_did": "did:key:z6MkGateOnly"}


async def test_waiting_view_does_not_hang_when_the_first_caller_is_cancelled(fake_dx):
    from app.services.public_view_gate import PublicViewGate
    from app.services.reverify import reverify_rows
    from tests.conftest import make_vc_jwt

    gate = PublicViewGate()
    row = _row(make_vc_jwt("did:key:z6MkGateOnly"))
    started = asyncio.Event()

    class Stuck:
        async def verify_badge(self, vc_jwt):
            started.set()
            await asyncio.sleep(3600)

    def view(verifier):
        return asyncio.ensure_future(reverify_rows([row], verifier, caller="anonymous", route="share_view",
                                                   persist=False, gate=gate))

    leader = view(Stuck())
    await started.wait()
    waiter, second_waiter = view(fake_dx), view(fake_dx)
    await asyncio.sleep(0.01)  # 두 조회 모두 먼저 묻는 조회의 응답을 기다린다
    assert not waiter.done() and gate._tokens == gate.capacity - 1  # 기다리는 조회는 몫을 가져가지 않았다
    # 기다리던 조회 하나가 취소돼도 함께 기다리는 다른 조회·먼저 묻는 조회는 그대로다.
    second_waiter.cancel()
    await asyncio.sleep(0.01)
    assert not waiter.done() and not leader.done()

    leader.cancel()  # 먼저 묻던 요청이 사라졌다 (연결 종료 등)
    [outcome] = await asyncio.wait_for(waiter, timeout=2)
    assert outcome.status == "unverifiable" and outcome.reason == "verifier_error"
    assert isinstance(outcome.error, ProviderError)
    assert not gate._flights  # 남은 호출 기록이 없다: 다음 조회는 새로 묻는다
    [again] = await asyncio.wait_for(view(fake_dx), timeout=2)
    assert again.status == "invalid" and again.reason == "record_mismatch"  # 검증기 응답으로 판정까지 갔다 (가짜 행이라 ID만 다름)


class FailingVerifier:
    """부른 서명 원본을 세고 항상 같은 오류로 실패하는 검증기 (조금 늦게 답해 동시 호출이 겹치게 한다)."""

    def __init__(self, error: ProviderError, delay: float = 0.01):
        self.error, self.delay, self.calls = error, delay, []

    async def verify_badge(self, vc_jwt):
        self.calls.append(vc_jwt)
        await asyncio.sleep(self.delay)
        raise self.error


async def test_rows_sharing_one_signed_original_finish_during_an_outage(fake_dx):
    # 회귀: 한 조회 안에 같은 서명 원본이 두 번 있으면 뒤 행은 앞 행의 호출을 기다린다. 장애가 확인돼 앞 행이 검증기를 부르지
    # 않고 건너뛰면 그 호출은 끝났다고 알려지지 않아, 뒤 행이(그리고 그 원본을 여는 다음 조회도) 끝없이 기다렸다.
    from app.services.public_view_gate import PublicViewGate
    from app.services.reverify import MAX_CONCURRENCY, reverify_rows
    from tests.conftest import make_vc_jwt

    gate = PublicViewGate()
    shared = make_vc_jwt("did:key:z6MkGateOnly")
    rows = [_row(make_vc_jwt("did:key:z6MkGateOnly"), str(uuid.uuid4())) for _ in range(6)]
    rows += [_row(shared, str(uuid.uuid4())), _row(shared, str(uuid.uuid4()))]  # 7·8번째 행이 같은 원본
    verifier = FailingVerifier(ProviderError("DXWorks 오류 (500): boom", upstream_status=500))

    outcomes = await asyncio.wait_for(
        reverify_rows(rows, verifier, caller="anonymous", route="share_view", persist=False, gate=gate), timeout=3)
    assert [(o.status, o.reason) for o in outcomes] == [("unverifiable", "verifier_error")] * 8
    assert all(isinstance(o.error, ProviderError) for o in outcomes)
    assert 1 <= len(verifier.calls) <= MAX_CONCURRENCY and shared not in verifier.calls
    assert not gate._flights  # 남은 호출 기록이 없다
    # 그 원본을 여는 다음 조회는 새로 묻는다 (앞 조회에 묶여 멈추지 않는다).
    [again] = await asyncio.wait_for(
        reverify_rows([_row(shared)], fake_dx, caller="anonymous", route="share_view", persist=False, gate=gate),
        timeout=3)
    assert again.status == "invalid" and again.reason == "record_mismatch"  # 검증기 응답으로 판정까지 갔다


async def test_same_record_listed_twice_asks_the_verifier_once_and_finishes(badge_calls, fake_dx):
    # 회귀: 할 일을 record_id로 찾았다. 같은 레코드가 목록에 두 번 있으면 앞 행의 '부른다'가 뒤 행의 '기다린다'로 덮여
    # 두 행 모두 아무도 하지 않는 호출을 끝없이 기다렸다 (검증기 호출 0번). 이제 행과 같은 자리로 짝짓는다.
    from app.services.public_view_gate import PublicViewGate
    from app.services.reverify import reverify_rows
    from tests.conftest import make_vc_jwt

    gate = PublicViewGate()
    row = _row(make_vc_jwt("did:key:z6MkGateOnly"))
    other = _row(make_vc_jwt("did:key:z6MkGateOnly"), "00000000-0000-0000-0000-0000000000bb")
    outcomes = await asyncio.wait_for(
        reverify_rows([row, dict(row), other, dict(row)], fake_dx, caller="anonymous", route="share_view",
                      persist=False, gate=gate), timeout=3)
    assert len(outcomes) == 4 and len(badge_calls) == 2  # 원본마다 한 번
    assert [o.record_id for o in outcomes] == [row["record_id"]] * 2 + [other["record_id"], row["record_id"]]
    assert {o.reason for o in outcomes} == {"record_mismatch"}  # 네 행 모두 검증기 응답으로 판정까지 갔다
    assert outcomes[0].evidence == outcomes[1].evidence == outcomes[3].evidence
    assert not gate._flights and gate._tokens == gate.capacity - 2


async def test_waiting_for_another_views_call_is_bounded(fake_dx, monkeypatch):
    # 먼저 묻던 조회가 끝내 결과를 알리지 않아도, 기다리는 조회는 어댑터의 호출 한 번 상한(자리 대기 + 교환)만큼만 기다린다.
    from app.core.config import get_settings
    from app.services import reverify
    from app.services.public_view_gate import PublicViewGate
    from tests.conftest import make_vc_jwt

    assert reverify._flight_wait_seconds() == 4 * get_settings().dxworks_timeout_seconds
    monkeypatch.setattr(reverify, "_flight_wait_seconds", lambda: 0.2)
    gate = PublicViewGate()
    row = _row(make_vc_jwt("did:key:z6MkGateOnly"))
    [(kind, flight)] = gate.begin([row["raw_token"]])  # 다른 조회가 묻기로 하고는 알리지 않는다
    assert kind == "call"

    started = asyncio.get_running_loop().time()
    [outcome] = await asyncio.wait_for(
        reverify.reverify_rows([row], fake_dx, caller="anonymous", route="share_view", persist=False, gate=gate),
        timeout=3)
    assert 0.15 <= asyncio.get_running_loop().time() - started < 2
    assert (outcome.status, outcome.reason) == ("unverifiable", "verifier_error")
    assert isinstance(outcome.error, ProviderError)
    assert not flight.future.done()  # 함께 기다리던 Future는 취소되지 않았다 (먼저 묻던 쪽이 나중에 알릴 수 있다)
    gate.settle(flight, ProviderError("late"))
    assert await gate.wait(flight) is flight.future.result() and await gate.wait(flight, 0.1) is flight.future.result()


async def test_public_view_gate_single_flight_plan():
    from app.services.public_view_gate import BudgetExceeded, PublicViewGate

    clock = {"now": 0.0}
    gate = PublicViewGate(calls_per_minute=3, clock=lambda: clock["now"])
    answer, verified_at = _answer(), datetime(2026, 11, 9, tzinfo=timezone.utc)
    gate.store("cached.x.y", verified_at, answer)

    # 담아 둔 응답 → cached, 처음 보는 원본 → call, 같은 목록에 두 번 나온 원본 → 뒤의 것은 wait. 몫은 call만큼만.
    steps = gate.begin(["cached.x.y", "a.b.c", "a.b.c", "d.e.f"])
    assert [kind for kind, _ in steps] == ["cached", "call", "wait", "call"]
    assert steps[0][1] == (verified_at, answer) and steps[1][1] is steps[2][1] and gate._tokens == 1
    # 다른 조회가 같은 원본을 물으면 기다린다 (몫을 가져가지 않는다).
    [(kind, flight)] = gate.begin(["a.b.c"])
    assert kind == "wait" and flight is steps[1][1] and gate._tokens == 1

    # 몫이 모자라면 아무것도 등록하지 않는다: 다음 조회가 '진행 중'으로 오해해 끝없이 기다리지 않게.
    with pytest.raises(BudgetExceeded):
        gate.begin(["g.h.i", "j.k.l"])
    assert set(gate._flights) == {gate._key("a.b.c"), gate._key("d.e.f")} and gate._tokens == 1

    # 결과를 알리면 기다리던 쪽이 받고 기록은 지워진다. 두 번 알려도 처음 것만 쓴다.
    gate.settle(steps[1][1], (verified_at, answer))
    gate.settle(steps[1][1], ProviderError("late"))
    assert await gate.wait(flight) == (verified_at, answer)
    failure = ProviderError("DXWorks 연결 실패: ConnectError")
    gate.settle(steps[3][1], failure)
    assert await gate.wait(steps[3][1]) is failure and not gate._flights
    # 실패한 원본은 다음 조회가 새로 묻는다.
    assert [kind for kind, _ in gate.begin(["d.e.f"])] == ["call"] and gate._tokens == 0

    # 다른 이벤트 루프에서 등록된(기다릴 수 없는) 호출은 없는 것으로 보고 새로 묻는다.
    gate.clear()
    stale = gate.begin(["m.n.o"])[0][1]
    other_loop = asyncio.new_event_loop()
    try:
        gate._flights[stale.key] = type(stale)(stale.key, other_loop, other_loop.create_future())
        assert [kind for kind, _ in gate.begin(["m.n.o"])] == ["call"]
    finally:
        other_loop.close()


def test_owner_flows_ignore_public_view_budget_and_cache(client, register, fake_dx, badge_calls, monkeypatch):
    """학생 본인의 재검증·근거 조회(사용자·서비스 키)·포트폴리오 생성·직무 비교는 기업용 조회 제한을 쓰지 않는다.

    한도는 클래스에서 바꿔 어느 게이트 객체(프로세스 하나에 하나인 public_view_gate 포함)든 바닥난 것으로 만든다.
    이 경로들이 게이트를 쓰기 시작하면 실패한다: 한도에 걸리면 500, 60초 재사용이면 검증기에 묻지 않아 호출 수가 모자란다.
    """
    from app.services.career import build_snapshot
    from app.services.database import db_service
    from app.services.public_view_gate import BudgetExceeded, PublicViewGate

    user = register()
    record_id, _ = store_badge(client, user)
    other_id, _ = store_badge(client, user)
    seen = create_share(client, user, [record_id]).json()["data"]["token"]
    unseen = create_share(client, user, [other_id]).json()["data"]["token"]
    assert public_view(client, seen).status_code == 200  # 이 서명 원본의 응답은 60초 동안 기업용 조회에 다시 쓰인다

    def exhausted(self, calls):
        raise BudgetExceeded(60)

    monkeypatch.setattr(PublicViewGate, "reserve", exhausted)
    badge_calls.clear()
    assert public_view(client, unseen).status_code == 503 and badge_calls == []

    own = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"])
    assert own.status_code == 200 and own.json()["success"] is True and len(badge_calls) == 1
    for headers in (user["headers"], SERVICE):
        res = client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=headers)
        assert res.status_code == 200 and len(res.json()["evidence"]) == 2
    assert len(badge_calls) == 5
    assert create_portfolio(client, user, [record_id]).status_code == 201 and len(badge_calls) == 6
    # 직무 비교(POST /career-analyses)가 재검증하는 곳은 build_snapshot 하나다.
    snapshot = asyncio.run(build_snapshot(db_service.get_user_by_id(user["id"]), fake_dx))
    assert len(snapshot.accepted_credentials) == 2 and len(badge_calls) == 8


def test_share_view_uses_process_gate_with_decided_limits(client, register, badge_calls):
    from app.api import shares
    from app.services import public_view_gate as gate_module

    assert gate_module.RESULT_TTL_SECONDS == 60 and gate_module.CALLS_PER_MINUTE == 300
    assert shares.public_view_gate is gate_module.public_view_gate
    user = register()
    record_id, _ = store_badge(client, user)
    share_token = create_share(client, user, [record_id]).json()["data"]["token"]
    badge_calls.clear()
    for _ in range(3):
        assert public_view(client, share_token).json()["credentials"][0]["status"] == "verified"
    assert len(badge_calls) == 1  # 같은 서명 원본은 60초 동안 한 번만 묻는다


def _answer():
    from app.domain.credential import ProviderBadgeResult

    return ProviderBadgeResult(valid=True, signature_ok=True, issuer_trusted=True, credential_status="VALID",
                               recipient_did=None, credential_id=None, issuer_name=None, achievement_name=None,
                               issued_at=None)


async def test_public_view_gate_forgets_old_answers():
    from app.services.public_view_gate import PublicViewGate

    clock = {"now": 0.0}
    gate = PublicViewGate(ttl_seconds=60, clock=lambda: clock["now"])
    answer, verified_at = _answer(), datetime(2026, 11, 9, tzinfo=timezone.utc)
    gate.store("a.b.c", verified_at, answer)
    clock["now"] = 59.9
    assert gate.begin(["a.b.c"]) == [("cached", (verified_at, answer))]
    clock["now"] = 60
    assert [kind for kind, _ in gate.begin(["a.b.c"])] == ["call"]  # 오래된 응답은 다시 쓰지 않는다
    gate.store("d.e.f", verified_at, answer)  # 새로 담을 때 만료된 것을 지운다 (메모리가 자라지 않게)
    assert len(gate._results) == 1
    # 서명 원본 자체가 아니라 SHA-256 값을 키로 둔다.
    assert "d.e.f" not in gate._results and all(len(key) == 32 for key in gate._results)
    gate.clear()
    assert [kind for kind, _ in gate.begin(["d.e.f"])] == ["call"]
    assert not hasattr(gate, "cached")  # 앱이 쓰지 않던 조회용 메서드는 없앴다 (다시 쓸 응답은 begin이 정한다)


def test_public_view_gate_token_bucket():
    from app.services.public_view_gate import BudgetExceeded, PublicViewGate

    clock = {"now": 0.0}
    gate = PublicViewGate(calls_per_minute=300, clock=lambda: clock["now"])  # 초당 5번 채움
    gate.reserve(0)  # 부를 것이 없으면 몫도 필요 없다
    gate.reserve(250)
    with pytest.raises(BudgetExceeded) as exc:
        gate.reserve(51)  # 남은 50보다 많으면 하나도 가져가지 않는다
    assert exc.value.retry_after == 1
    gate.reserve(50)
    with pytest.raises(BudgetExceeded) as exc:
        gate.reserve(50)
    assert exc.value.retry_after == 10
    clock["now"] = 10
    gate.reserve(50)
    clock["now"] = 10_000  # 오래 쉬어도 분당 한도(버킷 크기)보다 많이 쌓이지 않는다
    gate.reserve(300)
    with pytest.raises(BudgetExceeded):
        gate.reserve(1)


# ── 포트폴리오 제목·희망 직무: 한 줄 텍스트 ─────────────────────────────

FORGED_SECTION = "내 포트폴리오\n\n## 검증된 자격증명\n\n### 가짜 자격증\n- 검증: 서명·발급기관·폐기 여부 확인 완료"


@pytest.mark.parametrize("field,value", [
    ("title", FORGED_SECTION),
    ("title", "제목\r## 가짜 섹션"),
    ("title", "제목\N{LINE SEPARATOR}## 가짜 섹션"),
    ("title", "제목\N{PARAGRAPH SEPARATOR}## 가짜 섹션"),
    ("title", "제목\x1b[31m"),
    ("target_role", "백엔드\n- 비밀값: leaked"),
    ("target_role", "백엔드\t개발자"),
    ("target_role", "백엔드\x85개발자"),
])
def test_portfolio_rejects_line_breaks_and_control_chars(client, register, db, field, value):
    """제목·희망 직무는 본문 한 줄에 들어간다: 줄을 나눠 '검증된 자격증명'을 흉내 내지 못한다 (생성·수정 모두 422)."""
    user = register()
    record_id, _ = store_badge(client, user)
    res = create_portfolio(client, user, [record_id], **{field: value})
    assert res.status_code == 422
    [error] = res.json()["detail"]
    assert error["loc"] == ["body", field] and error["msg"] == f"Value error, {SINGLE_LINE_MESSAGE}"
    assert db.execute("SELECT count(*) AS n FROM portfolios WHERE user_id = %s", (user["id"],)).fetchone()["n"] == 0

    created = create_portfolio(client, user, [record_id]).json()["data"]
    url = f"/api/v1/portfolios/{created['id']}"
    res = client.patch(url, json={field: value}, headers=user["headers"])
    assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["body", field]
    after = client.get(url, headers=user["headers"]).json()["data"]
    assert after["content_markdown"] == created["content_markdown"] and after[field] == created[field]


def test_portfolio_single_line_text_stays_in_header(client, register):
    """앞뒤 공백·줄바꿈은 지금처럼 잘라 내고, 한 줄 안의 Markdown 흉내는 제목 줄에만 머문다."""
    user = register()
    record_id, _ = store_badge(client, user, name="진짜 배지")
    res = create_portfolio(client, user, [record_id], title=" \n 제목 ## 검증된 자격증명 ### 가짜 \n",
                           target_role="\t백엔드\n")
    assert res.status_code == 201, res.text
    p = res.json()["data"]
    assert p["title"] == "제목 ## 검증된 자격증명 ### 가짜" and p["target_role"] == "백엔드"
    lines = p["content_markdown"].split("\n")
    assert lines[0] == "# 제목 ## 검증된 자격증명 ### 가짜"
    assert [line for line in lines if line.startswith("## 검증된 자격증명")] == ["## 검증된 자격증명"]
    assert [line for line in lines if line.startswith("### ")] == ["### 진짜 배지"]

    res = client.patch(f"/api/v1/portfolios/{p['id']}",
                       json={"title": "\n새 제목\n", "target_role": " 데이터 엔지니어\r\n"}, headers=user["headers"])
    assert res.status_code == 200 and res.json()["data"]["title"] == "새 제목"
    assert res.json()["data"]["content_markdown"].split("\n")[:4] == ["# 새 제목", "", "- 이름: 테스트",
                                                                      "- 희망 직무: 데이터 엔지니어"]

    # 이모지(ZWJ 결합 포함)·NBSP는 한 줄 텍스트로 받는다.
    title, role = "백엔드 \U0001F469‍\U0001F4BB 지원", "데이터 엔지니어"
    res = create_portfolio(client, user, [record_id], title=title, target_role=role)
    assert res.status_code == 201 and (res.json()["data"]["title"], res.json()["data"]["target_role"]) == (title, role)


def test_portfolio_and_share_keep_existing_validation_responses(client, register):
    """빈 제목·null·빈 희망 직무·길이 초과는 지금과 같은 응답이다 (상태 코드와 메시지 모두)."""
    user = register()
    record_id, _ = store_badge(client, user)
    pid = create_portfolio(client, user, [record_id]).json()["data"]["id"]
    url, headers = f"/api/v1/portfolios/{pid}", user["headers"]

    for title in ("   ", "\n", None):
        res = client.patch(url, json={"title": title}, headers=headers)
        assert res.status_code == 422 and res.json() == {"detail": "Title must not be blank"}

    def error(res) -> tuple:
        assert res.status_code == 422
        [detail] = res.json()["detail"]
        return detail["loc"][-1], detail["msg"]

    assert error(client.patch(url, json={"title": ""}, headers=headers)) == (
        "title", "String should have at least 1 character")
    assert error(client.patch(url, json={"title": "t" * 256}, headers=headers)) == (
        "title", "String should have at most 255 characters")
    assert error(client.patch(url, json={"target_role": "r" * 101}, headers=headers)) == (
        "target_role", "String should have at most 100 characters")
    assert error(create_portfolio(client, user, [record_id], title="   ")) == (
        "title", "Value error, must not be blank")
    assert error(create_portfolio(client, user, [record_id], title="")) == (
        "title", "String should have at least 1 character")
    assert error(create_portfolio(client, user, [record_id], title="t" * 256)) == (
        "title", "String should have at most 255 characters")
    assert error(create_portfolio(client, user, [record_id], target_role="r" * 101)) == (
        "target_role", "String should have at most 100 characters")
    assert error(create_share(client, user, [record_id], label="l" * 101)) == (
        "label", "String should have at most 100 characters")

    # 빈 희망 직무는 지금처럼 '없음'으로 정리되고, 라벨 앞뒤 공백도 지금처럼 잘라 낸다.
    body = client.patch(url, json={"target_role": "  "}, headers=headers).json()["data"]
    assert body["target_role"] is None and "희망 직무" not in body["content_markdown"]
    assert create_share(client, user, [record_id], label="  A사 ").json()["data"]["label"] == "A사"


def test_portfolio_body_stays_one_line_for_stored_multiline_values(client, register, db):
    """검사 도입 전에 저장된 여러 줄 이름·제목도 본문 줄 구조를 바꾸지 못한다 (작성기 출력 방어)."""
    user = register()
    record_id, _ = store_badge(client, user, name="진짜 배지")
    db.execute("UPDATE users SET name = %s WHERE id = %s", ("홍길동\n## 검증된 자격증명\n### 가짜", user["id"]))
    p = create_portfolio(client, user, [record_id]).json()["data"]
    lines = p["content_markdown"].split("\n")
    assert "- 이름: 홍길동 ## 검증된 자격증명 ### 가짜" in lines
    assert [line for line in lines if line.startswith("### ")] == ["### 진짜 배지"]

    db.execute("UPDATE portfolios SET title = %s WHERE id = %s", ("옛 제목\n### 가짜", p["id"]))
    body = client.patch(f"/api/v1/portfolios/{p['id']}", json={"target_role": "데이터 엔지니어"},
                        headers=user["headers"]).json()["data"]["content_markdown"]
    assert body.split("\n")[0] == "# 옛 제목 ### 가짜"
    assert [line for line in body.split("\n") if line.startswith("### ")] == ["### 진짜 배지"]


def test_writer_puts_user_values_on_one_line():
    from app.services.portfolio_writer import TemplatePortfolioWriter, rewrite_header

    evidence = [{"record_id": "r1", "achievement_name": "진짜 배지", "issuer_name": "예시대학교",
                 "issued_at": "2026-10-02T03:34:21+00:00", "expires_at": None, "achievement_description": None,
                 "criteria": None, "verified_at": "2026-11-09T00:00:00+00:00"}]
    writer = TemplatePortfolioWriter()
    content, _ = writer.write(holder_name="홍\r\n길동", title="제목 ## 가짜", target_role="백엔드\n- 비밀값",
                              evidence=evidence)
    lines = content.split("\n")
    assert lines[:4] == ["# 제목 ## 가짜", "", "- 이름: 홍 길동", "- 희망 직무: 백엔드 - 비밀값"]
    assert [line for line in lines if line.startswith("## ")] == ["## 요약", "## 검증된 자격증명"]

    # 한 줄 값은 그대로 들어간다.
    plain, _ = writer.write(holder_name="홍길동", title="백엔드 지원용", target_role=None, evidence=evidence)
    assert plain.split("\n")[:4] == ["# 백엔드 지원용", "", "- 이름: 홍길동", ""]
    rewritten = rewrite_header(plain, title="새\n## 가짜", target_role="데이터\r엔지니어")
    assert rewritten.split("\n")[:4] == ["# 새 ## 가짜", "", "- 이름: 홍길동", "- 희망 직무: 데이터 엔지니어"]
    assert rewritten.split("## 요약", 1)[1] == plain.split("## 요약", 1)[1]


# ── 저장할 수 없는 문자(NUL·짝 없는 서로게이트) ─────────────────────────

def send_json(client, method: str, url: str, body: dict, headers: dict):
    return client.request(method, url, content=json.dumps(body),
                          headers={"Content-Type": "application/json", **headers})


@pytest.fixture
def holder(client, register):
    """검증된 배지 하나와 포트폴리오 하나를 가진 사용자 (공유 링크는 없음)."""
    user = register()
    user["record_id"], _ = store_badge(client, user)
    user["portfolio_id"] = create_portfolio(client, user, [user["record_id"]]).json()["data"]["id"]
    return user


TEXT_FIELDS = [
    # (이름, 메서드, 경로, 본문, 422 loc 마지막 값)
    ("share.label", "POST", "/api/v1/shares", lambda v, u: {"record_ids": [u["record_id"]], "label": v}, "label"),
    ("portfolio.title", "POST", "/api/v1/portfolios",
     lambda v, u: {"title": v, "record_ids": [u["record_id"]], "source": "VC_ONLY"}, "title"),
    ("portfolio.target_role", "POST", "/api/v1/portfolios",
     lambda v, u: {"title": "t", "target_role": v, "record_ids": [u["record_id"]], "source": "VC_ONLY"},
     "target_role"),
    ("portfolio.patch.title", "PATCH", "/api/v1/portfolios/{portfolio_id}", lambda v, u: {"title": v}, "title"),
    ("portfolio.patch.target_role", "PATCH", "/api/v1/portfolios/{portfolio_id}",
     lambda v, u: {"target_role": v}, "target_role"),
]


@pytest.mark.parametrize("bad", [NUL, SURROGATE], ids=["nul", "surrogate"])
@pytest.mark.parametrize("name,method,url,body,field", TEXT_FIELDS, ids=[f[0] for f in TEXT_FIELDS])
def test_unstorable_text_is_422_and_stores_nothing(client, holder, db, caplog, bad, name, method, url, body, field):
    with caplog.at_level(logging.WARNING):
        res = send_json(client, method, url.format(**holder), body(bad, holder), holder["headers"])
    assert res.status_code == 422, res.text
    assert res.json()["detail"][0]["loc"] == ["body", field]
    assert not unhandled(caplog)
    counts = db.execute("SELECT (SELECT count(*) FROM shares WHERE user_id = %(u)s) AS shares,"
                        " (SELECT count(*) FROM portfolios WHERE user_id = %(u)s) AS portfolios",
                        {"u": holder["id"]}).fetchone()
    assert counts == {"shares": 0, "portfolios": 1}
    p = client.get(f"/api/v1/portfolios/{holder['portfolio_id']}", headers=holder["headers"]).json()["data"]
    assert (p["title"], p["target_role"]) == ("백엔드 지원용", "백엔드 개발자")


@pytest.mark.parametrize("who", ["user", "service"])
def test_evidence_did_path_with_nul_is_422(client, register, caplog, who):
    user = register()
    headers = user["headers"] if who == "user" else SERVICE
    with caplog.at_level(logging.WARNING):
        res = client.get(f"/api/v1/subjects/{user['did']}%00/evidence", headers=headers)
    assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["path", "did"]
    assert not unhandled(caplog)
    # 정상 DID는 그대로 조회된다.
    assert client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=headers).status_code == 200


def test_emoji_text_is_still_accepted(client, holder):
    """서로게이트 쌍으로 이스케이프된 정상 문자(이모지)는 그대로 받는다."""
    label = "A사 \U0001F600"
    res = send_json(client, "POST", "/api/v1/shares", {"record_ids": [holder["record_id"]], "label": label},
                    holder["headers"])
    assert res.status_code == 201 and res.json()["data"]["label"] == label
    title = "포트폴리오 \U0001F680"
    res = send_json(client, "PATCH", f"/api/v1/portfolios/{holder['portfolio_id']}", {"title": title},
                    holder["headers"])
    assert res.status_code == 200 and res.json()["data"]["content_markdown"].split("\n")[0] == f"# {title}"
