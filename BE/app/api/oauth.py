import logging
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
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
from app.integrations import get_github_oauth_provider, get_google_oauth_provider, get_naver_oauth_provider
from app.integrations.oauth import GitHubOAuthAdapter, GoogleOAuthAdapter, NaverOAuthAdapter
from app.integrations.ports import OAuthCodeError, OAuthEmailMissing, OAuthEmailUnverified, OAuthProvider
from app.services.database import (
    OAuthAccountConflict, OAuthPasswordAccountConflict, OAuthUnverifiedLinkConflict, db_service,
)
from app.services.key_custody import KeyCustody, get_key_custody
from app.services.login_throttle import TOO_MANY_ATTEMPTS

router = APIRouter()
# 감사 로그 (INFO 한 줄, 이메일·DID·토큰·이름 없음, 끝에 provider=google|github|naver):
# event=oauth_login outcome=created|linked|login|refused_password_account|refused_unverified_link,
# 토큰 교환 한도(ExchangeBudget)가 거절한 콜백마다 event=oauth_exchange_denied retry_after=<초>
audit = logging.getLogger("vc-be.audit")

# FE가 이 문구로 안내를 고른다 (FE/src/lib/apiClient.ts). 바꾸면 FE도 함께 바꾼다.
OAUTH_EMAIL_REQUIRED = "OAuth email is required"
OAUTH_EMAIL_NOT_VERIFIED = "OAuth email is not verified"

_STATE_TTL_SECONDS = 600
# 제공자로 내보내기 전에 거르는 입력 길이. 실제 값은 훨씬 짧다 (Google 인가 코드 256바이트 이하, state 약 310자).
# FE도 2,048자를 넘는 code·state는 보내지 않는다 (FE/src/lib/oauth.ts).
MAX_OAUTH_PARAM_CHARS = 2048
# GET 콜백이 되돌려 주는 error 쿼리 값의 길이 (제공자의 오류 코드는 access_denied 같은 짧은 식별자다)
MAX_OAUTH_ERROR_CHARS = 200
# PKCE (RFC 7636): code_verifier는 [A-Za-z0-9._~-] 43~128자, S256 code_challenge는 SHA-256의 base64url(패딩 없음) 43자
_CODE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_CODE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
# 제공자 하나당 프로세스 전체의 분당 토큰 교환 수 (한 반이 함께 로그인해도 남는 크기. 기업용 조회 한도와 같은 값)
EXCHANGES_PER_MINUTE = 300


