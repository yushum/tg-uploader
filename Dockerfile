# Builder stage
FROM python:3.14-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# Final stage
FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libjemalloc2 \
    && rm -rf /var/lib/apt/lists/* \
    && find /usr/lib/ -name "libjemalloc.so.2" -exec ln -s {} /usr/local/lib/libjemalloc.so \;

ENV LD_PRELOAD=/usr/local/lib/libjemalloc.so \
    MALLOC_CONF=background_thread:true,dirty_decay_ms:1000,muzzy_decay_ms:1000

WORKDIR /app

COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache /wheels/*

COPY *.py .

CMD ["python", "uploader.py"]
