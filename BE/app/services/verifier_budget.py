"""로그인 사용자·서비스 키 요청이 협력사(DXWorks)를 부르는 양 제한.

배지 검증(/verify)·재검증·근거 조회·포트폴리오 생성·직무 비교는 요청마다 검증기를 부른다(자격증명 수만큼). 가입은 누구나
할 수 있으므로, 한도가 없으면 계정 하나로 협력사 API에 공격 수준의 트래픽을 우리 이름(IP·API 키)으로 보낼 수 있다
(협력사가 우리를 막으면 모든 사용자의 검증·포트폴리오·직무 비교가 502가 된다).
- 호출하기 전에 필요한 몫을 한꺼번에 가져간다(토큰 버킷). 호출자의 분 버킷·시간 버킷(사용자 id, 서비스 키 주체는 "service"
  하나)과 프로세스 전체 버킷을 함께 본다. 어느 하나라도 모자라면 아무것도 가져가지 않고 VerifierBudgetExceeded를 던진다
  (main.py가 429 + Retry-After로 답한다. 검증기는 한 번도 부르지 않는다). Retry-After는 모자란 버킷들이 다시 찰 때까지의
  시간 가운데 가장 긴 값이다 (그만큼 기다린 뒤 같은 요청을 다시 보내면 받는다. 그 사이 다른 요청이 몫을 쓰지 않았다면).
- 호출자마다 분당 CALLS_PER_MINUTE번, 프로세스 전체로 분당 PROCESS_CALLS_PER_MINUTE번 (버킷 크기도 같은 값).
- 호출자마다 시간당 CALLS_PER_HOUR번 (버킷 크기도 같은 값). 계정 하나가 분당 한도를 끝없이 채워 쓰지 못하게 한다: 시간 몫을
  다 쓴 계정은 분당 CALLS_PER_HOUR / 60번(20번)만 부를 수 있으므로, 프로세스 전체 몫(분당 600번)을 계속 비워 두려면 계정이
  6개가 아니라 30개쯤 필요하다. 자격증명 50개인 사용자가 한 번에 쓰는 양(포트폴리오 생성·직무 비교·근거 조회·모아서 재검증,
  각각 50번)은 200번이라 보통 사용에는 닿지 않는다.
- 한 요청이 버킷 크기보다 많이 필요해도(자격증명이 아주 많은 사용자의 근거 조회·직무 비교) 영영 막히지는 않는다: 버킷에
  min(필요한 몫, 버킷 크기)가 있으면 받아 주고 잔액을 음수로 둔다. 음수가 다시 찰 때까지 다음 요청이 기다리므로 평균 호출 수는
  그대로 묶인다.
- 프로세스 메모리에만 둔다(프로세스마다 따로 세고 재시작하면 지워짐: 로그인 실패 제한과 같은 방식). 호출자 키는 MAX_KEYS개까지
  두고 넘으면 가장 오래 쓰지 않은 것부터 버린다. 키는 몫을 실제로 가져갈 때만 생기므로 키가 느는 속도도 프로세스 한도에 묶인다.
- 클라이언트 IP로 나누지 않는다 (public_view_gate.py와 같은 이유). IP별 제한은 운영 nginx(deploy/nginx/http.conf)가 맡는다.
- 로그인 없는 기업용 조회(공유 링크)는 이 한도가 아니라 public_view_gate의 한도를 쓴다 (두 번 세지 않는다).
- 검증기를 부른 요청마다, 그리고 한도로 거절한 요청마다 감사 로그 한 줄을 남긴다 (로거 vc-be.audit, INFO. 이메일·DID·토큰 없음).

시도 창(AttemptWindow)을 쓰는 한도도 여기에 둔다.
- 거절된 배지 검증(rejected_verifications): POST /verify가 검증기까지 가서 그 토큰이 거절된 요청(자격증명 하나에 대한
  검증기 오류, 저장하지 않는 success: false, 다른 DID에 발급된 배지, 다른 사용자가 등록한 배지)과 검증기에 묻는 동안 끊긴
  요청을 호출자마다 REJECTED_VERIFICATION_WINDOW_SECONDS 동안 REJECTED_VERIFICATIONS번까지만 받는다 (모양만 맞는 쓰레기
  토큰을 분당 한도만큼 협력사로 흘려보내지 못하게). 부르기 전에 자리를 잡고 나중에 돌려주므로 동시에 몰아 보내도 넘지 못한다.
  통과한 요청, 저장된 배지(폐기·만료 포함), 검증기 장애, 검증기가 답한 뒤의 우리 쪽 실패(DB가 바쁨 등)는 자리를 돌려준다.
- 모의 발급의 시도 한도(mock_issue_attempts: 사용자별): 성공 건수만 세는 DB 한도와 달리 실패한 발급까지 센다.
- LLM 모드 학습 계획의 시도 한도(llm_curriculum_attempts: 사용자별 24시간, 한도는 CURRICULUM_LLM_DAILY_LIMIT):
  외부 LLM(Claude)을 불렀을 수 있는 시도를 실패까지 센다 (저장된 LLM 계획만 세는 DB 한도와 함께 본다: services/career.py).
  부르기 전에 자리를 잡으므로 동시에 몰아 보내도 넘지 못한다. 모델을 부르지 않고 끝난 요청은 자리를 돌려준다.
  모든 계정을 합친 같은 창(llm_curriculum_global_attempts, 한도는 CURRICULUM_LLM_GLOBAL_DAILY_LIMIT)도 함께 잡는다.

실패 차단기(FailureBreaker)도 여기에 둔다.
- 모의 발급의 실패 차단기(mock_issue_breaker: 태스크 전체에 하나): 협력사까지 간 실패가 24시간에
  MOCK_ISSUER_GLOBAL_DAILY_LIMIT번 쌓이면 발급 요청을 멈추고, MOCK_ISSUE_PROBE_INTERVAL_SECONDS마다 요청 하나만 협력사로 보내
  본다(탐침). 탐침이 성공하면 실패 기록을 모두 지워 바로 다시 연다 (재시작이나 24시간을 기다리지 않는다).
  진행 중인 요청도 자리를 잡으므로, 실패한 것 없이 진행 중인 요청만으로 창이 찼을 때는 멈춘 것이 아니다: 탐침 없이 거절하고
  api/mock_issuer.py가 '멈춤' 대신 전체 한도 문구로 답한다.
"""
import logging
import math
import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Optional

