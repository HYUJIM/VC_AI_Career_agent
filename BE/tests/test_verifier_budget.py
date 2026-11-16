"""로그인 사용자·서비스 키의 검증기(DXWorks) 호출 한도 (app/services/verifier_budget.py).

- 버킷 계산(호출자의 분·시간 버킷과 프로세스 전체, 모자라면 아무것도 가져가지 않음, 버킷보다 큰 요청, 키 수 상한,
  Retry-After는 모자란 버킷들 가운데 가장 긴 대기)은 손으로 움직이는 시계로 본다.
- API: 배지 검증·한 건 재검증·근거 조회·포트폴리오 생성은 검증기를 부르기 전에 몫을 한꺼번에 가져가고, 모자라면 검증기를
  한 번도 부르지 않고 429 + Retry-After로 답한다 (실제 PostgreSQL + 가짜 검증기). 직무 비교는 tests/test_career.py,
  실제 어댑터에서 협력사로 나가는 요청 수는 tests/test_dxworks_adapter.py에서 본다.
- 기업용 조회(공유 링크)는 이 한도가 아니라 public_view_gate의 한도를 쓴다 (두 번 세지 않는다).
- 거절된 배지 검증 창: POST /verify가 검증기까지 가서 그 토큰이 거절된 요청은 호출자마다 10분에 10번까지다 (저장된 배지와
  검증기가 답한 뒤의 우리 쪽 실패는 세지 않는다).
- 실패 차단기(모의 발급): 실패가 한도만큼 쌓이면 멈추고 간격마다 탐침 하나만 보낸다. API 쪽은 tests/test_credential_security.py.
- 감사 로그(vc-be.audit): 검증기를 부른 요청마다 한 줄, 한도로 거절한 요청마다 한 줄. 이메일·DID·토큰은 없다.
"""
import asyncio
import logging
import re
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from app.integrations.ports import ProviderError
from app.services import verifier_budget as budget_module
from app.services.verifier_budget import AttemptWindow, FailureBreaker, VerifierBudget, VerifierBudgetExceeded
from tests.conftest import make_vc_jwt
from tests.test_domain import make_raw_jwt
from tests.test_features import SERVICE, create_portfolio, create_share, store_badge

TOO_MANY = {"detail": "Too many verification requests. Try again later."}


def denied(budget: VerifierBudget, key: str, calls: int) -> int:
    with pytest.raises(VerifierBudgetExceeded) as exc:
        budget.reserve(key, calls)
    return exc.value.retry_after


# ── 버킷 계산 ─────────────────────────────────────────────────────────

def test_decided_limits_and_process_instances():
    assert (budget_module.CALLS_PER_MINUTE, budget_module.PROCESS_CALLS_PER_MINUTE) == (120, 600)
    assert budget_module.CALLS_PER_HOUR == 1200
    assert budget_module.MAX_KEYS == 10_000
    assert (budget_module.MOCK_ISSUE_ATTEMPTS, budget_module.MOCK_ISSUE_WINDOW_SECONDS) == (30, 24 * 60 * 60)
    assert (budget_module.REJECTED_VERIFICATIONS, budget_module.REJECTED_VERIFICATION_WINDOW_SECONDS) == (10, 600)
    budget = budget_module.verifier_budget
    assert (budget.capacity, budget.process_capacity, budget.max_keys) == (120, 600, 10_000)
    assert (budget.hour_capacity, budget.hour_rate) == (1200, 1200 / 3600)
    attempts = budget_module.mock_issue_attempts
    assert (attempts.max_attempts, attempts.window_seconds, attempts.max_keys) == (30, 86400, 10_000)
    rejected = budget_module.rejected_verifications
    assert (rejected.max_attempts, rejected.window_seconds, rejected.max_keys) == (10, 600, 10_000)
    breaker = budget_module.mock_issue_breaker  # 태스크 전체의 실패 차단기: 한도는 요청마다 설정에서 읽는다
    assert budget_module.MOCK_ISSUE_PROBE_INTERVAL_SECONDS == 60
    assert (breaker.window_seconds, breaker.probe_interval_seconds) == (86400, 60)
    assert budget_module.TOO_MANY_VERIFICATIONS == TOO_MANY["detail"]  # FE가 이 문구로 번역한다


def test_caller_bucket_refills_at_the_decided_rate():
    clock = {"now": 0.0}
    budget = VerifierBudget(clock=lambda: clock["now"])  # 호출자마다 분당 120번 = 초당 2번
    budget.reserve("alice", 0)  # 부를 것이 없으면 몫도 필요 없다
    budget.reserve("alice", 120)
    assert denied(budget, "alice", 1) == 1
    budget.reserve("bob", 120)  # 다른 호출자는 따로 센다
    clock["now"] = 0.5
    budget.reserve("alice", 1)
    assert denied(budget, "alice", 1) == 1
    clock["now"] = 10.5
    assert denied(budget, "alice", 30) == 5  # 20번 찼다: 10번 더 차려면 5초
    budget.reserve("alice", 20)
    clock["now"] = 10_000  # 오래 쉬어도 버킷 크기보다 많이 쌓이지 않는다
    budget.reserve("alice", 120)
    assert denied(budget, "alice", 1) == 1


def test_denied_request_takes_nothing_from_either_bucket():
    clock = {"now": 0.0}
    budget = VerifierBudget(calls_per_minute=10, process_calls_per_minute=15, clock=lambda: clock["now"])
    budget.reserve("alice", 6)
    assert denied(budget, "alice", 5) == 6  # 남은 4보다 많다: 하나도 가져가지 않는다 (초당 1/6번)
    budget.reserve("alice", 4)  # 남은 4는 그대로 있었다
    budget.reserve("bob", 5)  # 프로세스 몫도 줄지 않았다 (15 - 6 - 4 = 5)
    # 프로세스 몫이 모자라 거절된 요청은 호출자 자기 몫도 쓰지 않는다 (처음 보는 호출자는 기록도 생기지 않는다).
    assert denied(budget, "carol", 1) == 4 and denied(budget, "bob", 1) == 4
    assert "carol" not in budget._buckets and budget._buckets["bob"][0] == 5
    clock["now"] = 4
    budget.reserve("carol", 1)
    assert denied(budget, "bob", 1) == 4


def test_process_bucket_bounds_all_callers_together():
    clock = {"now": 0.0}
    budget = VerifierBudget(clock=lambda: clock["now"])  # 프로세스 전체 분당 600번 = 초당 10번
    for user in range(5):
        budget.reserve(f"user-{user}", 120)
    assert denied(budget, "user-5", 1) == 1
    assert denied(budget, "user-5", 120) == 12
    clock["now"] = 12
    budget.reserve("user-5", 120)
    assert denied(budget, "user-6", 1) == 1


