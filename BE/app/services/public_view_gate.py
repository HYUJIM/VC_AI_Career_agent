"""기업용 조회(로그인 없는 공유 링크)가 검증기(DXWorks)를 부르는 양 제한.

공유 링크를 열 때마다 자격증명(최대 50개)마다 검증기를 부르면, 로그인 없이 링크를 반복해 여는 것만으로 협력사 API를
우리 이름으로 대량 호출하게 된다(검증기가 우리를 막으면 직무 비교·포트폴리오 생성까지 502/503).
- 같은 서명 원본은 RESULT_TTL_SECONDS 동안 검증기에 다시 묻지 않고 그때 받은 응답을 다시 쓴다. 성공한 응답만 담고
  (검증기 오류는 담지 않음), 만료 판정은 매번 지금 시각으로 다시 한다.
  근거의 verified_at은 실제로 검증기에 물은 시각이다.
- 새로 부르는 검증기 호출은 분당 CALLS_PER_MINUTE번까지(토큰 버킷). 모자라면 아무것도 부르지 않고 BudgetExceeded.
  공유 링크 하나는 자격증명 50개까지라 한 번 여는 데 필요한 몫(≤50)은 버킷 크기보다 작다.
- 같은 서명 원본을 다른 조회가 지금 검증기에 묻고 있으면(아직 응답 전) 다시 부르지 않고 그 응답을 함께 기다린다(begin).
  몫은 담아 둔 응답도 없고 묻고 있는 조회도 없는 원본만큼만 가져간다. 그래서 같은 링크를 동시에 여러 번 열어도 검증기 호출은
  원본마다 한 번이고, 그 링크 하나가 태스크의 몫을 다 써서 다른 링크가 503이 되지 않는다.
  먼저 묻던 조회가 실패하거나 취소되면 기다리던 조회는 그 실패(검증기 오류)로 끝난다 (끝없이 기다리지 않는다).
- 프로세스 메모리에만 둔다(태스크마다 따로 세고 재시작하면 지워짐). 키는 서명 원본의 SHA-256이다. 새로 담으려면 먼저
  호출 몫을 가져가야 하므로 담는 수도 호출 한도로 묶이고(60초 동안 버킷 크기 + 다시 채워지는 몫 이하), 만료된 것은
  새로 담을 때 앞에서부터 지운다.
- 클라이언트 IP로 나누지 않는다: CloudFront·ALB 뒤에서는 엣지·ALB 주소가 보여 여러 사용자가 한 한도를 나눠 쓰게 된다.
- 학생 본인이 부르는 재검증·근거 조회·포트폴리오 생성·직무 비교는 이 제한을 쓰지 않는다(항상 검증기에 새로 묻는다).
  그 경로의 호출 양은 verifier_budget.py가 호출자별·프로세스 전체로 묶는다.
"""
import asyncio
import hashlib
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional, Union

from app.domain.credential import ProviderBadgeResult
from app.integrations.ports import ProviderError

RESULT_TTL_SECONDS = 60
CALLS_PER_MINUTE = 300


class BudgetExceeded(Exception):
    """기업용 조회에 필요한 검증기 호출 몫이 모자람. retry_after: 몫이 다시 찰 때까지의 초 (Retry-After 헤더 값)."""

    def __init__(self, retry_after: int):
        super().__init__(f"public verifier call budget exceeded (retry after {retry_after}s)")
        self.retry_after = retry_after


@dataclass(frozen=True, eq=False)
class Flight:
    """검증기에 묻는 중인 서명 원본 하나. future의 결과는 (검증 시각, 검증기 응답) 또는 ProviderError(실패)다."""

    key: bytes
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future


