FROM python:3.11-slim

WORKDIR /app

# GPU 패키지(requirements-gpu.txt)는 넣지 않는다. 로컬 Qwen은 GPU PC에서 직접 실행한다 (README 6번).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

EXPOSE 8100
CMD ["uvicorn", "src.server.app:app", "--host", "0.0.0.0", "--port", "8100"]
