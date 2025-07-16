# 使用一个轻量级的 Python 官方镜像作为基础
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装依赖
# 这样做可以利用 Docker 的层缓存机制，只有在 requirements.txt 改变时才重新安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制所有项目文件到工作目录
COPY . .

# 定义容器启动时要执行的命令
CMD ["python", "main.py"]