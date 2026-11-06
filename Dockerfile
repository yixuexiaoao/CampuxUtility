FROM python:3.10-slim

LABEL org.opencontainers.image.version="2.4.1"

# 浏览器装到公共目录，降权后的运行用户也能使用
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1. 安装系统级中文字体与彩色 Emoji 字体，避免容器内渲染中文/Emoji 显示豆腐块(□)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-microhei \
    fonts-wqy-zenhei \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# 2. 只拷贝依赖清单文件
COPY requirements.txt .

# 3. 安装 Python 依赖（只要 requirements.txt 不变，这一步永远使用缓存）
RUN pip install --no-cache-dir -r requirements.txt

# 4. 安装 Playwright 浏览器及其系统依赖（需要 root，必须在降权之前）
RUN playwright install --with-deps chromium

# 5. 创建非 root 运行用户：Chromium 以 --no-sandbox 运行，降权可以限制渲染进程被攻破后的影响范围
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin t2i \
    && mkdir -p /app/data \
    && chown t2i:t2i /app /app/data

# 6. 拷贝剩余所有源代码（代码属主仍为 root，运行用户只读；只有 data/ 可写）
COPY . .

USER t2i

EXPOSE 8999

CMD ["python", "main.py"]
