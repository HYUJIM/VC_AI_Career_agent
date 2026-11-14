# 운영 이미지 (ECS 등): 비루트(uid/gid 10001), 리로더 없음, 실행에 필요한 파일만 담는다.
# 로컬 개발은 docker-compose.yml이 command(--reload)·user(root)·소스 바인드 마운트로 덮어쓴다.
FROM python:3.11.15-slim-bookworm

WORKDIR /app

# 실행 사용자 (runAsNonRoot 같은 검사도 확인할 수 있게 숫자 uid/gid로 고정)
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

# 패키지 캐싱 최적화를 위해 requirements.txt(버전 고정 목록) 먼저 복사
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 실행에 필요한 파일만 복사한다 (.venv·tests·시드·csv 제외). DB/는 마이그레이션 실행기(python -m app.migrate)가 쓴다.
COPY main.py ./
COPY app ./app
COPY DB/schema ./DB/schema
COPY DB/migrations ./DB/migrations

# APP_ENV=production이면 필수 비밀값(JWT_SECRET 등)이 없을 때 서버가 뜨지 않는다. 로컬 Compose는 development로 덮어쓴다.
ENV APP_ENV=production \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001

# 포트 노출 (기본 3000)
EXPOSE 3000

# /health는 DB를 보지 않는 생존 확인용이다 (curl 없이 python으로 확인).
# HTTP_PROXY 같은 프록시 환경변수가 있어도 루프백으로 바로 가도록 ProxyHandler를 비운다 (AI 이미지와 같은 방식).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request as r; r.build_opener(r.ProxyHandler({})).open('http://127.0.0.1:3000/health', timeout=4)"]

# 운영 기본값: 리로더 없이 실행한다. 개발 핫 리로드는 docker-compose.yml의 command로 켠다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]