class ExchangeBudget:
    """OAuth 토큰 교환(외부 호출) 수 제한: 제공자마다 프로세스 전체로 분당 calls_per_minute번 (토큰 버킷, public_view_gate와 같은 방식).

    콜백은 로그인 없이 부르고 state도 누구나 받을 수 있다. 한도가 없으면 아무 code나 반복해 보내는 것만으로 BE가 우리
    client_id·client_secret을 실어 제공자의 토큰 엔드포인트를 끝없이 부르게 된다 (제공자가 이 클라이언트를 제한하면 모든
    사용자의 그 제공자 로그인이 막힌다). 몫이 모자라면 제공자를 부르지 않고 429 + Retry-After로 답한다.
    - 프로세스 메모리에만 둔다 (태스크마다 따로 세고 재시작하면 지워짐).
    - 제공자마다 따로 센다: 한 제공자의 콜백을 몰아 보내도 다른 제공자의 로그인은 429가 되지 않는다.
    - 클라이언트 IP로 나누지 않는다 (CloudFront·ALB 뒤에서는 엣지·ALB 주소가 보인다). 그래서 누가 몰아 보내는 동안에는
      그 제공자의 정상 사용자도 함께 429가 된다: 이 한도가 지키는 것은 제공자 클라이언트이고, IP별 제한은 AWS WAF 속도 기반
      규칙이 맡는다.
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


# 프로세스(태스크) 하나에 제공자마다 하나. Google 것은 예전 이름(oauth_exchange_budget)을 그대로 쓴다.
oauth_exchange_budget = ExchangeBudget()
github_exchange_budget = ExchangeBudget()
naver_exchange_budget = ExchangeBudget()


def exchange_budget(provider: str) -> ExchangeBudget:
    """제공자의 토큰 교환 한도. 부를 때마다 모듈 속성을 읽는다 (테스트가 바꿔 끼운 한도도 그대로 쓰이게)."""
    return {"google": oauth_exchange_budget, "github": github_exchange_budget, "naver": naver_exchange_budget}[provider]


def _clean_code_verifier(value: Optional[str]) -> Optional[str]:
    """PKCE code_verifier 검사 (없거나 비어 있으면 None). 형식이 틀린 값은 제공자로 보내지 않고 ValueError(→ 422)."""
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


@dataclass(frozen=True)
class _ProviderSpec:
    """경로를 만들 때 쓰는 제공자 정보 (어댑터 클래스의 속성에서 가져온다)."""

    name: str  # 경로·state의 provider 클레임·oauth_accounts.provider
    label: str  # 응답 문구의 이름
    requires_verified_email: bool  # 제공자가 인증한 이메일만 받는지 (Google·GitHub). Naver는 인증 여부를 알리지 않는다.


def _redirect_uris(provider: str) -> tuple[str, list[str]]:
    """(기본 콜백 주소, 허용하는 콜백 주소 목록). Google은 GOOGLE_REDIRECT_URI + GOOGLE_ALLOWED_REDIRECT_URIS,
    GitHub·네이버는 *_REDIRECT_URI 하나뿐이다."""
    settings = get_settings()
    if provider == "google":
        return settings.google_redirect_uri, settings.google_allowed_redirect_uris
    default = getattr(settings, f"{provider}_redirect_uri")
    return default, [default] if default else []


def _resolve_redirect_uri(spec: _ProviderSpec, requested: Optional[str]) -> str:
    default, allowed = _redirect_uris(spec.name)
    if not allowed or ((requested is None or not requested.strip()) and not default):
        # 콜백 주소를 하나도 설정하지 않은 제공자(운영에서 아직 켜지 않은 GitHub·네이버 등)는 FE가 보낸 주소와 비교하기 전에
        # '설정 없음'(503)으로 답한다. 400 'Invalid redirect_uri'는 설정된 주소와 다른 주소를 보낸 경우만이다.
        raise HTTPException(status_code=503, detail=f"{spec.label} OAuth redirect_uri is not configured")
    if requested is None or not requested.strip():
        return default
    candidate = requested.strip()
    if candidate not in allowed:
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


def _authorize(
    spec: _ProviderSpec,
    *,
    redirect_uri: Optional[str],
    code_challenge: Optional[str],
    code_challenge_method: Optional[str],
    redirect: bool,
    provider: OAuthProvider,
):
    if code_challenge is not None:
        # 형식이 맞는 S256 code_challenge만 인가 주소에 싣는다 (아무 길이·문자의 값을 그대로 옮기지 않는다).
        # PKCE를 받지 않는 제공자(Naver)는 어댑터가 인가 주소에 싣지 않는다 (FE는 제공자와 관계없이 보낸다).
        if not _CODE_CHALLENGE.fullmatch(code_challenge.strip()):
            raise HTTPException(status_code=400, detail="Invalid code_challenge")
        if code_challenge_method not in (None, "S256"):
            raise HTTPException(status_code=400, detail="Only S256 code_challenge_method is supported")
        challenge = code_challenge.strip()
        challenge_method = "S256"
    else:
        challenge = None
        challenge_method = None

    resolved_redirect_uri = _resolve_redirect_uri(spec, redirect_uri)
    state = _create_oauth_state(spec.name, resolved_redirect_uri)
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
        "provider": spec.name,
        "authorization_url": authorization_url,
        "state": state,
        "expires_in": _STATE_TTL_SECONDS,
    }


async def _complete_oauth(
    spec: _ProviderSpec,
    *,
    code: str,
    state: str,
    redirect_uri: Optional[str],
    code_verifier: Optional[str],
    provider: OAuthProvider,
    custody: Optional[KeyCustody],
) -> dict:
    state_redirect_uri = _verify_oauth_state(state, spec.name)
    if redirect_uri is not None and redirect_uri.strip() and redirect_uri.strip() != state_redirect_uri:
        raise HTTPException(status_code=400, detail="redirect_uri does not match OAuth state")

    # 여기까지 통과한 요청만 제공자를 부른다. 부르기 전에 교환 한 번의 몫을 가져가고, 모자라면 부르지 않는다 (ExchangeBudget).
    retry_after = exchange_budget(spec.name).reserve()
    if retry_after is not None:
        # 누가 몰아 보내는 동안에는 그 제공자의 정상 로그인도 함께 429가 되므로, 지표 필터로 알 수 있게 한 줄 남긴다.
        audit.info("event=oauth_exchange_denied retry_after=%d provider=%s", retry_after, spec.name)
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS, headers={"Retry-After": str(retry_after)})

    try:
        user_info = await provider.exchange_code(
            code=code,
            redirect_uri=state_redirect_uri,
            code_verifier=code_verifier,
            state=state,  # 토큰 요청에 state를 함께 보내는 제공자(Naver)만 쓴다
        )
    except OAuthCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OAuthEmailMissing as exc:
        # 사용자가 이메일 제공에 동의하지 않았다 (계정을 이메일로 찾고 만들므로 이메일 없이는 가입·로그인할 수 없다).
        raise HTTPException(status_code=400, detail=OAUTH_EMAIL_REQUIRED) from exc
    except OAuthEmailUnverified as exc:
        # 제공자가 토큰 단계에서 이메일이 인증되지 않았다고 알렸다 (GitHub unverified_user_email). 아래 검사와 같은 400이다.
        raise HTTPException(status_code=400, detail=OAUTH_EMAIL_NOT_VERIFIED) from exc

    # 이메일을 인증하는 제공자(Google·GitHub)는 인증된 이메일만 받는다. Naver는 인증 여부를 알리지 않으므로 여기서 막지 않고,
    # 인증되지 않은 이메일로 보고 같은 이메일의 다른 계정에 붙이지 않는다 (database.find_or_create_oauth_user).
    email_verified = spec.requires_verified_email and user_info.email_verified is True
    if spec.requires_verified_email and not email_verified:
        raise HTTPException(status_code=400, detail=OAUTH_EMAIL_NOT_VERIFIED)

    try:
        user, created, linked = await run_in_threadpool(
            db_service.find_or_create_oauth_user,
            provider=spec.name,
            provider_user_id=user_info.provider_user_id,
            email=user_info.email,
            email_verified=email_verified,
            name=user_info.name,
            issue_identity=lambda uid: issue_user_identity(uid, custody),
        )
    except OAuthAccountConflict as exc:
        if isinstance(exc, OAuthPasswordAccountConflict):
            # 같은 이메일의 비밀번호 계정에는 붙이지 않았다 (user는 그 기존 계정). 반복되면 그 주소를 먼저 가입해 둔 계정일 수 있다.
            audit.info("event=oauth_login outcome=refused_password_account user=%s provider=%s", exc.user_id, spec.name)
        elif isinstance(exc, OAuthUnverifiedLinkConflict):
            # 같은 이메일의 다른 방법(다른 소셜 로그인)으로 만든 계정에 붙이지 않았다 (이메일이 양쪽에서 확인되지 않음).
            audit.info("event=oauth_login outcome=refused_unverified_link user=%s provider=%s", exc.user_id, spec.name)
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # FE(OAuth 콜백 화면)가 이 문구의 '회원가입'·'연동' 포함 여부로 환영 안내를 고른다. 바꾸면 FE도 함께 바꾼다.
    if created:
        outcome, message = "created", f"{spec.label} OAuth 회원가입 및 로그인 완료"
    elif linked:
        outcome, message = "linked", f"기존 계정에 {spec.label} 계정 연동 및 로그인 성공"
    else:
        outcome, message = "login", f"{spec.label} OAuth 로그인 성공"
    audit.info("event=oauth_login outcome=%s user=%s provider=%s", outcome, user["id"], spec.name)
    return build_auth_response(user, message)


def _development_only() -> None:
    """GET 콜백은 BE 단독 테스트(개발)용이다. APP_ENV=production에서는 없는 경로처럼 404로 답한다.

    FE는 POST만 쓴다. GET은 code·state·code_verifier가 URL에 실려 접근 로그(ALB·CloudFront 포함)에 남고, 사전 요청 없이
    다른 사이트에서도 걸 수 있어 IP별 제한(WAF)을 비켜 간다. 쿼리 검사보다 먼저 답하도록 경로 의존성으로 둔다.
    """
    if get_settings().app_env == "production":
        raise HTTPException(status_code=404, detail="Not Found")


def _add_provider_routes(spec: _ProviderSpec, get_provider: Callable[[], OAuthProvider]) -> None:
    """한 제공자의 세 경로를 등록한다: GET /<name>/authorize, GET /<name>/callback(개발용), POST /<name>/callback.

    경로는 제공자마다 고정 문자열이다 ('/{provider}' 경로 변수를 쓰지 않는다): 본문 상한 목록(main.py)·접근 로그 가리기·
    WAF의 경로 규칙·공개 경로 목록 테스트가 모두 정확한 경로로 맞춘다. 의존성(get_provider)도 제공자마다 따로 둬서
    테스트가 app.dependency_overrides로 제공자 하나만 바꿔 끼울 수 있다.
    """

    def authorize(
        redirect_uri: Optional[str] = Query(default=None),
        code_challenge: Optional[str] = Query(default=None),
        code_challenge_method: Optional[str] = Query(default=None),
        redirect: bool = Query(default=False),
        provider: OAuthProvider = Depends(get_provider),
    ):
        return _authorize(spec, redirect_uri=redirect_uri, code_challenge=code_challenge,
                          code_challenge_method=code_challenge_method, redirect=redirect, provider=provider)

    async def callback_get(
        code: Optional[str] = Query(default=None, max_length=MAX_OAUTH_PARAM_CHARS),
        state: Optional[str] = Query(default=None, max_length=MAX_OAUTH_PARAM_CHARS),
        error: Optional[str] = Query(default=None),
        # Query를 Annotated 안에 둬야 길이 검사 다음에 형식 검사기(_clean_code_verifier)가 실행된다 (기본값 자리에 두면 빠진다).
        code_verifier: Annotated[Optional[str], Query(max_length=MAX_OAUTH_PARAM_CHARS),
                                 AfterValidator(_clean_code_verifier)] = None,
        provider: OAuthProvider = Depends(get_provider),
        custody: Optional[KeyCustody] = Depends(get_key_custody),
    ):
        if error:
            # 쿼리로 받은 값을 길이 제한 없이 되돌려 주지 않는다.
            raise HTTPException(status_code=400, detail=f"OAuth authorization failed: {error[:MAX_OAUTH_ERROR_CHARS]}")
        if not code or not code.strip() or not state or not state.strip():
            raise HTTPException(status_code=400, detail="Missing OAuth code or state")
        return await _complete_oauth(
            spec,
            code=code.strip(),
            state=state.strip(),
            redirect_uri=None,
            code_verifier=code_verifier,
            provider=provider,
            custody=custody,
        )

    async def callback_post(
        req: OAuthCallbackRequest,
        provider: OAuthProvider = Depends(get_provider),
        custody: Optional[KeyCustody] = Depends(get_key_custody),
    ):
        return await _complete_oauth(
            spec,
            code=req.code,
            state=req.state,
            redirect_uri=req.redirect_uri,
            code_verifier=req.code_verifier,
            provider=provider,
            custody=custody,
        )

    # 라우트 이름(OpenAPI operationId의 앞부분)은 예전 Google 함수 이름과 같은 꼴이다: google_authorize 등.
    router.add_api_route(f"/{spec.name}/authorize", authorize, methods=["GET"], name=f"{spec.name}_authorize")
    router.add_api_route(f"/{spec.name}/callback", callback_get, methods=["GET"], name=f"{spec.name}_callback_get",
                         dependencies=[Depends(_development_only)])
    router.add_api_route(f"/{spec.name}/callback", callback_post, methods=["POST"], name=f"{spec.name}_callback_post")


# 등록 순서(OpenAPI 문서의 순서)는 FE의 버튼·배지 순서와 같다: Google, GitHub, Naver.
for _adapter, _get_provider in ((GoogleOAuthAdapter, get_google_oauth_provider),
                                (GitHubOAuthAdapter, get_github_oauth_provider),
                                (NaverOAuthAdapter, get_naver_oauth_provider)):
    _add_provider_routes(
        _ProviderSpec(name=_adapter.provider_name, label=_adapter.label,
                      requires_verified_email=_adapter.requires_verified_email),
        _get_provider,
    )
