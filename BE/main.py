import logging
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from app.api import auth, career, mock_issuer, portfolios, reverify, shares, user, verify  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.integrations.ports import ProviderError  # noqa: E402

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("vc-be")

app = FastAPI(title="VC AI Career Agent BE")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    # 인증은 Authorization 헤더(Bearer)로 하므로 쿠키 자격증명은 쓰지 않는다.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Service-Key"],
)


@app.exception_handler(ProviderError)
async def provider_error_handler(_: Request, exc: ProviderError):
    logger.warning("Provider error: %s (upstream=%s)", exc.message, exc.upstream_status)
    return JSONResponse(status_code=503 if exc.unavailable else 502, content={"detail": exc.message})


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    # 내부 예외 메시지(SQL, 스택 등)는 응답에 싣지 않고 로그로만 남긴다.
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
def health_check():
    return {"status": "ok", "message": "VC AI Career Agent BE (FastAPI) is running"}


app.include_router(verify.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1/users")
app.include_router(user.router, prefix="/api/v1/subjects")
app.include_router(mock_issuer.router, prefix="/api/mock-issuer")
app.include_router(reverify.router, prefix="/api/v1")
app.include_router(portfolios.router, prefix="/api/v1/portfolios")
app.include_router(shares.router, prefix="/api/v1/shares")
app.include_router(shares.public_router, prefix="/api/v1/public/shares")
app.include_router(career.router, prefix="/api/v1")

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 3000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
