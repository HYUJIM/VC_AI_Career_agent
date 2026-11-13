from app.core.config import get_settings
from app.integrations.dxworks import DXWorksAdapter
from app.integrations.oauth import GoogleOAuthAdapter
from app.integrations.ports import CredentialIssuer, CredentialVerifier, OAuthProvider


def get_verifier() -> CredentialVerifier:
    """FastAPI 의존성. 테스트에서는 app.dependency_overrides로 가짜 검증기를 넣는다."""
    return DXWorksAdapter.from_settings(get_settings())


def get_issuer() -> CredentialIssuer:
    return DXWorksAdapter.from_settings(get_settings())


def get_google_oauth_provider() -> OAuthProvider:
    """FastAPI 의존성. 테스트에서는 app.dependency_overrides로 가짜 OAuth 제공자를 넣는다."""
    return GoogleOAuthAdapter.from_settings(get_settings())