def test_request_larger_than_the_bucket_is_served_and_the_rate_stays_bounded():
    # 자격증명이 버킷 크기보다 많은 사용자의 근거 조회·직무 비교가 영영 429가 되지 않게: 버킷이 가득이면 받아 주고 잔액을 음수로 둔다.
    clock = {"now": 0.0}
    budget = VerifierBudget(clock=lambda: clock["now"])
    budget.reserve("alice", 500)  # 버킷 120, 잔액 -380
    assert denied(budget, "alice", 1) == 191  # (380 + 1) / 초당 2번
    clock["now"] = 190
    assert denied(budget, "alice", 1) == 1
    clock["now"] = 190.5
    budget.reserve("alice", 1)
    # 다시 큰 요청을 하려면 버킷이 가득 차야 한다: 500번을 250초에 한 번꼴 = 분당 120번 그대로.
    assert denied(budget, "alice", 500) == 60
    clock["now"] = 250.5
    budget.reserve("alice", 500)

    # 프로세스 버킷(600)보다 큰 요청도 같다: 가득이면 받고, 음수가 찰 때까지 모두가 기다린다.
    clock["now"] = 100_000
    budget.reserve("bulk", 1000)
    assert denied(budget, "carol", 1) == 41  # (400 + 1) / 초당 10번
    clock["now"] += 41
    budget.reserve("carol", 1)


def test_caller_hour_bucket_bounds_the_sustained_rate():
    # 회귀: 호출자 한도가 분 버킷뿐이라 계정 하나가 분당 120번을 끝없이 부를 수 있었다 (계정 6개면 프로세스 몫 600을 계속 비움).
    # 이제 호출자마다 시간 버킷(1200)도 있어야 한다: 시간 몫을 다 쓴 뒤에는 분당 20번만 부를 수 있다.
    clock = {"now": 0.0}
    budget = VerifierBudget(clock=lambda: clock["now"])
    served = []
    for second in range(2 * 3600):  # 2시간 동안 초당 2번(분 버킷이 다시 차는 속도 그대로) 부른다
        clock["now"] = float(second)
        ok = 0
        for _ in range(2):
            try:
                budget.reserve("alice", 1)
                ok += 1
            except VerifierBudgetExceeded:
                pass
        served.append(ok)
    assert sum(served[:600]) == 1200  # 처음 10분은 분당 120번 그대로 (보통 사용은 여기에 닿지 않는다)
    assert 2399 <= sum(served[:3600]) <= 2400  # 가득 찬 시간 버킷 1200 + 한 시간 동안 다시 찬 몫 1200
    # 시간 몫을 다 쓴 뒤의 한 시간: 분당 20번 (프로세스 몫 600을 계속 비우려면 이런 계정이 30개 필요하다)
    second_hour = [sum(served[start:start + 60]) for start in range(3600, 7200, 60)]
    assert all(19 <= per_minute <= 21 for per_minute in second_hour) and 1199 <= sum(second_hour) <= 1201
    # 다른 호출자와 프로세스 몫에는 영향이 없다.
    budget.reserve("bob", 120)
    assert budget._process[0] > 400


def test_hour_bucket_denial_takes_nothing_and_large_requests_follow_the_same_rule():
    clock = {"now": 0.0}
    budget = VerifierBudget(calls_per_minute=10, process_calls_per_minute=1000, calls_per_hour=30,
                            clock=lambda: clock["now"])
    for minute in range(3):  # 분 버킷(10)을 세 번 다 쓴다: 시간 버킷 30 + 그동안 다시 찬 1
        clock["now"] = minute * 60.0
        budget.reserve("alice", 10)
    clock["now"] = 180.0
    # 분 버킷은 다시 가득이지만 시간 버킷이 모자라다: 아무것도 가져가지 않는다 (분 버킷·프로세스 몫 그대로).
    assert denied(budget, "alice", 2) == 60  # 남은 1.5에서 0.5번 더 차려면 60초 (시간당 30번)
    assert budget._buckets["alice"][0] == 10 and budget._buckets["alice"][2] == pytest.approx(1.5)
    assert budget._process[0] == 1000
    assert denied(budget, "alice", 10) == 1020 and budget._process[0] == 1000
    budget.reserve("alice", 1)
    assert budget._buckets["alice"][2] == pytest.approx(0.5) and budget._process[0] == 999
    # 시간 버킷보다 큰 요청도 분 버킷과 같은 규칙이다: 가득 찼을 때만 받고 잔액을 음수로 둔다.
    budget = VerifierBudget(calls_per_minute=1000, process_calls_per_minute=10_000, calls_per_hour=100,
                            clock=lambda: clock["now"])
    budget.reserve("bulk", 150)  # 시간 버킷 100, 잔액 -50
    assert denied(budget, "bulk", 1) == 1836  # (50 + 1) / (100 / 3600)
    clock["now"] += 1836
    budget.reserve("bulk", 1)
    assert denied(budget, "bulk", 150) == 3600  # 다시 큰 요청을 하려면 시간 버킷이 가득 차야 한다


def test_retry_after_is_the_longest_wait_of_the_short_buckets():
    # 회귀: 호출자 버킷이 모자라면 그 대기 시간만 알려, 프로세스 몫이 더 오래 걸려도 Retry-After가 짧게 나갔다
    # (그만큼 기다린 뒤 다시 보내면 또 429였다).
    clock = {"now": 0.0}
    budget = VerifierBudget(calls_per_minute=60, process_calls_per_minute=60, clock=lambda: clock["now"])
    budget.reserve("alice", 30)
    budget.reserve("bob", 30)  # 프로세스 몫 0, alice 30, 둘 다 초당 1번씩 찬다
    assert denied(budget, "alice", 40) == 40  # 호출자 10초, 프로세스 40초 → 40초
    assert denied(budget, "carol", 20) == 20  # 호출자 몫은 충분하고 프로세스 몫만 모자라다
    clock["now"] = 40.0
    budget.reserve("alice", 40)  # 알려 준 만큼 기다리면 받는다
    # 시간 버킷이 가장 오래 걸리면 그 값이다.
    budget = VerifierBudget(calls_per_minute=60, process_calls_per_minute=600, calls_per_hour=60,
                            clock=lambda: clock["now"])
    budget.reserve("alice", 60)
    assert denied(budget, "alice", 1) == 60  # 분 버킷은 1초면 되지만 시간 버킷은 60초
    clock["now"] += 30
    assert 30 <= denied(budget, "alice", 1) <= 31  # (부동소수점 올림으로 1초 길어질 수는 있어도 짧게 알리지는 않는다)
    clock["now"] += 31
    budget.reserve("alice", 1)


def test_caller_keys_are_bounded_and_only_created_by_successful_reservations():
    clock = {"now": 0.0}
    budget = VerifierBudget(calls_per_minute=2, process_calls_per_minute=7, max_keys=3, clock=lambda: clock["now"])
    for key in ("a", "b", "c"):
        budget.reserve(key, 2)
    assert list(budget._buckets) == ["a", "b", "c"]
    budget.reserve("d", 1)  # 가장 오래 쓰지 않은 키(a)를 버린다
    assert list(budget._buckets) == ["b", "c", "d"]
    # 거절된 요청은 키를 만들지 않는다: 새 키를 몰아 보내도 남의 기록을 밀어내지 못한다.
    for n in range(100):
        denied(budget, f"flood-{n}", 1)
    assert list(budget._buckets) == ["b", "c", "d"]
    assert denied(budget, "b", 1) >= 1  # b의 빈 버킷은 그대로다
    budget.clear()
    assert not budget._buckets
    budget.reserve("b", 2)


