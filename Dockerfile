FROM python:3.14-slim

# Set timezone and non-interactive mode
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

# Install FFmpeg and dependencies for compiling cryptg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    gcc \
    python3-dev \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependencies and install to utilize Docker cache layers
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py .

# Container startup command
CMD ["python", "uploader.py"]