CALLS_PER_MINUTE = 120
CALLS_PER_HOUR = 1200
PROCESS_CALLS_PER_MINUTE = 600
MAX_KEYS = 10_000
# 배지 검증(POST /verify): 검증기까지 가서 그 토큰이 거절된 요청을 호출자마다 10분에 10번까지 (남기는 경우는 api/verify.py)
REJECTED_VERIFICATIONS = 10
REJECTED_VERIFICATION_WINDOW_SECONDS = 10 * 60
# 모의 발급: 사용자별 24시간 시도 수 (협력사까지 간 요청은 실패해도 센다). 실제 한도는 요청마다
# max(MOCK_ISSUE_ATTEMPTS, 3 × MOCK_ISSUER_DAILY_LIMIT)로 정한다 (api/mock_issuer.py: 발급 한도를 올린 설정을 가리지 않게).
MOCK_ISSUE_ATTEMPTS = 30
MOCK_ISSUE_WINDOW_SECONDS = 24 * 60 * 60
# 모의 발급: 태스크 전체의 실패 차단기(mock_issue_breaker)는 실패를 위와 같은 24시간 동안 센다 (한도는 요청마다
# MOCK_ISSUER_GLOBAL_DAILY_LIMIT에서 읽는다). 한도에 닿은 뒤에는 이 간격(초)마다 요청 하나만 협력사로 보내 본다(탐침).
MOCK_ISSUE_PROBE_INTERVAL_SECONDS = 60
# LLM 모드 학습 계획: 사용자별 24시간 시도 수 (한도는 요청마다 CURRICULUM_LLM_DAILY_LIMIT에서 읽는다)
LLM_CURRICULUM_WINDOW_SECONDS = 24 * 60 * 60

