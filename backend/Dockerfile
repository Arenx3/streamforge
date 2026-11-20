FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN sed -i 's/\r$//' entrypoint.sh \
    && chmod +x entrypoint.sh \
    && mkdir -p /app/staticfiles \
    && chown -R app:app /app

USER app
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --timeout 120 --access-logfile - --error-logfile -"]