class PublicViewGate:
    def __init__(self, ttl_seconds: float = RESULT_TTL_SECONDS, calls_per_minute: int = CALLS_PER_MINUTE,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.capacity = float(calls_per_minute)
        self.rate = calls_per_minute / 60.0  # 초당 다시 채우는 몫
        self._clock = clock
        # 이벤트 루프 안에서만 부르지만(await 사이에 잡고 있지 않음) 스레드에서 불려도 안전하게 잠근다.
        # begin이 잠근 채로 reserve를 부르므로 다시 잠글 수 있는 잠금을 쓴다.
        self._lock = threading.RLock()
        # 키 → (담은 시각(clock), 검증 시각, 검증기 응답). 넣은 순서 = 담은 시각 순서.
        self._results: "OrderedDict[bytes, tuple[float, datetime, ProviderBadgeResult]]" = OrderedDict()
        self._tokens = self.capacity
        self._refilled_at = clock()
        # 키 → 지금 검증기에 묻고 있는 호출 (응답이 오면 settle이 지운다)
        self._flights: dict[bytes, Flight] = {}

    @staticmethod
    def _key(vc_jwt: str) -> bytes:
        return hashlib.sha256(vc_jwt.encode("utf-8")).digest()

    def store(self, vc_jwt: str, verified_at: datetime, result: ProviderBadgeResult) -> None:
        """검증기에 새로 물어 받은 응답을 담는다 (성공한 응답만 담는다: 검증기 오류는 부르는 쪽이 담지 않는다)."""
        key = self._key(vc_jwt)
        with self._lock:
            now = self._clock()  # 잠근 뒤에 읽어야 넣은 순서와 담은 시각 순서가 같다
            self._results.pop(key, None)
            self._results[key] = (now, verified_at, result)
            while self._results and now - next(iter(self._results.values()))[0] >= self.ttl_seconds:
                self._results.popitem(last=False)  # 앞에서부터 만료된 것을 지운다 (방금 넣은 것은 남음)

    def reserve(self, calls: int) -> None:
        """검증기를 calls번 부를 몫을 가져간다.

        모자라면 하나도 가져가지 않고 BudgetExceeded(retry_after: 몫이 다시 찰 때까지의 초)를 던진다.
        """
        if calls <= 0:
            return
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._refilled_at) * self.rate)
            self._refilled_at = now
            if self._tokens < calls:
                raise BudgetExceeded(max(1, math.ceil((calls - self._tokens) / self.rate)))
            self._tokens -= calls

    def begin(self, tokens: list[str]) -> list[tuple[str, Any]]:
        """서명 원본마다 할 일을 정하고, 새로 부를 것만큼만 몫을 가져간다 (잠근 채 한 번에).

        tokens와 같은 순서로 돌려준다:
        - ("cached", (검증 시각, 검증기 응답)): RESULT_TTL_SECONDS 안에 받은 응답을 다시 쓴다.
        - ("wait", flight): 다른 조회(또는 이 목록의 앞 항목)가 같은 원본을 묻고 있다. wait(flight)로 그 결과를 기다린다.
        - ("call", flight): 부른 쪽이 검증기에 묻는다. 끝나면 성공·실패·취소와 관계없이 settle(flight, ...)로 알려야 한다.
        몫이 모자라면 아무것도 등록하지 않고 BudgetExceeded를 던진다 (검증기 호출 0번).
        기다릴 Future를 지금 도는 이벤트 루프에 만들므로 이벤트 루프 안에서 불러야 한다.
        """
        loop = asyncio.get_running_loop()
        steps: list[tuple[str, Any]] = []
        leading: dict[bytes, Flight] = {}
        with self._lock:
            now = self._clock()
            for token in tokens:
                key = self._key(token)
                entry = self._results.get(key)
                flight = leading.get(key) or self._flights.get(key)
                if entry is not None and now - entry[0] < self.ttl_seconds:
                    steps.append(("cached", (entry[1], entry[2])))
                elif flight is not None and flight.loop is loop and not flight.future.done():
                    steps.append(("wait", flight))
                else:
                    # 다른 이벤트 루프의 호출(테스트)은 기다릴 수 없으므로 없는 것으로 보고 새로 부른다.
                    flight = leading[key] = Flight(key, loop, loop.create_future())
                    steps.append(("call", flight))
            self.reserve(len(leading))  # 모자라면 여기서 끝난다 (아래 등록 전)
            self._flights.update(leading)
        return steps

    def settle(self, flight: Flight, answer: Union[tuple[datetime, ProviderBadgeResult], ProviderError]) -> None:
        """("call", flight)를 받은 쪽이 결과((검증 시각, 응답) 또는 ProviderError)를 알린다. 처음 알린 것만 쓴다."""
        with self._lock:
            if self._flights.get(flight.key) is flight:
                del self._flights[flight.key]
        if not flight.future.done():
            flight.future.set_result(answer)

    @staticmethod
    async def wait(flight: Flight, timeout: Optional[float] = None
                   ) -> Union[tuple[datetime, ProviderBadgeResult], ProviderError]:
        """먼저 묻던 조회의 결과를 기다린다. shield: 기다리던 조회가 취소돼도 함께 기다리는 Future는 취소되지 않는다.

        timeout(초)을 주면 그만큼만 기다리고, 넘으면 ProviderError를 돌려준다 (먼저 묻던 쪽이 끝내 알리지 않아도 멈추지 않는다).
        """
        try:
            return await asyncio.wait_for(asyncio.shield(flight.future), timeout)
        except asyncio.TimeoutError:
            return ProviderError("검증기 호출이 끝나지 않았습니다")

    def clear(self) -> None:
        with self._lock:
            self._results.clear()
            self._flights.clear()
            self._tokens = self.capacity
            self._refilled_at = self._clock()


# 프로세스(태스크) 하나에 하나. 기업용 조회(app/api/shares.py)만 쓴다.
public_view_gate = PublicViewGate()