SERVICE_CALLER = "service"  # 서비스 키 주체 (X-Service-Key)는 사용자 id가 없어 한 키를 함께 쓴다
ANONYMOUS_CALLER = "anonymous"  # 기업용 조회 (감사 로그 표기용. 한도는 public_view_gate가 맡는다)
TOO_MANY_VERIFICATIONS = "Too many verification requests. Try again later."

audit = logging.getLogger("vc-be.audit")


class VerifierBudgetExceeded(Exception):
    """검증기 호출 몫이 모자람. retry_after: 몫이 다시 찰 때까지의 초 (Retry-After 헤더 값)."""

    def __init__(self, retry_after: int):
        super().__init__(f"verifier call budget exceeded (retry after {retry_after}s)")
        self.retry_after = retry_after


class VerifierBudget:
    def __init__(self, calls_per_minute: int = CALLS_PER_MINUTE,
                 process_calls_per_minute: int = PROCESS_CALLS_PER_MINUTE, max_keys: int = MAX_KEYS,
                 clock: Callable[[], float] = time.monotonic, calls_per_hour: int = CALLS_PER_HOUR):
        self.capacity = float(calls_per_minute)
        self.rate = calls_per_minute / 60.0  # 초당 다시 채우는 몫
        self.hour_capacity = float(calls_per_hour)
        self.hour_rate = calls_per_hour / 3600.0
        self.process_capacity = float(process_calls_per_minute)
        self.process_rate = process_calls_per_minute / 60.0
        self.max_keys = max_keys
        self._clock = clock
        # 이벤트 루프 안에서만 부르지만(await 사이에 잡고 있지 않음) 스레드에서 불려도 안전하게 잠근다.
        self._lock = threading.Lock()
        # 호출자 키 → [분 버킷 잔액, 마지막으로 채운 시각(clock), 시간 버킷 잔액]. 맨 뒤가 가장 최근에 몫을 가져간 키.
        self._buckets: "OrderedDict[str, list[float]]" = OrderedDict()
        self._process = [self.process_capacity, clock()]

    @staticmethod
    def _refill(bucket: list[float], capacity: float, rate: float, now: float) -> None:
        bucket[0] = min(capacity, bucket[0] + (now - bucket[1]) * rate)
        bucket[1] = now

    @staticmethod
    def _shortfall(balance: float, capacity: float, rate: float, calls: int) -> int:
        """몫이 충분하면 0, 모자라면 다시 시도할 수 있을 때까지의 초."""
        needed = min(calls, capacity)  # 버킷 크기보다 큰 요청은 가득 찬 버킷이면 받는다 (잔액은 음수가 된다)
        if balance >= needed:
            return 0
        return max(1, math.ceil((needed - balance) / rate))

    def reserve(self, key: str, calls: int) -> None:
        """key(사용자 id 또는 "service")가 검증기를 calls번 부를 몫을 가져간다.

        호출자의 분 버킷·시간 버킷과 프로세스 전체 버킷에 모두 몫이 있어야 한다. 하나라도 모자라면 하나도 가져가지 않고
        VerifierBudgetExceeded(retry_after: 모자란 버킷들의 대기 시간 가운데 가장 긴 값)를 던진다.
        """
        if calls <= 0:
            return
        with self._lock:
            now = self._clock()
            own = self._buckets.get(key) or [self.capacity, now, self.hour_capacity]
            own[2] = min(self.hour_capacity, own[2] + (now - own[1]) * self.hour_rate)  # 분 버킷과 같은 시각으로 채운다
            self._refill(own, self.capacity, self.rate, now)
            self._refill(self._process, self.process_capacity, self.process_rate, now)
            retry_after = max(
                self._shortfall(own[0], self.capacity, self.rate, calls),
                self._shortfall(own[2], self.hour_capacity, self.hour_rate, calls),
                self._shortfall(self._process[0], self.process_capacity, self.process_rate, calls),
            )
            if retry_after:
                raise VerifierBudgetExceeded(retry_after)
            own[0] -= calls
            own[2] -= calls
            self._process[0] -= calls
            self._buckets[key] = own
            self._buckets.move_to_end(key)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)  # 가장 오래 쓰지 않은 키 (대개 이미 다시 가득 찬 버킷)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._process = [self.process_capacity, self._clock()]


