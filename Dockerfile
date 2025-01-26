# 使用更小的 Alpine 镜像
FROM python:3.9-alpine

# 设置工作目录
WORKDIR /app

# 将脚本复制到容器
COPY ./s2s_server.py /app/s2s_server.py

# 安装依赖
RUN pip install --no-cache-dir pysocks

# 设置环境变量的默认值（可以通过 Docker 启动时传入新的值）
ENV SOCKS5_USERNAME username
ENV SOCKS5_PASSWORD password
ENV TS_SOCKS5_HOST 127.0.0.1
ENV TS_SOCKS5_PORT 1055
ENV INBOUND_PORT 45675

# 公开端口
EXPOSE 45675

# 启动程序
CMD ["python", "/app/s2s_server.py"]
