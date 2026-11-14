import secrets
from typing import Optional

import bcrypt
from anyio import CapacityLimiter, to_thread
from anyio.lowlevel import RunVar

# bcrypt는 입력의 앞 72바이트만 사용하므로 그보다 긴 비밀번호는 받지 않는다.
MAX_PASSWORD_BYTES = 72

# bcrypt(비용 12, 1회 약 0.2초)는 공용 스레드 풀(기본 40개: 모든 동기 API·DB 호출이 함께 씀) 대신 이 제한기로 이 개수만큼만
# 동시에 돌린다. 가입·로그인이 몰려도 나머지는 이벤트 루프에서 차례를 기다리므로 다른 API와 /health가 스레드를 못 받는 일이 없다.
PASSWORD_HASH_CONCURRENCY = 4
_hash_limiter: RunVar[CapacityLimiter] = RunVar("password_hash_limiter")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # 해시 형식이 깨져 있거나 비밀번호가 72바이트를 넘는 경우
        return False


# 비교할 해시가 없을 때(미가입 이메일, 비밀번호가 없는 사용자) 대신 비교하는 해시.
# hash_password로 만들어 실제 해시와 bcrypt 비용이 같고, 프로세스마다 무작위 값이라 어떤 비밀번호와도 맞지 않는다.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def check_login_password(password: str, password_hash: Optional[str]) -> bool:
    """로그인 비밀번호 확인. 해시가 없거나 비밀번호가 72바이트를 넘으면(UTF-8로 바꿀 수 없어도) 더미 해시와 같은 비용의
    bcrypt 비교를 한 뒤 항상 False.

    사용자가 있을 때만 bcrypt를 돌리면 응답 시간(약 180ms 차이)으로 이메일 가입 여부가 드러난다.
    """
    if not password_hash:
        # 해시가 없으면(미가입 이메일, 비밀번호가 없는 사용자) 비밀번호의 길이도 재지 않고 더미 비교만 한 번 한 뒤 False다.
        # 그래서 이 함수를 바로 불러 문자열이 아닌 값(None 등)을 넘겨도 예외가 나지 않는다.
        verify_password("x", _DUMMY_PASSWORD_HASH)
        return False
    try:
        too_long = len(password.encode("utf-8")) > MAX_PASSWORD_BYTES
    except UnicodeEncodeError:
        # 짝 없는 서로게이트처럼 UTF-8로 바꿀 수 없는 비밀번호(API에서는 요청 검사가 422로 먼저 거절한다)도 맞을 수 없으므로,
        # 이 함수를 바로 불러도 예외 대신 같은 더미 비교를 한 뒤 False다.
        too_long = True
    if too_long:
        # bcrypt 5는 72바이트를 넘는 비밀번호를 비교 없이 바로 ValueError로 거절한다. 그런 시도도 같은 비용을 치르게 해,
        # 비용 없는 로그인을 몰아 보내 로그인 실패 기록(login_throttle)의 잠긴 키를 밀어내지 못하게 한다.
        verify_password("x", _DUMMY_PASSWORD_HASH)
        return False
    return verify_password(password, password_hash)


def _password_hash_limiter() -> CapacityLimiter:
    # 이벤트 루프마다 하나 (anyio 기본 스레드 제한기와 같은 방식)
    try:
        return _hash_limiter.get()
    except LookupError:
        limiter = CapacityLimiter(PASSWORD_HASH_CONCURRENCY)
        _hash_limiter.set(limiter)
        return limiter


async def hash_password_async(password: str) -> str:
    """API(가입)용: 전용 제한기 안의 작업 스레드에서 hash_password를 실행한다."""
    return await to_thread.run_sync(hash_password, password, limiter=_password_hash_limiter())


async def verify_password_async(password: str, password_hash: Optional[str]) -> bool:
    """API(로그인)용: 전용 제한기 안의 작업 스레드에서 check_login_password를 실행한다.

    해시가 없으면(미가입 이메일, 비밀번호가 없는 사용자) 같은 비용의 더미 비교를 한 뒤 False다.
    """
    return await to_thread.run_sync(check_login_password, password, password_hash, limiter=_password_hash_limiter())
