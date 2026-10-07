FROM python:3.11-slim

WORKDIR /app

# 기본 유틸 설치 및 불필요한 패키지 캐시 정리
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 패키지 캐싱 최적화를 위해 requirements.txt 먼저 복사
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 전체 소스 코드 복사
COPY . .

# 포트 노출 (기본 3000)
EXPOSE 3000

# 개발 환경 핫 리로드 지원
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000", "--reload"]
