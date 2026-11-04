ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS wheels
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /app/requirements.lock
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.lock

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libmariadb3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home eatful
COPY --from=wheels /wheels /wheels
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.lock \
    && rm -rf /wheels
COPY . /app
RUN DJANGO_SECRET_KEY=build-only-not-a-runtime-secret PGHOST=build.invalid \
    python manage.py collectstatic --noinput \
    && mkdir -p /app/media \
    && chown -R eatful:eatful /app/media
USER eatful
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]
CMD ["python", "scripts/container.py", "all"]
