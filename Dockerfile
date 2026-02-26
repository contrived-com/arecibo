FROM python:3.12-slim

ARG GIT_COMMIT=dev
ENV GIT_COMMIT=${GIT_COMMIT}
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir -r /app/api/requirements.txt

COPY schemas /app/schemas
COPY api/src /app/api/src

WORKDIR /app/api
EXPOSE 8080

CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8080"]
