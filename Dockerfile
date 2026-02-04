FROM python:3.13-slim AS builder

WORKDIR /app

# Build dependencies for tree-sitter native extensions
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir .

# --- Runtime stage ---
FROM python:3.13-slim

WORKDIR /app

# git is required by gristle_ingest_github (clones repos via gitpython)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/gristle /usr/local/bin/gristle

ENV GRISTLE_TRANSPORT=streamable-http \
    GRISTLE_HTTP_HOST=:: \
    GRISTLE_HTTP_PORT=8080 \
    GRISTLE_FALKORDB_HOST=localhost \
    GRISTLE_FALKORDB_PORT=6390

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://[::1]:8080/health')"

CMD ["gristle"]
