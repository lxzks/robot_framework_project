# 车载自动化测试环境（CI / 本地一致的"一条命令"运行环境）
#
# 关键设计：
#   1. 固定 Python 3.12（与 CI 主版本一致），避免"我这能跑"式环境差异；
#   2. 装 PySide6 运行库（libgl1/libegl1）——headless 测试其实不需要 Qt，
#      但装全依赖后 GUI 模拟器（dashboard/android_gui）也能在同一镜像里跑；
#   3. 只 COPY 项目文件（.dockerignore 已排除 .venv/.git/results 等），
#      镜像可复现且体积小；
#   4. 默认 CMD 跑全量 Robot Framework 套件（276 用例，会自动拉起仿真进程）。
#
# 构建 / 运行（不要在本机缺 Docker 时尝试 build；CI/GHCR 上构建更合适）：
#   docker build -t vehicle-test-framework:latest .
#   docker run --rm vehicle-test-framework:latest
#   docker run --rm vehicle-test-framework:latest python run_tests.py --report
#   docker run --rm -v "$PWD/results:/app/results" vehicle-test-framework:latest python run_tests.py --report
#   # 某些平台 UDP 组播出容器需要 host 网络：
#   docker run --rm --network host vehicle-test-framework:latest
FROM python:3.12-slim

# Python 运行环境：不写 .pyc、日志实时输出、UTF-8（Windows 宿主机日志也友好）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONIOENCODING=utf-8

# PySide6 运行库（本项目 GUI 模拟器需要；headless 测试可省但代价很小）
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libegl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖再拷代码：依赖不变时可复用 Docker 层缓存
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt \
    # pytest 用于 tools/pytest_style 的单元测试（本地 venv 已自带；镜像内补装）
    && pip install pytest

COPY . .

# 默认入口：跑全量 RF 套件（276 用例；自动拉起 ECU/仪表/车机仿真进程）
CMD ["python", "run_tests.py"]