def test_attempt_window_counts_releases_and_expires():
    clock = {"now": 0.0}
    attempts = AttemptWindow(3, 100, max_keys=2, clock=lambda: clock["now"])
    assert [attempts.reserve("alice") for _ in range(4)] == [True, True, True, False]
    assert attempts.reserve("bob") is True  # 키마다 따로 센다
    attempts.release("alice")  # 협력사를 부르지 않고 끝난 시도는 돌려준다
    assert attempts.reserve("alice") is True and attempts.reserve("alice") is False
    clock["now"] = 100  # 0초의 시도들이 창 밖으로 나간다
    assert attempts.reserve("alice") is True
    attempts.release("nobody")  # 기록이 없어도 오류가 아니다
    attempts.reserve("carol")  # 키는 max_keys개까지: 가장 오래 쓰지 않은 bob을 버린다
    assert list(attempts._attempts) == ["alice", "carol"]
    attempts.clear()
    assert attempts.reserve("alice") is True


def test_attempt_window_limit_per_call_and_wait():
    clock = {"now": 0.0}
    attempts = AttemptWindow(3, 100, clock=lambda: clock["now"])
    # 한도를 호출할 때 줄 수 있다 (설정에서 요청마다 읽는 한도). 주지 않으면 만들 때의 한도다.
    assert [attempts.reserve("alice", 5) for _ in range(6)] == [True] * 5 + [False]
    assert attempts.reserve("alice") is False and attempts.reserve("alice", 6) is True
    # 기다릴 시간: 가장 오래된 시도가 창 밖으로 나갈 때까지 (최소 1초).
    assert attempts.retry_after("alice") == 100
    clock["now"] = 40.5
    assert attempts.retry_after("alice") == 60
    clock["now"] = 99.9
    assert attempts.retry_after("alice") == 1
    assert attempts.retry_after("nobody") == 1
    clock["now"] = 100.0
    assert attempts.reserve("alice", 6) is True  # 0초의 시도들이 나갔다


# ── 실패 차단기 (모의 발급: 실패가 한도만큼 쌓이면 멈추고, 간격마다 탐침 하나) ──────────

def make_breaker(window_seconds: float = 24 * 60 * 60):
    clock = {"now": 0.0}
    return FailureBreaker(window_seconds, 60, clock=lambda: clock["now"]), clock


def fail(breaker: FailureBreaker, limit: int, times: int) -> None:
    for _ in range(times):
        ticket = breaker.enter(limit)
        assert ticket[0] == "counted"
        breaker.leave(ticket, "failure")


def test_failure_breaker_keeps_only_failures_and_expires_them():
    breaker, clock = make_breaker(window_seconds=1000)
    # 보통 요청: 성공과 협력사를 부르지 않고 끝난 요청은 자리를 돌려주고, 실패만 남는다.
    for outcome, kept in (("success", 0), ("not_sent", 0), ("failure", 1)):
        ticket = breaker.enter(3)
        assert ticket[0] == "counted"
        breaker.leave(ticket, outcome)
        assert len(breaker._failures) == kept, outcome
    # 진행 중인 요청도 자리를 잡고 있다: 한꺼번에 들어와도 한도(3)를 넘지 못한다 (남은 실패 1 + 진행 중 2).
    first, second = breaker.enter(3), breaker.enter(3)
    assert (first[0], second[0]) == ("counted", "counted") and len(breaker._failures) == 3
    # 끝난 요청은 자기 자리만 돌려준다 (나중에 들어온 요청이 성공해도 먼저 들어온 요청의 자리는 그대로다).
    breaker.leave(second, "success")
    assert first[1] in breaker._failures and second[1] not in breaker._failures
    breaker.leave(first, "failure")
    assert len(breaker._failures) == 2
    # 창(1000초)이 지난 실패는 저절로 나간다.
    clock["now"] = 500.0
    fail(breaker, 3, 1)
    clock["now"] = 1000.0  # 0초의 실패 둘이 나갔다 (500초의 실패는 남는다)
    fail(breaker, 3, 2)
    assert len(breaker._failures) == 3 and breaker._last_probe is None  # 창이 찬 채로 들어온 요청이 없었다: 탐침도 없었다
    breaker.clear()
    assert len(breaker._failures) == 0


def test_failure_breaker_lets_one_probe_through_per_interval():
    breaker, clock = make_breaker()
    fail(breaker, 5, 5)
    # 창이 찼다: 탐침이 나간 적이 없으면 다음 요청 하나가 바로 탐침이고, 그 뒤 60초 동안은 거절이다.
    probe = breaker.enter(5)
    assert probe[0] == "probe"
    assert [breaker.enter(5) for _ in range(3)] == [None] * 3 and breaker.retry_after() == 60
    breaker.leave(probe, "failure")  # 실패한 탐침은 아무것도 바꾸지 않는다: 다음 탐침은 한 간격 뒤다
    assert len(breaker._failures) == 5 and breaker.enter(5) is None
    clock["now"] = 59.5
    assert breaker.enter(5) is None and breaker.retry_after() == 1
    clock["now"] = 60.0
    probe = breaker.enter(5)
    assert probe[0] == "probe" and breaker.enter(5) is None and breaker.retry_after() == 60
    breaker.leave(probe, "failure")
    # 협력사가 계속 실패하는 한 시간 동안 1초마다 두드려도 탐침은 분당 하나다.
    probes = 0
    for second in range(61, 3661):
        clock["now"] = float(second)
        ticket = breaker.enter(5)
        if ticket is not None:
            assert ticket[0] == "probe"
            breaker.leave(ticket, "failure")
            probes += 1
    assert probes == 60 and len(breaker._failures) == 5


def test_failure_breaker_successful_probe_clears_every_failure():
    breaker, _ = make_breaker()
    fail(breaker, 5, 4)
    in_flight = breaker.enter(5)  # 진행 중인 보통 요청 하나 (다섯 번째 자리)
    probe = breaker.enter(5)
    assert in_flight[0] == "counted" and probe[0] == "probe" and breaker.enter(5) is None
    breaker.leave(probe, "success")
    assert len(breaker._failures) == 0  # 협력사가 다시 된다: 다음 요청부터 보통 요청이다
    tickets = [breaker.enter(5) for _ in range(5)]
    assert [ticket[0] for ticket in tickets] == ["counted"] * 5
    # 지워지기 전에 들어온 보통 요청이 뒤늦게 끝나도 (같은 시각에 들어온) 남의 자리를 돌려주지 않는다.
    breaker.leave(in_flight, "success")
    assert len(breaker._failures) == 5
    for ticket in tickets:
        breaker.leave(ticket, "success")
    assert len(breaker._failures) == 0


def test_failure_breaker_probe_that_was_not_sent_is_given_back():
    breaker, clock = make_breaker()
    fail(breaker, 2, 2)
    # 탐침으로 들어온 요청이 협력사를 부르지 않고 끝나면(DB 한도 거절, 설정 오류) 탐침 기회를 쓰지 않는다: 다음 요청이 탐침이다.
    for _ in range(3):
        probe = breaker.enter(2)
        assert probe[0] == "probe" and breaker.enter(2) is None
        breaker.leave(probe, "not_sent")
    assert breaker._last_probe is None
    # 되돌리는 값은 직전 탐침 시각이다: 실패한 탐침(0초) 뒤 70초에 나간 탐침을 돌려주면 다음 요청이 다시 탐침이다.
    breaker.leave(breaker.enter(2), "failure")
    clock["now"] = 70.0
    breaker.leave(breaker.enter(2), "not_sent")
    assert breaker._last_probe == 0.0
    slow = breaker.enter(2)
    assert slow[0] == "probe" and breaker.enter(2) is None
    # 탐침이 진행 중인데 간격이 지나 다음 탐침이 나갔으면, 먼저 나간 탐침이 not_sent로 끝나도 탐침 시각을 건드리지 않는다.
    clock["now"] = 130.0
    assert breaker.enter(2)[0] == "probe"
    breaker.leave(slow, "not_sent")
    assert breaker._last_probe == 130.0 and breaker.enter(2) is None


