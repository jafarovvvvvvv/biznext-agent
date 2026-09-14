FROM python:3.12.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /seed
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 6 --timeout 180 -r requirements.txt

COPY reference.py generate.py ./

RUN useradd --create-home --uid 10001 seed
USER seed

CMD ["python", "generate.py"]
