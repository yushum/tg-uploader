FROM python:3.14-slim

# 设置时区和非交互模式
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

# 安装 FFmpeg 以及编译 cryptg 所需的依赖环境
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    gcc \
    python3-dev \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先拷贝依赖文件并安装，利用 Docker 缓存层加速构建
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝核心业务脚本
COPY *.py .

# 容器启动命令
CMD ["python", "uploader.py"]
