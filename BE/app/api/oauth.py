import logging
import math
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Callable, Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import AfterValidator, BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.api.auth import build_auth_response, issue_user_identity
from app.core.auth import ALGORITHM
from app.core.config import get_settings
from app.integrations import get_google_oauth_provider
from app.integrations.ports import OAuthCodeError, OAuthProvider
from app.services.database import OAuthAccountConflict, OAuthPasswordAccountConflict, db_service
from app.services.key_custody import KeyCustody, get_key_custody
from app.services.login_throttle import TOO_MANY_ATTEMPTS

router = APIRouter()
# 감사 로그 (INFO 한 줄, 이메일·DID·토큰·이름 없음): event=oauth_login outcome=created|linked|login|refused_password_account,
# 토큰 교환 한도(ExchangeBudget)가 거절한 콜백마다 event=oauth_exchange_denied retry_after=<초>
audit = logging.getLogger("vc-be.audit")

_STATE_TTL_SECONDS = 600
# Google로 내보내기 전에 거르는 입력 길이. 실제 값은 훨씬 짧다 (인가 코드 256바이트 이하, state 약 310자).
# FE도 2,048자를 넘는 code·state는 보내지 않는다 (FE/src/lib/oauth.ts).
MAX_OAUTH_PARAM_CHARS = 2048
# GET 콜백이 되돌려 주는 error 쿼리 값의 길이 (Google의 오류 코드는 access_denied 같은 짧은 식별자다)
MAX_OAUTH_ERROR_CHARS = 200
# PKCE (RFC 7636): code_verifier는 [A-Za-z0-9._~-] 43~128자, S256 code_challenge는 SHA-256의 base64url(패딩 없음) 43자
_CODE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_CODE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
# 프로세스 전체의 분당 Google 토큰 교환 수 (한 반이 함께 로그인해도 남는 크기. 기업용 조회 한도와 같은 값)
EXCHANGES_PER_MINUTE = 300


class ExchangeBudget:
    """Google 토큰 교환(외부 호출) 수 제한: 프로세스 전체로 분당 calls_per_minute번 (토큰 버킷, public_view_gate와 같은 방식).

    콜백은 로그인 없이 부르고 state도 누구나 받을 수 있다. 한도가 없으면 아무 code나 반복해 보내는 것만으로 BE가 우리
    client_id·client_secret을 실어 Google 토큰 엔드포인트를 끝없이 부르게 된다 (Google이 이 클라이언트를 제한하면 모든
    사용자의 Google 로그인이 막힌다). 몫이 모자라면 Google을 부르지 않고 429 + Retry-After로 답한다.
    - 프로세스 메모리에만 둔다 (태스크마다 따로 세고 재시작하면 지워짐).
    - 클라이언트 IP로 나누지 않는다 (CloudFront·ALB 뒤에서는 엣지·ALB 주소가 보인다). 그래서 누가 몰아 보내는 동안에는
      정상 사용자도 함께 429가 된다: 이 한도가 지키는 것은 Google 클라이언트이고, IP별 제한은 AWS WAF 속도 기반 규칙이 맡는다.
    """

    def __init__(self, calls_per_minute: int = EXCHANGES_PER_MINUTE, clock: Callable[[], float] = time.monotonic):
        self.capacity = float(calls_per_minute)
        self.rate = calls_per_minute / 60.0  # 초당 다시 채우는 몫
        self._clock = clock
        # 이벤트 루프 안에서만 부르지만(await 사이에 잡고 있지 않음) 스레드에서 불려도 안전하게 잠근다.
        self._lock = threading.Lock()
        self._tokens = self.capacity
        self._refilled_at = clock()

    def reserve(self) -> Optional[int]:
        """교환 한 번의 몫을 가져가고 None을 돌려준다. 모자라면 가져가지 않고 몫이 다시 찰 때까지의 초를 준다."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._refilled_at) * self.rate)
            self._refilled_at = now
            if self._tokens < 1:
                return max(1, math.ceil((1 - self._tokens) / self.rate))
            self._tokens -= 1
            return None

    def clear(self) -> None:
        with self._lock:
            self._tokens = self.capacity
            self._refilled_at = self._clock()


# 프로세스(태스크) 하나에 하나.
oauth_exchange_budget = ExchangeBudget()


def _clean_code_verifier(value: Optional[str]) -> Optional[str]:
    """PKCE code_verifier 검사 (없거나 비어 있으면 None). 형식이 틀린 값은 Google로 보내지 않고 ValueError(→ 422)."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not _CODE_VERIFIER.fullmatch(value):
        raise ValueError("code_verifier must be 43 to 128 unreserved characters (RFC 7636)")
    return value


class OAuthCallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=MAX_OAUTH_PARAM_CHARS)
    state: str = Field(min_length=1, max_length=MAX_OAUTH_PARAM_CHARS)
    redirect_uri: Optional[str] = Field(default=None, max_length=MAX_OAUTH_PARAM_CHARS)
    # 길이 상한은 형식 검사 전에 아주 긴 값을 거르는 용도다. 실제 규칙(43~128자)은 아래 검사기가 본다.
    code_verifier: Optional[str] = Field(default=None, max_length=MAX_OAUTH_PARAM_CHARS)

    @field_validator("code", "state")
    @classmethod
    def strip_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("code_verifier")
    @classmethod
    def check_code_verifier(cls, v: Optional[str]) -> Optional[str]:
        return _clean_code_verifier(v)


def _resolve_redirect_uri(requested: Optional[str]) -> str:
    settings = get_settings()
    if requested is None or not requested.strip():
        if not settings.google_redirect_uri:
            raise HTTPException(status_code=503, detail="Google OAuth redirect_uri is not configured")
        return settings.google_redirect_uri
    candidate = requested.strip()
    if candidate not in settings.google_allowed_redirect_uris:
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")
    return candidate


def _create_oauth_state(provider: str, redirect_uri: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "type": "oauth_state",
        "provider": provider,
        "redirect_uri": redirect_uri,
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + timedelta(seconds=_STATE_TTL_SECONDS),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=ALGORITHM)


def _verify_oauth_state(state: str, provider: str) -> str:
    try:
        payload = jwt.decode(
            state,
            get_settings().jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "type", "provider", "redirect_uri", "nonce"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state") from exc

    if payload.get("type") != "oauth_state" or payload.get("provider") != provider:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    redirect_uri = payload.get("redirect_uri")
    if not isinstance(redirect_uri, str) or not redirect_uri:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    return redirect_uri


@router.get("/google/authorize")
def google_authorize(
    redirect_uri: Optional[str] = Query(default=None),
    code_challenge: Optional[str] = Query(default=None),
    code_challenge_method: Optional[str] = Query(default=None),
    redirect: bool = Query(default=False),
    provider: OAuthProvider = Depends(get_google_oauth_provider),
):
    if code_challenge is not None:
        # 형식이 맞는 S256 code_challenge만 Google 인가 주소에 싣는다 (아무 길이·문자의 값을 그대로 옮기지 않는다).
        if not _CODE_CHALLENGE.fullmatch(code_challenge.strip()):
            raise HTTPException(status_code=400, detail="Invalid code_challenge")
        if code_challenge_method not in (None, "S256"):
            raise HTTPException(status_code=400, detail="Only S256 code_challenge_method is supported")
        challenge = code_challenge.strip()
        challenge_method = "S256"
    else:
        challenge = None
        challenge_method = None

    resolved_redirect_uri = _resolve_redirect_uri(redirect_uri)
    state = _create_oauth_state("google", resolved_redirect_uri)
    authorization_url = provider.build_authorize_url(
        state=state,
        redirect_uri=resolved_redirect_uri,
        code_challenge=challenge,
        code_challenge_method=challenge_method,
    )
    if redirect:
        return RedirectResponse(url=authorization_url, status_code=302)
    return {
        "success": True,
        "provider": "google",
        "authorization_url": authorization_url,
        "state": state,
        "expires_in": _STATE_TTL_SECONDS,
    }


async def _complete_google_oauth(
    *,
    code: str,
    state: str,
    redirect_uri: Optional[str],
    code_verifier: Optional[str],
    provider: OAuthProvider,
    custody: Optional[KeyCustody],
) -> dict:
    state_redirect_uri = _verify_oauth_state(state, "google")
    if redirect_uri is not None and redirect_uri.strip() and redirect_uri.strip() != state_redirect_uri:
        raise HTTPException(status_code=400, detail="redirect_uri does not match OAuth state")

    # 여기까지 통과한 요청만 Google을 부른다. 부르기 전에 교환 한 번의 몫을 가져가고, 모자라면 부르지 않는다 (ExchangeBudget).
    retry_after = oauth_exchange_budget.reserve()
    if retry_after is not None:
        # 누가 몰아 보내는 동안에는 정상 사용자의 Google 로그인도 함께 429가 되므로, 지표 필터로 알 수 있게 한 줄 남긴다.
        audit.info("event=oauth_exchange_denied retry_after=%d", retry_after)
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS, headers={"Retry-After": str(retry_after)})

    try:
        user_info = await provider.exchange_code(
            code=code,
            redirect_uri=state_redirect_uri,
            code_verifier=code_verifier,
        )
    except OAuthCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not user_info.email_verified:
        raise HTTPException(status_code=400, detail="OAuth email is not verified")

    try:
        user, created, linked = await run_in_threadpool(
            db_service.find_or_create_oauth_user,
            provider=user_info.provider,
            provider_user_id=user_info.provider_user_id,
            email=user_info.email,
            name=user_info.name,
            issue_identity=lambda uid: issue_user_identity(uid, custody),
        )
    except OAuthAccountConflict as exc:
        if isinstance(exc, OAuthPasswordAccountConflict):
            # 같은 이메일의 비밀번호 계정에는 붙이지 않았다 (user는 그 기존 계정). 반복되면 그 주소를 먼저 가입해 둔 계정일 수 있다.
            audit.info("event=oauth_login outcome=refused_password_account user=%s", exc.user_id)
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # FE(GoogleCallbackPage)가 이 문구의 '회원가입'·'연동' 포함 여부로 환영 안내를 고른다. 바꾸면 FE도 함께 바꾼다.
    if created:
        outcome, message = "created", "Google OAuth 회원가입 및 로그인 완료"
    elif linked:
        outcome, message = "linked", "기존 계정에 Google 계정 연동 및 로그인 성공"
    else:
        outcome, message = "login", "Google OAuth 로그인 성공"
    audit.info("event=oauth_login outcome=%s user=%s", outcome, user["id"])
    return build_auth_response(user, message)


