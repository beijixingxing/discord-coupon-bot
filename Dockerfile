# 使用官方的 Python 3.10 slim 镜像作为基础
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖
# --no-cache-dir: 不存储缓存，减小镜像体积
# --trusted-host pypi.python.org: 解决部分网络环境下可能出现的SSL问题
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制所有项目文件到工作目录
COPY . .

# 容器启动时执行的命令
CMD ["python", "main.py"]