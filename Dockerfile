FROM python:3.14-alpine

RUN apk add --no-cache curl

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline ./pipeline
COPY server ./server
COPY config ./config
COPY site ./site
COPY ops ./ops

ENV PYTHONUNBUFFERED=1 \
    PORT=8184 \
    SITE_DIR=/app/site \
    DATA_DIR=/app/data

EXPOSE 8184

CMD ["python3", "server/server.py"]
