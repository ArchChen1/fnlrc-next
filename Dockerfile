# fnlrc 网页版 / NAS 部署
#
#   docker build -t fnlrc .
#   docker run -d --name fnlrc -p 8080:8080 \
#     -v /vol2/1000/音乐:/music \
#     -v fnlrc-data:/data \
#     fnlrc
#
# API key 走环境变量注入（**别用 -e KEY=...，那会留在 shell history 和 docker inspect**）：
#   docker run ... --env-file fnlrc.env fnlrc
#   文件内容：FNLRC_REMOTE_API_KEY=sk-xxxx
#
# 启动时会把访问令牌打到日志里，浏览器第一次打开要填：
#   docker logs fnlrc 2>&1 | grep -i token

FROM python:3.12-slim

# 时区与字符集：中文路径/日文歌名要一路 UTF-8
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    TZ=Asia/Shanghai \
    FNLRC_DATA=/data

WORKDIR /app

# 先装依赖再拷代码：改一行 Python 不会让依赖层失效
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fnlrc/ ./fnlrc/

# 任务历史/配置/令牌都落在挂载卷里，容器重建不丢
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8080

# 容器里必须绑 0.0.0.0 才能被访问；令牌是唯一的防线，启动日志里会给
CMD ["python", "-m", "fnlrc", "--server", "--host", "0.0.0.0", "--port", "8080"]