def test_failure_breaker_full_of_requests_in_flight_is_not_paused():
    # 회귀(XBE-2): 진행 중인 요청의 자리만으로 창이 차도(아무것도 실패하지 않았다) 탐침을 들여보내고 나머지는 '멈춤'으로
    # 거절했다. 이제 창에 실패로 끝난 요청이 하나라도 있을 때만 멈춘 상태다. 그 밖에는 탐침 없이 거절하고 그렇다고 알린다.
    breaker, clock = make_breaker(window_seconds=1000)
    in_flight = [breaker.enter(3) for _ in range(3)]
    assert [ticket[0] for ticket in in_flight] == ["counted"] * 3
    assert breaker.admit(3) == (None, False)
    clock["now"] = 100.0  # 탐침 간격이 지나도 같다
    assert breaker.admit(3) == (None, False) and breaker.enter(3) is None and breaker._last_probe is None
    # 하나가 실패로 끝나면 멈춘 상태다: 탐침 하나를 들여보내고, 그 밖에는 거절한다.
    breaker.leave(in_flight[0], "failure")
    probe, paused = breaker.admit(3)
    assert probe[0] == "probe" and paused
    assert breaker.admit(3) == (None, True) and breaker.enter(3) is None
    # 성공한 탐침은 실패 표시까지 지운다. 그때 진행 중이던 요청이 뒤늦게 실패로 끝나도(지워진 자리) 표시가 남지 않는다.
    breaker.leave(probe, "success")
    breaker.leave(in_flight[1], "failure")
    assert len(breaker._failures) == 0 and breaker._finished == set()
    # 창 밖으로 나간 실패는 표시도 함께 나간다: 남은 자리가 모두 진행 중이면 다시 탐침 없이 거절한다.
    breaker.clear()
    clock["now"] = 0.0
    fail(breaker, 3, 1)  # 0초: 실패로 끝난 요청 하나
    clock["now"] = 500.0
    assert [breaker.enter(3)[0] for _ in range(2)] == ["counted"] * 2  # 500초: 진행 중인 요청 둘 (창이 찼다)
    clock["now"] = 1000.0  # 0초의 실패가 창 밖으로 나갔다
    assert breaker.enter(3)[0] == "counted"
    assert breaker.admit(3) == (None, False) and breaker._finished == set()


def test_failure_breaker_is_exact_under_threads():
    breaker, _ = make_breaker()

    def hammer(_):
        tickets = []
        for _ in range(200):
            ticket = breaker.enter(50)
            tickets.append(ticket)
            if ticket is not None:
                breaker.leave(ticket, "failure")
        return tickets

    with ThreadPoolExecutor(16) as pool:
        tickets = [ticket for batch in pool.map(hammer, range(16)) for ticket in batch]
    # 16스레드 × 200번: 보통 요청은 정확히 한도(50)만큼, 탐침은 (시계가 멈춰 있으므로) 하나뿐이다.
    assert Counter(ticket[0] if ticket else None for ticket in tickets) == {"counted": 50, "probe": 1, None: 3149}
    assert len(breaker._failures) == 50


def test_process_breaker_is_left_full_by_this_test():
    # 다음 테스트와 한 쌍이다: conftest의 reset_call_budgets가 테스트마다 프로세스의 차단기를 비우는지 본다 (한 테스트가
    # 남긴 실패 기록·탐침 시각이 다른 테스트의 429가 되지 않게). 이 테스트는 차단기를 가득 채운 채로 끝난다.
    breaker = budget_module.mock_issue_breaker
    assert len(breaker._failures) == 0 and breaker._last_probe is None
    fail(breaker, 3, 3)
    assert breaker.enter(3)[0] == "probe" and breaker.enter(3) is None


def test_process_breaker_starts_empty_in_the_next_test():
    breaker = budget_module.mock_issue_breaker
    assert len(breaker._failures) == 0 and breaker._last_probe is None
    assert breaker.enter(1)[0] == "counted"


# ── API: 부르기 전에 몫을 가져가고, 모자라면 검증기 호출 0번 + 429 ──────────

@pytest.fixture
def budget(monkeypatch):
    """프로세스의 검증기 호출 한도를 손으로 움직이는 시계와 작은 한도로 바꾼다. install(...)이 새 한도를 끼운다."""
    clock = {"now": 1000.0}

    def install(**limits) -> VerifierBudget:
        replaced = VerifierBudget(clock=lambda: clock["now"], **limits)
        monkeypatch.setattr(budget_module, "verifier_budget", replaced)
        return replaced

    return install, clock


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


def audit_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "vc-be.audit"]


def test_verify_beyond_the_budget_is_429_without_calling_the_verifier(client, register, budget, badge_calls):
    install, clock = budget
    install()  # 운영과 같은 한도 (호출자마다 분당 120번), 시계만 멈춰 둔다
    user, other = register(), register()
    _, token = store_badge(client, user)
    badge_calls.clear()
    statuses = [client.post("/api/v1/verify", json={"type": "badge", "token": token},
                            headers=user["headers"]).status_code for _ in range(200)]
    assert statuses == [200] * 119 + [429] * 81 and len(badge_calls) == 119  # store_badge가 이미 한 번 썼다
    res = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"])
    assert res.status_code == 429 and res.json() == TOO_MANY and res.headers["retry-after"] == "1"
    assert res.headers["cache-control"] == "no-store"
    # 다른 사용자는 자기 몫을 쓴다.
    assert store_badge(client, other)[0] and len(badge_calls) == 120
    clock["now"] += 30  # 초당 2번씩 다시 찬다
    statuses = [client.post("/api/v1/verify", json={"type": "badge", "token": token},
                            headers=user["headers"]).status_code for _ in range(70)]
    assert statuses == [200] * 60 + [429] * 10


def test_fan_out_reserves_every_call_before_the_first_one(client, register, budget, badge_calls):
    install, clock = budget
    user = register()
    record_ids = [store_badge(client, user)[0] for _ in range(5)]
    install(calls_per_minute=12)
    badge_calls.clear()

    evidence_url = f"/api/v1/subjects/{user['did']}/evidence"
    assert client.get(evidence_url, headers=user["headers"]).status_code == 200 and len(badge_calls) == 5
    assert client.get(evidence_url, headers=user["headers"]).status_code == 200 and len(badge_calls) == 10
    # 남은 몫 2 < 필요한 몫 5: 2번만 부르고 멈추는 것이 아니라 하나도 부르지 않는다.
    res = client.get(evidence_url, headers=user["headers"])
    assert res.status_code == 429 and res.json() == TOO_MANY and res.headers["retry-after"] == "15"
    res = create_portfolio(client, user, record_ids[:3])
    assert res.status_code == 429 and res.json() == TOO_MANY and res.headers["retry-after"] == "5"
    assert len(badge_calls) == 10
    # 몫 안의 요청은 지금처럼 처리된다.
    assert create_portfolio(client, user, record_ids[:1]).status_code == 201
    assert client.post(f"/api/v1/credentials/{record_ids[0]}/reverify", headers=user["headers"]).status_code == 200
    assert len(badge_calls) == 12
    res = client.post(f"/api/v1/credentials/{record_ids[0]}/reverify", headers=user["headers"])
    assert res.status_code == 429 and res.json() == TOO_MANY and len(badge_calls) == 12
    # 거절된 요청은 아무것도 저장하지 않는다.
    assert len(client.get("/api/v1/portfolios", headers=user["headers"]).json()["data"]) == 1
    clock["now"] += 25
    assert client.get(evidence_url, headers=user["headers"]).status_code == 200 and len(badge_calls) == 17


