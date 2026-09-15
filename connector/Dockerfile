# BizNext Connector — ставится внутри сети предприятия. Контекст сборки — корень репозитория
# (нужен контракт contracts/canonical).
FROM python:3.12.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY connector/requirements.txt ./
RUN pip install --retries 6 --timeout 180 -r requirements.txt

COPY contracts ./contracts
COPY connector/mappings ./mappings
COPY connector/biznext_connector ./biznext_connector

RUN useradd --system --uid 10003 connector
USER connector

CMD ["python", "-m", "biznext_connector", "run"]
