"""로그인 실패 제한 (계정=이메일별).

- 같은 이메일로 WINDOW_SECONDS 안에 MAX_FAILURES번 틀리면, 가장 오래된 실패가 창 밖으로 나갈 때까지 그 이메일의 로그인을
  429로 거절한다 (비밀번호가 맞아도, bcrypt도 돌리지 않음). 로그인에 성공하면 그 이메일의 기록을 지운다.
- 시도는 비밀번호를 확인하기 전에 실패로 먼저 기록하고 성공하면 지운다. 그래서 동시에 몰아 보내도 한도를 넘지 못한다.
- 가입하지 않은 이메일도 똑같이 센다. 429가 '가입된 이메일'을 알려 주지 않게 하기 위해서다.
- 이메일은 DB 조회(lower(email))처럼 접어서 센다: 앞뒤 공백을 떼고 글자마다 따로 소문자로 바꾼다. PostgreSQL lower()(libc 정렬)는
  글자마다 접으므로('İ'(U+0130) → 'i', 어말을 포함한 모든 'Σ' → 'σ') DB가 같은 계정으로 찾는 표기는 같은 키를 쓴다.
  DB 정렬이 이와 다르게 접으면(예: ICU 정렬, 또는 BE 파이썬(3.11, 유니코드 14)보다 새 유니코드 대소문자 표를 쓰는 libc)
  다른 키의 표기로 계정을 찾을 수 있어, 그때는 로그인이 저장된 이메일로도 센다 (api/auth.py). 파이썬만 접고 DB는 그대로
  두는 글자(예: musl(Alpine) PostgreSQL의 원문자 'Ⓐ'. Compose·CI 이미지의 glibc 2.36은 파이썬처럼 접는다)는 여러 표기가
  한 키를 나눠 쓸 뿐이다 (DB가 못 찾음).
- 클라이언트 IP로는 세지 않는다. CloudFront·ALB 뒤에서 BE가 보는 주소는 CloudFront 엣지나 ALB라 여러 사용자가 한 키를
  나눠 쓰게 되기 때문이다. IP별 제한은 AWS WAF 속도 기반 규칙이 맡는다 (README "AWS 배포 체크리스트").
- 기록은 프로세스 메모리에만 둔다(태스크마다 따로 세고 재시작하면 지워짐). 키는 이메일의 SHA-256이라 원문을 들고 있지 않고,
  키 수는 MAX_KEYS로 묶어 아무 이메일이나 보내도 메모리가 자라지 않는다.
"""
import hashlib
import math
import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Optional

MAX_FAILURES = 10
WINDOW_SECONDS = 15 * 60
MAX_KEYS = 10_000

TOO_MANY_ATTEMPTS = "Too many attempts. Try again later."


class LoginThrottle:
    def __init__(self, max_failures: int = MAX_FAILURES, window_seconds: float = WINDOW_SECONDS,
                 max_keys: int = MAX_KEYS, clock: Callable[[], float] = time.monotonic):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: "OrderedDict[bytes, deque[float]]" = OrderedDict()

    @staticmethod
    def _key(email: str) -> bytes:
        # 로그인 조회와 같은 정규화 (DB는 lower(email)로 찾음). 파이썬 str.lower()는 'İ'를 두 글자('i' + U+0307)로,
        # 어말 'Σ'를 'ς'로 바꿔 DB lower()와 다른 키가 되므로, DB처럼 글자마다 접고 그 첫 글자만 쓴다 (보통 이메일은 같은 키).
        folded = "".join(ch.lower()[:1] for ch in email.strip())
        return hashlib.sha256(folded.encode("utf-8", "surrogatepass")).digest()

    def same_key(self, email: str, other: str) -> bool:
        """두 이메일이 같은 기록을 쓰는지 (앞뒤 공백을 떼고 글자마다 소문자로 접으면 같은지: 대소문자·'İ'·'Σ' 표기 차이)."""
        return self._key(email) == self._key(other)

    def reserve(self, email: str) -> Optional[int]:
        """시도 하나를 실패로 먼저 기록하고 None을 돌려준다. 이미 한도면 기록하지 않고 다시 시도할 수 있을 때까지의 초를 준다."""
        key = self._key(email)
        now = self._clock()
        with self._lock:
            stamps = self._failures.pop(key, None) or deque()
            while stamps and stamps[0] <= now - self.window_seconds:
                stamps.popleft()
            self._failures[key] = stamps  # 가장 최근 사용으로 맨 뒤에 둔다
            if len(stamps) >= self.max_failures:
                return max(1, math.ceil(stamps[0] + self.window_seconds - now))
            stamps.append(now)
            while len(self._failures) > self.max_keys:
                # 가장 오래 쓰지 않은 키부터 버리되, 방금 쓴 키와 잠긴 키(한도만큼 실패했고 마지막 실패가 창 안)는 남긴다.
                # 새 이메일을 몰아 보내 잠긴 계정의 기록을 밀어내고 잠금을 푸는 것을 막는다. 다른 키가 모두 잠겼으면 가장 오래된 키.
                horizon = now - self.window_seconds
                old = next((k for k, s in self._failures.items()
                            if k != key and (len(s) < self.max_failures or s[-1] <= horizon)), None)
                if old is None:
                    self._failures.popitem(last=False)
                else:
                    del self._failures[old]
            return None

    def succeeded(self, email: str) -> None:
        with self._lock:
            self._failures.pop(self._key(email), None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle()