def test_rows_without_a_signed_original_need_no_budget(client, register, db, budget, badge_calls):
    install, _ = budget
    user = register()
    record_id, _ = store_badge(client, user)
    db.execute("UPDATE credentials SET raw_token = NULL WHERE record_id = %s", (record_id,))
    install(calls_per_minute=1, process_calls_per_minute=1).reserve(user["id"], 1)  # 몫을 다 썼다
    badge_calls.clear()
    res = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"])
    assert res.status_code == 200 and res.json()["message"] == "No signed original to re-verify."
    assert client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"]).status_code == 200
    assert badge_calls == []


def test_request_needing_more_than_the_bucket_is_served_when_the_bucket_is_full(client, register, budget,
                                                                                  badge_calls):
    install, clock = budget
    user = register()
    for _ in range(5):
        store_badge(client, user)
    install(calls_per_minute=3)  # 자격증명(5)이 버킷(3)보다 많은 사용자
    badge_calls.clear()
    url = f"/api/v1/subjects/{user['did']}/evidence"
    res = client.get(url, headers=user["headers"])
    assert res.status_code == 200 and len(res.json()["evidence"]) == 5 and len(badge_calls) == 5
    res = client.get(url, headers=user["headers"])
    assert res.status_code == 429 and res.headers["retry-after"] == "100"  # (3 + 2) / 초당 0.05번
    clock["now"] += 100
    assert client.get(url, headers=user["headers"]).status_code == 200 and len(badge_calls) == 10


def test_process_wide_budget_bounds_all_users_and_the_service_key(client, register, budget, badge_calls):
    install, clock = budget
    alice, bob, carol = register(), register(), register()
    for user in (alice, bob, carol):
        store_badge(client, user)
        store_badge(client, user)
    install(calls_per_minute=4, process_calls_per_minute=7)
    badge_calls.clear()

    def evidence(user, headers=None):
        return client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=headers or user["headers"])

    assert [evidence(alice).status_code, evidence(alice).status_code] == [200, 200]  # alice 4번
    assert evidence(alice).status_code == 429  # 자기 몫이 먼저 막는다
    assert evidence(bob).status_code == 200  # 프로세스 6번
    # 서비스 키는 누구의 DID를 읽든 "service" 한 키를 쓴다. 프로세스 몫이 1뿐이라 2번은 부를 수 없다.
    denied_service = evidence(carol, SERVICE)
    assert denied_service.status_code == 429 and denied_service.json() == TOO_MANY
    assert evidence(carol).status_code == 429 and len(badge_calls) == 6
    clock["now"] += 60
    assert [evidence(alice, SERVICE).status_code, evidence(bob, SERVICE).status_code] == [200, 200]
    assert evidence(carol, SERVICE).status_code == 429  # service 키의 몫(4)을 다 썼다
    assert evidence(carol).status_code == 200 and len(badge_calls) == 12