class AttemptWindow:
    """키마다 window_seconds 동안 max_attempts번까지만 받는 시도 한도 (프로세스 메모리, 키는 max_keys개까지)."""

    def __init__(self, max_attempts: int, window_seconds: float, max_keys: int = MAX_KEYS,
                 clock: Callable[[], float] = time.monotonic):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: "OrderedDict[str, deque[float]]" = OrderedDict()

    def reserve(self, key: str, max_attempts: Optional[int] = None) -> bool:
        """시도 하나를 기록하고 True를 돌려준다. 이미 한도면 기록하지 않고 False.

        max_attempts를 주면 이 호출에만 그 한도를 쓴다 (설정에서 요청마다 읽는 한도용).
        """
        now = self._clock()
        with self._lock:
            stamps = self._attempts.pop(key, None) or deque()
            while stamps and stamps[0] <= now - self.window_seconds:
                stamps.popleft()
            if len(stamps) >= (self.max_attempts if max_attempts is None else max_attempts):
                self._attempts[key] = stamps
                return False
            stamps.append(now)
            self._attempts[key] = stamps  # 가장 최근 사용으로 맨 뒤에 둔다
            while len(self._attempts) > self.max_keys:
                self._attempts.popitem(last=False)
            return True

    def release(self, key: str) -> None:
        """방금 기록한 시도 하나를 돌려준다 (협력사를 부르지 않고 끝난 요청은 시도로 세지 않는다)."""
        with self._lock:
            stamps = self._attempts.get(key)
            if stamps:
                stamps.pop()
                if not stamps:
                    del self._attempts[key]

    def retry_after(self, key: str) -> int:
        """가장 오래된 시도가 창 밖으로 나갈 때까지의 초 (Retry-After 헤더 값, 최소 1). 기록이 없으면 1."""
        now = self._clock()
        with self._lock:
            stamps = self._attempts.get(key)
            oldest = stamps[0] if stamps else now - self.window_seconds
        return max(1, math.ceil(oldest + self.window_seconds - now))

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()