def _development_only() -> None:
    """GET 콜백은 BE 단독 테스트(개발)용이다. APP_ENV=production에서는 없는 경로처럼 404로 답한다.

    FE는 POST만 쓴다. GET은 code·state·code_verifier가 URL에 실려 접근 로그(ALB·CloudFront 포함)에 남고, 사전 요청 없이
    다른 사이트에서도 걸 수 있어 IP별 제한(WAF)을 비켜 간다. 쿼리 검사보다 먼저 답하도록 경로 의존성으로 둔다.
    """
    if get_settings().app_env == "production":
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/google/callback", dependencies=[Depends(_development_only)])
async def google_callback_get(
    code: Optional[str] = Query(default=None, max_length=MAX_OAUTH_PARAM_CHARS),
    state: Optional[str] = Query(default=None, max_length=MAX_OAUTH_PARAM_CHARS),
    error: Optional[str] = Query(default=None),
    # Query를 Annotated 안에 둬야 길이 검사 다음에 형식 검사기(_clean_code_verifier)가 실행된다 (기본값 자리에 두면 빠진다).
    code_verifier: Annotated[Optional[str], Query(max_length=MAX_OAUTH_PARAM_CHARS),
                             AfterValidator(_clean_code_verifier)] = None,
    provider: OAuthProvider = Depends(get_google_oauth_provider),
    custody: Optional[KeyCustody] = Depends(get_key_custody),
):
    if error:
        # 쿼리로 받은 값을 길이 제한 없이 되돌려 주지 않는다.
        raise HTTPException(status_code=400, detail=f"OAuth authorization failed: {error[:MAX_OAUTH_ERROR_CHARS]}")
    if not code or not code.strip() or not state or not state.strip():
        raise HTTPException(status_code=400, detail="Missing OAuth code or state")
    return await _complete_google_oauth(
        code=code.strip(),
        state=state.strip(),
        redirect_uri=None,
        code_verifier=code_verifier,
        provider=provider,
        custody=custody,
    )


@router.post("/google/callback")
async def google_callback_post(
    req: OAuthCallbackRequest,
    provider: OAuthProvider = Depends(get_google_oauth_provider),
    custody: Optional[KeyCustody] = Depends(get_key_custody),
):
    return await _complete_google_oauth(
        code=req.code,
        state=req.state,
        redirect_uri=req.redirect_uri,
        code_verifier=req.code_verifier,
        provider=provider,
        custody=custody,
    )