def test_requests_refused_before_the_verifier_cost_no_budget(client, register, budget, badge_calls):
    install, _ = budget
    alice, bob = register(), register()
    record_id, token = store_badge(client, alice)
    limits = install(calls_per_minute=2)
    badge_calls.clear()
    body = {"type": "badge", "token": token}
    assert client.post("/api/v1/verify", json=body).status_code == 401
    assert client.post("/api/v1/verify", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/api/v1/verify", json=body, headers=SERVICE).status_code == 403
    assert client.post("/api/v1/verify", json={"type": "badge", "token": "not-a-jwt"},
                       headers=alice["headers"]).status_code == 200  # compact JWS가 아님: 검증기를 부르지 않는다
    assert client.post("/api/v1/verify", json={"type": "identity", "token": token},
                       headers=alice["headers"]).status_code == 400  # 신원 검증은 기본으로 꺼져 있다
    assert client.post(f"/api/v1/credentials/{record_id}/reverify", headers=bob["headers"]).status_code == 404
    assert client.get(f"/api/v1/subjects/{alice['did']}/evidence", headers=bob["headers"]).status_code == 403
    assert create_portfolio(client, bob, [record_id]).status_code == 404
    assert badge_calls == [] and not limits._buckets  # 아무도 몫을 쓰지 않았다
    assert client.post("/api/v1/verify", json=body, headers=alice["headers"]).status_code == 200
    assert client.post(f"/api/v1/credentials/{record_id}/reverify", headers=alice["headers"]).status_code == 200
    assert client.post("/api/v1/verify", json=body, headers=alice["headers"]).status_code == 429
    assert len(badge_calls) == 2


def test_identity_verification_uses_the_same_budget(client, register, fake_dx, budget, identity_enabled, monkeypatch):
    from tests.conftest import make_vp_jwt

    install, _ = budget
    install(calls_per_minute=2)
    user = register()
    calls = []
    original = fake_dx.verify_presentation

    async def counting(vp_token):
        calls.append(vp_token)
        return await original(vp_token)

    monkeypatch.setattr(fake_dx, "verify_presentation", counting)
    body = {"type": "identity", "token": make_vp_jwt(user["did"])}
    statuses = [client.post("/api/v1/verify", json=body, headers=user["headers"]).status_code for _ in range(4)]
    assert statuses == [200, 200, 429, 429] and len(calls) == 2


def test_share_view_is_not_charged_to_the_owner_and_owner_calls_not_to_the_share(client, register, budget,
                                                                                 badge_calls):
    install, _ = budget
    user = register()
    record_id, _ = store_badge(client, user)
    other_id, _ = store_badge(client, user)
    share_token = create_share(client, user, [record_id, other_id]).json()["data"]["token"]
    limits = install(calls_per_minute=2, process_calls_per_minute=2)
    badge_calls.clear()
    # 기업용 조회는 public_view_gate의 몫을 쓴다: 로그인 사용자용 한도에서 가져가지 않는다.
    assert client.get(f"/api/v1/public/shares/{share_token}").status_code == 200 and len(badge_calls) == 2
    assert not limits._buckets and limits._process[0] == 2
    # 소유자의 몫이 바닥나도 기업용 조회는 따로 돈다 (60초 재사용이 끝난 뒤에도 자기 한도로 부른다).
    assert client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"]).status_code == 200
    assert client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"]).status_code == 429
    from app.services.public_view_gate import public_view_gate

    public_view_gate.clear()
    assert client.get(f"/api/v1/public/shares/{share_token}").status_code == 200 and len(badge_calls) == 6


# ── 거절된 배지 검증 창: 토큰이 거절된 POST /verify는 호출자마다 10분에 10번 ──────────

@pytest.fixture
def rejected(monkeypatch):
    """거절된 배지 검증 창을 운영과 같은 한도(10분에 10번)에 손으로 움직이는 시계로 바꾼다. (창, 시계)를 돌려준다."""
    clock = {"now": 5000.0}
    window = AttemptWindow(budget_module.REJECTED_VERIFICATIONS, budget_module.REJECTED_VERIFICATION_WINDOW_SECONDS,
                           clock=lambda: clock["now"])
    monkeypatch.setattr(budget_module, "rejected_verifications", window)
    return window, clock


def post_badge(client, user, token: str):
    return client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"])


def junk_token() -> str:
    """compact JWS 모양이라 모양 필터는 통과하지만 검증기가 받아 주지 않을 토큰 (요청마다 다른 값)."""
    return make_raw_jwt('{"junk": "%s"}' % uuid.uuid4().hex)


def test_rejected_badge_verifications_are_capped_per_caller(client, register, fake_dx, budget, rejected, badge_calls,
                                                            caplog):
    # 회귀: 모양만 맞는 쓰레기 토큰을 계정 하나가 분당 120번(호출 몫 그대로) 협력사로 흘려보낼 수 있었다 (계정 6개면 프로세스
    # 몫 전체). 이제 검증기까지 가서 통과하지 못한 배지 검증은 호출자마다 10분에 10번까지다.
    install, _ = budget
    limits = install()
    window, clock = rejected
    user, other = register(), register()
    valid = make_vc_jwt(user["did"])
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        responses = [post_badge(client, user, junk_token()) for _ in range(15)]
    assert [res.status_code for res in responses] == [200] * 10 + [429] * 5
    assert all(res.json()["success"] is False for res in responses[:10])
    assert len(badge_calls) == 10  # 창이 찬 뒤에는 검증기를 부르지 않는다
    refused = responses[-1]
    assert refused.json() == TOO_MANY and refused.headers["retry-after"] == "600"
    assert refused.headers["cache-control"] == "no-store"
    assert limits._buckets[user["id"]][0] == 120 - 10  # 거절된 요청은 호출 몫도 쓰지 않는다
    assert audit_lines(caplog)[-1] == (
        f"event=verifier_budget_denied user={user['id']} route=verify_rejected retry_after=600")
    assert len(audit_lines(caplog)) == 10 + 5
    # 창이 찬 동안에는 그 계정의 배지 검증 자체를 받지 않는다 (정상 토큰도 같은 429). 다른 계정과 다른 API는 그대로다.
    assert post_badge(client, user, valid).status_code == 429 and len(badge_calls) == 10
    assert post_badge(client, other, make_vc_jwt(other["did"])).json()["success"] is True
    assert client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"]).status_code == 200
    # Retry-After는 가장 오래된 거절이 창 밖으로 나갈 때까지다.
    clock["now"] += 599
    assert post_badge(client, user, valid).headers["retry-after"] == "1"
    clock["now"] += 1
    assert post_badge(client, user, valid).json()["success"] is True
    assert window._attempts == {}  # 통과한 요청은 자리를 돌려준다 (10분 전의 거절은 창 밖으로 나갔다)


def test_valid_badge_verifications_never_fill_the_rejected_window(client, register, budget, rejected, badge_calls):
    install, _ = budget
    install()
    window, _ = rejected
    user = register()
    # 보통 사용: 한 계정이 정상 배지 30개를 잇달아 검증·저장한다 (같은 배지를 다시 올리는 것 포함).
    tokens = [make_vc_jwt(user["did"]) for _ in range(25)]
    responses = [post_badge(client, user, token) for token in tokens + tokens[:5]]
    assert [res.status_code for res in responses] == [200] * 30
    assert all(res.json()["success"] is True for res in responses)
    assert len(badge_calls) == 30 and window._attempts == {}


def provider_error(status):
    return ProviderError(f"DXWorks 오류 ({status}): refused", upstream_status=status)


@pytest.mark.parametrize("case,status,kept", [
    ("verifier-says-invalid", 200, True),
    ("issuer-not-confirmed", 200, True),
    ("payload-not-decodable", 200, True),
    ("no-recipient", 200, True),
    ("per-credential-provider-error", 502, True),
    ("issued-to-a-different-did", 403, True),
    ("registered-to-another-user", 409, True),
    ("verified", 200, False),
    ("saved-as-revoked", 200, False),
    ("saved-as-expired", 200, False),
    ("database-busy-after-the-answer", 503, False),
    ("database-error-after-the-answer", 500, False),
    ("verifier-outage-5xx", 502, False),
    ("verifier-outage-no-response", 502, False),
    ("verifier-not-configured", 503, False),
    ("call-budget-429", 429, False),
    ("shape-refused", 200, False),
])
def test_which_badge_verifications_stay_in_the_rejected_window(client, register, fake_dx, budget, rejected,
                                                               monkeypatch, case, status, kept):
    # 남는 것: 검증기까지 가서 그 토큰이 거절된 요청 (저장하지 않는 success: false, 자격증명 하나에 대한 검증기 오류, 다른 DID에
    # 발급된 배지, 다른 사용자가 등록한 배지). 돌려주는 것: 통과한 요청, 저장된 배지(폐기·만료로 확인된 것도 이 사용자의 진짜
    # 배지다), 검증기 장애, 호출 몫 부족(429), 검증기를 부르기 전에 끝난 요청, 검증기가 답한 뒤에 난 우리 쪽 실패(DB가 바쁨,
    # 그 밖의 오류). 모양 필터에 걸린 토큰은 자리를 잡지도 않는다.
    from app.services.database import DatabaseBusy, db_service

    install, _ = budget
    install()
    window, _ = rejected
    user, other = register(), register()
    token = make_vc_jwt(user["did"])
    if case == "verifier-says-invalid":
        fake_dx.set_badge_result(token, valid=False, signature_ok=False)
    elif case == "issuer-not-confirmed":
        fake_dx.set_badge_result(token, valid=False, issuer_trusted=False)
    elif case == "payload-not-decodable":
        token = make_raw_jwt("not json at all")
    elif case == "no-recipient":
        token = make_raw_jwt('{"vc": {"id": "urn:uuid:%s", "issuer": {"name": "x"}, "credentialSubject": {}}}'
                             % uuid.uuid4())
    elif case == "saved-as-revoked":
        fake_dx.set_badge_result(token, valid=False, credential_status="REVOKED")
    elif case == "saved-as-expired":
        token = make_vc_jwt(user["did"], valid_until="2020-01-01T00:00:00Z")
    elif case == "database-busy-after-the-answer":
        def busy(*args, **kwargs):
            raise DatabaseBusy("no free DB connection slot within 5s")

        monkeypatch.setattr(db_service, "find_credential_owner", busy)
    elif case == "database-error-after-the-answer":
        def broken(**kwargs):
            raise RuntimeError("simulated: save failed")

        monkeypatch.setattr(db_service, "save_badge", broken)
    elif case == "per-credential-provider-error":
        fake_dx.error = provider_error(400)
    elif case == "issued-to-a-different-did":
        token = make_vc_jwt(other["did"])
    elif case == "registered-to-another-user":
        credential_id = f"urn:uuid:{uuid.uuid4()}"
        assert post_badge(client, other, make_vc_jwt(other["did"], credential_id=credential_id)).json()["success"]
        token = make_vc_jwt(user["did"], credential_id=credential_id)
    elif case == "verifier-outage-5xx":
        fake_dx.error = provider_error(503)
    elif case == "verifier-outage-no-response":
        fake_dx.error = ProviderError("DXWorks 연결 실패: ConnectError")
    elif case == "verifier-not-configured":
        fake_dx.error = ProviderError("DXWORKS_API_HOST가 설정되지 않았습니다", unavailable=True)
    elif case == "call-budget-429":
        install(calls_per_minute=1).reserve(user["id"], 1)
    elif case == "shape-refused":
        token = "not-a-jwt"
    res = post_badge(client, user, token)
    assert res.status_code == status, res.text
    if status == 200:
        assert res.json()["success"] is (case == "verified")
        assert (res.json()["data"]["record_id"] is not None) is (case in ("verified", "saved-as-revoked",
                                                                          "saved-as-expired"))
    assert len(window._attempts.get(user["id"], ())) == (1 if kept else 0)
    assert other["id"] not in window._attempts  # 다른 사용자의 정상 검증은 남지 않는다


def test_valid_badges_answered_503_while_the_database_is_busy_do_not_lock_verify(client, register, budget, rejected,
                                                                                badge_calls, monkeypatch):
    # 회귀: 검증기가 통과시킨 배지도 저장 단계가 503(DB가 바쁨)으로 끝나면 거절 창에 남았다. DB가 잠깐 바쁜 동안 정상 배지를
    # 열 번 올리면, DB가 돌아온 뒤에도 그 사용자의 /verify가 10분 동안 429였다.
    from app.services.database import DatabaseBusy, db_service

    install, _ = budget
    install()
    window, _ = rejected
    user = register()
    find_owner = db_service.find_credential_owner

    def busy(*args, **kwargs):
        raise DatabaseBusy("no free DB connection slot within 5s")

    monkeypatch.setattr(db_service, "find_credential_owner", busy)
    responses = [post_badge(client, user, make_vc_jwt(user["did"])) for _ in range(12)]
    assert [res.status_code for res in responses] == [503] * 12 and responses[-1].headers["retry-after"] == "5"
    assert len(badge_calls) == 12 and window._attempts == {}
    monkeypatch.setattr(db_service, "find_credential_owner", find_owner)
    res = post_badge(client, user, make_vc_jwt(user["did"]))  # DB가 돌아오면 13번째는 그대로 저장된다
    assert res.status_code == 200 and res.json()["success"] is True


def test_confirmed_revoked_and_expired_badges_saved_in_a_row_are_not_refused(client, register, fake_dx, budget,
                                                                             rejected, badge_calls):
    # 회귀: 폐기·만료로 확인돼 저장된 배지도 거절 창에 남아, 그런 배지를 10분에 10개 넘게 가져오면 11번째부터 429였다.
    install, _ = budget
    install()
    window, _ = rejected
    user = register()
    tokens = [make_vc_jwt(user["did"]) for _ in range(6)]
    for token in tokens:
        fake_dx.set_badge_result(token, valid=False, credential_status="REVOKED")
    tokens += [make_vc_jwt(user["did"], valid_until="2020-01-01T00:00:00Z") for _ in range(6)]
    responses = [post_badge(client, user, token) for token in tokens]
    assert [res.status_code for res in responses] == [200] * 12
    assert [res.json()["data"]["status"] for res in responses] == ["revoked"] * 6 + ["expired"] * 6
    assert all(res.json()["data"]["record_id"] for res in responses)  # 모두 저장됐다
    assert len(badge_calls) == 12 and window._attempts == {}


def test_rejections_of_the_token_still_fill_the_window_after_ten(client, register, fake_dx, budget, rejected,
                                                                 badge_calls):
    # 남는 경우들을 섞어 보내도 10번까지만 검증기에 닿는다: 검증기의 거절, 발급자 미확인, 자격증명 하나에 대한 검증기 오류,
    # 다른 DID에 발급된 배지, 다른 사용자가 등록한 배지.
    install, _ = budget
    install()
    window, _ = rejected
    user, other = register(), register()
    taken = f"urn:uuid:{uuid.uuid4()}"
    assert post_badge(client, other, make_vc_jwt(other["did"], credential_id=taken)).json()["success"]
    badge_calls.clear()
    statuses = []
    for _ in range(3):
        invalid, untrusted, refused = (make_vc_jwt(user["did"]) for _ in range(3))
        fake_dx.set_badge_result(invalid, valid=False, signature_ok=False)
        fake_dx.set_badge_result(untrusted, valid=False, issuer_trusted=False)
        statuses += [post_badge(client, user, token).status_code for token in (invalid, untrusted)]
        fake_dx.error = provider_error(400)
        statuses.append(post_badge(client, user, refused).status_code)
        fake_dx.error = None
        statuses.append(post_badge(client, user, make_vc_jwt(other["did"])).status_code)
        statuses.append(post_badge(client, user, make_vc_jwt(user["did"], credential_id=taken)).status_code)
    assert statuses == [200, 200, 502, 403, 409] * 2 + [429] * 5
    assert len(badge_calls) == 10 and len(window._attempts[user["id"]]) == 10
    assert post_badge(client, user, make_vc_jwt(user["did"])).status_code == 429  # 창이 찬 동안은 정상 토큰도 같다


async def test_badge_verification_cancelled_while_it_is_being_saved_leaves_the_window(rejected, monkeypatch):
    # 검증기가 답한 뒤 DB 단계에서 취소된 요청은 자리를 돌려준다 (검증기는 이 배지를 받아들였다: 토큰의 거절이 아니다).
    import threading

    from app.api.verify import _verify_badge
    from app.core.auth import Principal
    from app.services.database import db_service
    from tests.conftest import FakeProvider

    window, _ = rejected
    principal = Principal(kind="user", user_id=str(uuid.uuid4()), did="did:key:z6MkCancelledWhileSaving")
    in_db, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        in_db.set()
        release.wait(5)  # 테스트가 풀어 줄 때까지 (길어도 5초: 테스트가 멈추지 않는다)
        return None

    monkeypatch.setattr(db_service, "find_credential_owner", held)
    task = asyncio.ensure_future(_verify_badge(make_vc_jwt(principal.did), principal, FakeProvider()))
    for _ in range(500):
        if in_db.is_set():
            break
        await asyncio.sleep(0.01)
    assert in_db.is_set() and len(window._attempts[principal.user_id]) == 1  # 진행 중인 요청은 자리를 잡고 있다
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    release.set()
    assert window._attempts == {}


def test_parallel_junk_tokens_cannot_pass_the_rejected_window_together(client, register, fake_dx, budget, rejected,
                                                                       monkeypatch):
    # 부르기 전에 자리를 잡으므로, 검증기 응답이 오기 전에 한꺼번에 보낸 요청도 10번까지만 검증기에 닿는다.
    from main import app

    install, _ = budget
    install()
    window, _ = rejected
    user = register()
    calls = []

    async def slow_reject(vc_jwt):
        calls.append(vc_jwt)
        await asyncio.sleep(0.2)  # 40개가 모두 겹치도록
        return _rejected_result()

    monkeypatch.setattr(fake_dx, "verify_badge", slow_reject)

    async def burst():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
            return await asyncio.gather(*(
                ac.post("/api/v1/verify", json={"type": "badge", "token": junk_token()}, headers=user["headers"])
                for _ in range(40)))

    statuses = sorted(res.status_code for res in asyncio.run(burst()))
    assert statuses == [200] * 10 + [429] * 30 and len(calls) == 10
    assert len(window._attempts[user["id"]]) == 10


def _rejected_result():
    from app.domain.credential import ProviderBadgeResult

    return ProviderBadgeResult(valid=False, signature_ok=False, issuer_trusted=None, credential_status=None,
                               recipient_did=None, credential_id=None, issuer_name=None, achievement_name=None,
                               issued_at=None)


def test_more_than_ten_badge_verifications_in_flight_from_one_caller_are_429(client, register, fake_dx, budget,
                                                                              rejected, monkeypatch):
    # 자리는 부르기 전에 잡고 통과한 뒤에 돌려주므로, 한 계정이 동시에 진행 중인 배지 검증은 10개까지다 (정상 토큰이어도
    # 11번째부터는 429). 화면은 배지를 하나씩 검증하므로 보통 사용에는 닿지 않는다. 끝난 뒤에는 창에 아무것도 남지 않는다.
    from main import app

    install, _ = budget
    install()
    window, _ = rejected
    user = register()
    verify_badge = fake_dx.verify_badge

    async def slow_verify(vc_jwt):
        await asyncio.sleep(0.2)
        return await verify_badge(vc_jwt)

    monkeypatch.setattr(fake_dx, "verify_badge", slow_verify)

    async def burst():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
            return await asyncio.gather(*(
                ac.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"])},
                        headers=user["headers"]) for _ in range(14)))

    statuses = sorted(res.status_code for res in asyncio.run(burst()))
    assert statuses == [200] * 10 + [429] * 4
    assert window._attempts == {}
    assert post_badge(client, user, make_vc_jwt(user["did"])).json()["success"] is True


