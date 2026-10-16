FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    HOME=/tmp/ccdc-home \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt \
    && groupadd --gid 10001 ccdc \
    && useradd --uid 10001 --gid ccdc --no-create-home --home-dir /tmp/ccdc-home ccdc \
    && mkdir -p /tmp/ccdc-home \
    && chown 10001:10001 /tmp/ccdc-home

COPY app.py core.py ui_labels.py ./
COPY services/ ./services/
# Uploaded checkouts may have 0600 files/0700 directories. Keep code root-owned
# while making it readable/traversable by the unprivileged runtime account.
RUN chmod -R a+rX /app

USER 10001:10001
EXPOSE 8080
CMD ["/bin/sh", "-c", "exec python -m streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8080} --server.headless=true --browser.gatherUsageStats=false"]
