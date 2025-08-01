# --- 阶段 1: 构建器 ---
# 使用一个包含完整构建工具的镜像来安装依赖
FROM python:3.9 as builder

# 设置工作目录
WORKDIR /app

# 创建并激活虚拟环境
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# --- 阶段 2: 运行环境 ---
# 使用一个轻量级的 slim 镜像作为最终的运行环境
FROM python:3.9-slim

# 创建一个非 root 用户和组
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser

# 设置工作目录
WORKDIR /app

# 从构建器阶段复制已安装好的虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 复制所有项目文件到工作目录
COPY . .

# 将工作目录的所有权交给新用户
RUN chown -R appuser:appgroup /app

# 复制并设置入口点脚本
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 激活虚拟环境，以便 python 命令能找到安装的包
ENV PATH="/opt/venv/bin:$PATH"

ENTRYPOINT ["/entrypoint.sh"]
# 定义容器启动时要执行的默认命令 (会被传递给入口点)
CMD ["python", "main.py"]