async def test_badge_verification_cancelled_while_the_verifier_is_asked_stays_in_the_window(rejected):
    # 검증기를 부른 뒤에 취소된 요청(연결 종료 등)은 자리를 돌려주지 않는다: 요청을 끊는 것으로 창을 피해 가지 못한다.
    from app.api.verify import _verify_badge
    from app.core.auth import Principal

    window, _ = rejected
    principal = Principal(kind="user", user_id=str(uuid.uuid4()), did="did:key:z6MkCancelled")
    started = asyncio.Event()

    class Stuck:
        provider_name = "stuck"

        async def verify_badge(self, vc_jwt):
            started.set()
            await asyncio.sleep(3600)

    task = asyncio.ensure_future(_verify_badge(make_vc_jwt(principal.did), principal, Stuck()))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    assert len(window._attempts[principal.user_id]) == 1


def test_every_verifier_call_site_is_behind_a_budget():
    """앱 코드에서 검증기 포트를 직접 부르는 곳과 reverify_rows를 부르는 곳의 목록을 고정한다.

    새 호출 지점을 만들면 이 테스트가 실패한다: 호출 전에 몫을 가져가게(verifier_budget 또는 public_view_gate) 만든 뒤 여기에 더한다.
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    direct, fan_out = {}, {}
    for path in sorted(app_dir.rglob("*.py")):
        text, name = path.read_text(encoding="utf-8"), path.relative_to(app_dir).as_posix()
        if n := len(re.findall(r"\bverifier\.verify_(?:badge|presentation)\(", text)):
            direct[name] = n
        if n := len(re.findall(r"await reverify_rows\(", text)):
            fan_out[name] = n
        # 포트 이름이 아닌 다른 변수로 부르는 곳이 생겨도 잡는다 (어댑터 정의·포트 선언 제외).
        if name not in ("integrations/dxworks.py", "integrations/ports.py"):
            assert len(re.findall(r"\.verify_(?:badge|presentation)\(", text)) == direct.get(name, 0), name
    # api/verify.py: _ask_verifier가 몫을 가져간 뒤 부른다. services/reverify.py: reverify_rows가 몫(또는 게이트)을 거친 뒤 부른다.
    assert direct == {"api/verify.py": 2, "services/reverify.py": 1}
    # reverify_rows는 caller·route가 필수 인자다 (게이트가 없으면 verifier_budget에서 몫을 가져간다).
    assert fan_out == {"api/portfolios.py": 1, "api/reverify.py": 2, "api/shares.py": 1, "services/career.py": 1}


async def test_reverify_rows_requires_a_caller_and_a_route(fake_dx):
    from app.services.reverify import reverify_rows

    with pytest.raises(TypeError):
        await reverify_rows([], fake_dx)


# ── 감사 로그 ─────────────────────────────────────────────────────────

def test_audit_line_for_every_request_that_calls_the_verifier(client, register, fake_dx, budget, caplog):
    install, _ = budget
    user = register()
    record_ids, tokens = zip(*(store_badge(client, user) for _ in range(3)))
    share_token = create_share(client, user, list(record_ids)).json()["data"]["token"]
    install(calls_per_minute=12)
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        caplog.clear()
        client.post("/api/v1/verify", json={"type": "badge", "token": tokens[0]}, headers=user["headers"])
        client.post(f"/api/v1/credentials/{record_ids[0]}/reverify", headers=user["headers"])
        client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"])
        client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=SERVICE)
        create_portfolio(client, user, list(record_ids[:2]))
        client.get(f"/api/v1/public/shares/{share_token}")
        client.get(f"/api/v1/public/shares/{share_token}")  # 60초 안: 검증기를 부르지 않으므로 줄도 없다
        client.post("/api/v1/verify", json={"type": "badge", "token": "not-a-jwt"}, headers=user["headers"])
        fake_dx.error = ProviderError("DXWorks 오류 (500): boom", upstream_status=500)
        client.post("/api/v1/verify", json={"type": "badge", "token": tokens[0]}, headers=user["headers"])
        client.post(f"/api/v1/credentials/{record_ids[0]}/reverify", headers=user["headers"])
        client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"])
        fake_dx.error = None
        client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"])  # 몫(12)을 다 썼다
    uid = user["id"]
    assert audit_lines(caplog) == [
        f"event=verifier_calls user={uid} route=verify_badge calls=1 failed=0",
        f"event=verifier_calls user={uid} route=reverify calls=1 failed=0",
        f"event=verifier_calls user={uid} route=evidence calls=3 failed=0",
        "event=verifier_calls user=service route=evidence calls=3 failed=0",
        f"event=verifier_calls user={uid} route=portfolio_create calls=2 failed=0",
        "event=verifier_calls user=anonymous route=share_view calls=3 failed=0",
        # 모양 필터에 걸린 배지 토큰: 검증기를 부르지 않았다는 흔적 한 줄 (토큰·길이 없음)
        f"event=verify_shape_refused user={uid}",
        f"event=verifier_calls user={uid} route=verify_badge calls=1 failed=1",
        f"event=verifier_calls user={uid} route=reverify calls=1 failed=1",
        # 장애가 확인되면 남은 자격증명은 부르지 않는다: 몫은 3을 가져갔지만 실제로 부른 것은 1번이다.
        f"event=verifier_calls user={uid} route=evidence calls=1 failed=1",
        f"event=verifier_budget_denied user={uid} route=evidence retry_after=15",
    ]
    records = [r for r in caplog.records if r.name == "vc-be.audit"]
    assert all(r.levelno == logging.INFO for r in records)
    text = "\n".join(audit_lines(caplog))
    # 한 줄씩이고, 이메일·DID·서명 원본·공유 링크 토큰은 남기지 않는다.
    assert not any("\n" in line for line in audit_lines(caplog))
    assert not any(secret in text for secret in (user["email"], user["did"], share_token, *tokens))
