FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
WORKDIR /app
RUN groupadd --gid 10001 fraudguard && useradd --uid 10001 --gid fraudguard --no-create-home fraudguard
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml /app/
COPY src /app/src
COPY scripts /app/scripts
RUN pip install --no-cache-dir --no-deps .
COPY artifacts/benchmark /app/artifacts/benchmark
COPY artifacts/behavioral_service /app/artifacts/behavioral_service
RUN mkdir -p /data /backups && chown 10001:10001 /data /backups
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"
CMD ["python", "-m", "fraudguard.server"]
