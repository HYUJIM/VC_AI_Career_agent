# 기본 이미지는 BE와 같은 고정 태그를 쓴다 (Python 3.11.15, Debian bookworm).
FROM python:3.11.15-slim-bookworm

# 코드 폴더에 .pyc를 쓰지 않고(실행 사용자는 쓰기 권한이 없다), 로그는 버퍼 없이 바로 stdout으로 보낸다.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 실행 전용 사용자(uid/gid 10001). 코드와 패키지는 root 소유로 두고 읽기만 허용한다.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-log-init --create-home --shell /usr/sbin/nologin app

# GPU 패키지(requirements-gpu.txt)는 넣지 않는다. 로컬 Qwen은 GPU PC에서 직접 실행한다 (README 6번).
# 테스트 도구(requirements-dev.txt)도 넣지 않는다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

USER 10001:10001

EXPOSE 8100
# 프로세스가 응답하는지만 본다(/health는 인증 없음). 프록시 환경변수를 타지 않도록 ProxyHandler를 비운다.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request as r; sys.exit(0 if r.build_opener(r.ProxyHandler({})).open('http://127.0.0.1:8100/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "src.server.app:app", "--host", "0.0.0.0", "--port", "8100"]
