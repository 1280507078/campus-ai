# 校园 AI 助手 —— 生产镜像
#
# 构建：  docker build -t campus-ai .
# 运行：  docker run -p 3000:3000 \
#           -e GLM_API_KEY=... -e QDRANT_URL=... -e QDRANT_API_KEY=... -e AMAP_WEB_KEY=... \
#           campus-ai
#
# 为什么这么简单（只有一个 stage、没有 node 构建）：
#   前端是**原生多页面**（HTML + CSS + JS，零构建、零依赖），不需要 vite/webpack，
#   也不需要 Node 运行时。所以镜像里只有 Python 一层，体积小、构建快、出错面也小。
#
# ⚠️ Key 一律走环境变量，**不要把 backend/.env 打进镜像**（`.dockerignore` 已排除）。
#    镜像层是可被 `docker history` 翻出来的，密钥进了层就等于泄露。

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=3000

WORKDIR /app

# 依赖单独一层：只改业务代码时不会触发重新安装（这一步最慢）
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY frontend ./frontend

# 以非 root 运行。容器里跑 root 是老习惯，一旦有文件写入漏洞代价太大。
RUN useradd --create-home --uid 10001 campus \
 && chown -R campus:campus /app
USER campus

# main.py 会读 PORT（见 config/main 的端口优先级注释），默认 3000
WORKDIR /app/backend
EXPOSE 3000

# 健康检查直接打 /api/ping —— 这个接口就是为「区分前后端」而存在的，
# 比 curl 首页可靠：首页 200 只说明静态服务活着，不代表后端路由正常。
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/ping'%os.environ.get('PORT','3000'),timeout=4)" || exit 1

CMD ["python", "main.py"]
