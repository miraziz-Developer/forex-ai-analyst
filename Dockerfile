FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5000

WORKDIR /app

# Dependencies first so code-only changes rebuild quickly.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY app.py ./

RUN useradd --create-home --uid 10001 trader
USER trader

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"5000\")}/health', timeout=4)"

# One process only: the APScheduler jobs run in-process, so multiple
# workers would duplicate every scan and order.
CMD ["python", "app.py"]