class FailureBreaker:
    """실패가 한도만큼 쌓이면 요청을 멈추고, 간격마다 하나씩만 보내 보다가(탐침) 성공하면 다시 여는 차단기 (프로세스 메모리).

    - enter(limit): 최근 window_seconds 동안의 실패(진행 중인 요청 포함)가 limit보다 적으면 자리 하나를 기록하고 보통 요청
      표("counted")를 돌려준다. 창이 찼고 그 안에 실패로 끝난 요청이 하나라도 있으면(멈춘 상태), 최근
      probe_interval_seconds 동안 들여보낸 탐침이 없을 때만 이 요청을 탐침("probe")으로 들여보낸다. 그 밖에는 None
      (거절: 아무것도 보내지 않는다). 창이 진행 중인 요청의 자리만으로 찼으면 아무것도 실패하지 않았으므로 탐침도 없이
      거절한다 (남은 몫을 그 요청들이 잡고 있다). admit(limit)은 같은 결정에 멈춘 상태인지(paused)를 붙여 돌려준다
      (호출한 쪽이 거절 문구를 고른다: api/mock_issuer.py).
    - leave(ticket, outcome): outcome은 "success"(협력사가 처리했다), "failure"(협력사까지 갔거나 결과를 모르는 실패),
      "not_sent"(협력사를 부르지 않고 끝났다) 가운데 하나다.
      보통 요청: success·not_sent는 자리를 돌려주고 failure는 자리를 남긴 채 실패로 끝났다고 표시한다.
      탐침: success는 실패 기록을 모두 지운다 (협력사가 다시 된다: 다음 요청부터 보통 요청이다. 그때 진행 중이던 보통
      요청의 자리도 함께 지워진다). failure는 아무것도 바꾸지 않는다 (다음 탐침은 한 간격 뒤). not_sent는 탐침을 돌려준다
      (탐침 시각을 직전 값으로 되돌려, 협력사를 부르지 않고 끝난 요청이 탐침 기회를 쓰지 않게 한다).
    그래서 협력사가 계속 실패하는 동안 프로세스 하나가 보내는 요청은 window_seconds마다 limit번, 그 뒤로는
    probe_interval_seconds마다 한 번이다. 협력사가 돌아오면 처음 들여보낸 탐침이 성공하면서 모두에게 다시 열린다.
    탐침만 성공하고 나머지는 실패하는 협력사(우리를 분당 한두 번으로 제한하는 경우 등)에서는 탐침이 성공할 때마다 다시
    limit번을 받는다: 그때의 양은 이 차단기가 아니라 부르는 쪽의 다른 한도(모의 발급은 사용자별 시도 한도와 DB 발급 한도)가 묶는다.
    """

    def __init__(self, window_seconds: float, probe_interval_seconds: float,
                 clock: Callable[[], float] = time.monotonic):
        self.window_seconds = window_seconds
        self.probe_interval_seconds = probe_interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # (기록한 시각, 일련번호). 들어온 순서가 곧 시각 순서다. 일련번호는 같은 시각의 자리를 서로 구분한다.
        self._failures: "deque[tuple[float, int]]" = deque()
        self._finished: "set[tuple[float, int]]" = set()  # _failures 가운데 실패로 끝난 요청의 자리 (나머지는 진행 중)
        self._serial = 0
        self._last_probe: Optional[float] = None  # 마지막으로 탐침을 들여보낸 시각

    def enter(self, limit: int) -> Optional[tuple]:
        """요청 하나를 들여보낼지 정한다. 표(leave에 그대로 넘긴다) 또는 None(거절)."""
        return self.admit(limit)[0]

    def admit(self, limit: int) -> tuple[Optional[tuple], bool]:
        """enter와 같고, 멈춘 상태인지(paused)도 같은 잠금 안에서 함께 돌려준다: (표 또는 None, paused).

        paused는 창이 찼고 그 안에 실패로 끝난 요청이 하나라도 있는지다 (탐침 표·탐침 차례가 아니어서 거절한 경우에 True).
        창이 진행 중인 요청의 자리만으로 찼으면(아무것도 실패하지 않았다) 탐침도 들여보내지 않고 (None, False)다.
        """
        now = self._clock()
        with self._lock:
            while self._failures and self._failures[0][0] <= now - self.window_seconds:
                self._finished.discard(self._failures.popleft())
            if len(self._failures) < limit:
                self._serial += 1
                stamp = (now, self._serial)
                self._failures.append(stamp)
                return ("counted", stamp, None), False
            if not self._finished:
                return None, False
            if self._last_probe is None or now - self._last_probe >= self.probe_interval_seconds:
                ticket = ("probe", now, self._last_probe)
                self._last_probe = now
                return ticket, True
            return None, True

    def leave(self, ticket: tuple, outcome: str) -> None:
        """enter가 돌려준 표의 요청이 끝났다. outcome: "success" | "failure" | "not_sent"."""
        kind, stamp, previous_probe = ticket
        with self._lock:
            if kind == "counted":
                if stamp in self._failures:  # 없으면 성공한 탐침이 이미 지웠거나 창 밖으로 나갔다
                    if outcome == "failure":
                        self._finished.add(stamp)  # 자리는 남기고 실패로 끝났다고 표시한다
                    else:
                        self._failures.remove(stamp)
            elif outcome == "success":
                self._failures.clear()
                self._finished.clear()
            elif outcome == "not_sent" and self._last_probe == stamp:  # 그 사이 다른 탐침이 나갔으면 그대로 둔다
                self._last_probe = previous_probe

    def retry_after(self) -> int:
        """다음 탐침을 들여보낼 수 있을 때까지의 초 (Retry-After 헤더 값, 최소 1)."""
        now = self._clock()
        with self._lock:
            last_probe = self._last_probe
        if last_probe is None:
            return 1
        return max(1, math.ceil(last_probe + self.probe_interval_seconds - now))

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()
            self._finished.clear()
            self._last_probe = None


