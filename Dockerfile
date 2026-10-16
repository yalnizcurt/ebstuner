FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STUDIO_DB_PATH=/app/.studio/projects.sqlite3
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home studio
COPY *.py index.html ./
COPY static ./static
COPY fixtures/oracle ./fixtures/oracle
RUN mkdir .studio && chown studio:studio .studio
USER studio
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)"
CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--workers", "1"]
