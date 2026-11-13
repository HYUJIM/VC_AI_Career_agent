import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.api.auth import build_auth_response, issue_user_identity
from app.core.auth import ALGORITHM
from app.core.config import get_settings
from app.integrations import get_google_oauth_provider
from app.integrations.ports import OAuthCodeError, OAuthProvider
from app.services.database import OAuthAccountConflict, db_service
from app.services.key_custody import KeyCustody, get_key_custody

router = APIRouter()

_STATE_TTL_SECONDS = 600


class OAuthCallbackRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)
    redirect_uri: Optional[str] = None
    code_verifier: Optional[str] = None

    @field_validator("code", "state")
    @classmethod
    def strip_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


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
        if not code_challenge.strip():
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

    try:
        user_info = await provider.exchange_code(
            code=code,
            redirect_uri=state_redirect_uri,
            code_verifier=code_verifier.strip() if code_verifier and code_verifier.strip() else None,
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
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if created:
        message = "Google OAuth 회원가입 및 로그인 완료"
    elif linked:
        message = "기존 계정에 Google 계정 연동 및 로그인 성공"
    else:
        message = "Google OAuth 로그인 성공"
    return build_auth_response(user, message)


@router.get("/google/callback")
async def google_callback_get(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    code_verifier: Optional[str] = Query(default=None),
    provider: OAuthProvider = Depends(get_google_oauth_provider),
    custody: Optional[KeyCustody] = Depends(get_key_custody),
):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth authorization failed: {error}")
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