# 프로세스(태스크) 하나에 하나씩.
verifier_budget = VerifierBudget()
rejected_verifications = AttemptWindow(REJECTED_VERIFICATIONS, REJECTED_VERIFICATION_WINDOW_SECONDS)
mock_issue_attempts = AttemptWindow(MOCK_ISSUE_ATTEMPTS, MOCK_ISSUE_WINDOW_SECONDS)
mock_issue_breaker = FailureBreaker(MOCK_ISSUE_WINDOW_SECONDS, MOCK_ISSUE_PROBE_INTERVAL_SECONDS)
# max_attempts는 reserve 때마다 설정값(CURRICULUM_LLM_DAILY_LIMIT)으로 넘긴다. 여기 값은 쓰지 않는 자리표시다.
llm_curriculum_attempts = AttemptWindow(1, LLM_CURRICULUM_WINDOW_SECONDS)
# 모든 계정을 합친 LLM 시도 창 (키는 LLM_GLOBAL_KEY 하나, 한도는 reserve 때마다 CURRICULUM_LLM_GLOBAL_DAILY_LIMIT).
LLM_GLOBAL_KEY = "all"
llm_curriculum_global_attempts = AttemptWindow(1, LLM_CURRICULUM_WINDOW_SECONDS)


def caller_key(principal) -> str:
    """한도와 감사 로그에 쓰는 호출자 키: 로그인 사용자는 사용자 id, 서비스 키 주체는 "service"."""
    return principal.user_id or SERVICE_CALLER


def reserve_verifier_calls(caller: str, route: str, calls: int) -> None:
    """검증기를 calls번 부르기 전에 몫을 가져간다.

    모자라면 감사 로그 한 줄을 남기고 VerifierBudgetExceeded를 그대로 던진다 (호출한 API는 검증기를 부르지 않고 끝난다).
    """
    try:
        verifier_budget.reserve(caller, calls)
    except VerifierBudgetExceeded as exc:
        audit.info("event=verifier_budget_denied user=%s route=%s retry_after=%d", caller, route, exc.retry_after)
        raise


def reserve_rejected_verification(caller: str) -> None:
    """배지 검증(POST /verify)이 검증기를 부르기 전에 거절 창의 자리 하나를 잡는다.

    창이 이미 찼으면(최근 10분 동안 거절된 검증 10번) 감사 로그 한 줄을 남기고 VerifierBudgetExceeded를 던진다
    (호출 몫은 가져가지 않고 검증기도 부르지 않는다). 잡은 자리는 api/verify.py가 토큰이 거절된 요청이 아니면 돌려준다.
    """
    if not rejected_verifications.reserve(caller):
        retry_after = rejected_verifications.retry_after(caller)
        audit.info("event=verifier_budget_denied user=%s route=verify_rejected retry_after=%d", caller, retry_after)
        raise VerifierBudgetExceeded(retry_after)


def release_rejected_verification(caller: str) -> None:
    """reserve_rejected_verification으로 잡은 자리를 돌려준다 (통과한 요청, 저장된 배지, 검증기까지 가지 않았거나 장애로
    끝난 요청, 검증기가 답한 뒤 우리 쪽 실패로 끝난 요청)."""
    rejected_verifications.release(caller)


def log_verifier_calls(caller: str, route: str, calls: int, failed: int) -> None:
    """검증기를 부른 요청마다 감사 로그 한 줄 (calls: 실제로 부른 수, failed: 그중 실패한 수). 부르지 않았으면 남기지 않는다."""
    if calls > 0:
        audit.info("event=verifier_calls user=%s route=%s calls=%d failed=%d", caller, route, calls, failed)
