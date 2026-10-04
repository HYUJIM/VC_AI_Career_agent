import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
load_dotenv()

from app.api import mock_issuer, verify, user

app = FastAPI(title="VC AI Career Agent BE")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "VC AI Career Agent BE (FastAPI) is running"}

app.include_router(verify.router, prefix="/api/v1")
app.include_router(user.router, prefix="/api/v1/subjects")
app.include_router(mock_issuer.router, prefix="/api/mock-issuer")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 3000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
