# -*- coding: utf-8 -*-
# =============================================================================
# KB RAG Engine · 生产检索服务容器镜像（W32）
# =============================================================================
# 设计三条边界（与 AGENTS §5 红线一致）：
#   ① 镜像只装「代码 + 运行时依赖」；数据库（kb.db / demo.db）与模型（BGE）
#      一律走挂载卷，**不烤进镜像层** —— 库会天天长、模型 2.3G，烤进去每次重建都是浪费，
#      而且一旦烤进去，镜像就成了「隐私语料的副本」，删不干净。
#   ② raw/ 隐私语料、个人信息、密钥由 .dockerignore 挡在构建上下文之外（不靠自觉）。
#   ③ 非 root（uid 10001）+ 只读根文件系统（见 docker-compose.yml）。
#
# 中文路径：代码目录名是 modules/知识库索引，容器内路径保持原名不改 ——
#   改路径要同步改 sys.path 与所有相对引用，风险远大于收益；正确做法是保证
#   locale/Python IO 全 UTF-8（下面 ENV 三行），Linux 文件系统本身没问题。
#
# 构建：
#   docker build -t kb-rag-engine:local .
#   docker build -t kb-rag-engine:slim --build-arg WITH_VECTOR=0 .   # 瘦身版，无 vec 链路
# =============================================================================
FROM python:3.11-slim AS base

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── 1. 互操作层依赖（REST / Web UI / 中文分词）──────────────────────────────
# 生产检索引擎 kb_rag.py 本身零第三方依赖，这里装的是 REST 服务与 jieba 的依赖。
# 🔴 jieba 缺了会静默降级 bigram，检索质量下降且不报错 —— 所以它是必需项不是可选项。
COPY requirements-demo.txt ./
RUN pip install --no-cache-dir -r requirements-demo.txt

# ── 2. 语义向量链路（可选，默认开）────────────────────────────────────────────
# bge_encoder / bge_reranker 依赖 torch + transformers，但**模型权重不进镜像**，
# 运行时由卷挂到 /app/modules/知识库索引/models（与代码里的默认路径对齐，
# 这样 bge_reranker.py 里那处硬编码 MODEL_DIR 也不用改）。
# WITH_VECTOR=0 时镜像约小 1G，此时 fused 检索会静默退化为 kw 链路（能力缺项，
# 仅在明确只要「关键词检索 + 演示」时这么构建）。
ARG WITH_VECTOR=1
ARG TORCH_SPEC=torch==2.14.1
RUN if [ "$WITH_VECTOR" = "1" ]; then \
        pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "$TORCH_SPEC" \
        && pip install --no-cache-dir "transformers>=4.40,<6" "numpy>=1.26" ; \
    else \
        echo "WITH_VECTOR=0 → 跳过 torch/transformers，镜像不含语义向量能力" ; \
    fi

# ── 3. 代码（构建上下文已由 .dockerignore 去隐私）────────────────────────────
COPY . .

# ── 4. 非 root 运行 + 运行时开关 ─────────────────────────────────────────────
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin kb \
    && chown -R 10001:10001 /app
USER 10001

# LLM 环节默认 off（AGENTS §5.11：能力必建、开关可控，凭据只走库外环境变量）
ENV KB_LLM_ENABLED=0 \
    KB_API_PORT=8788

EXPOSE 8788

# slim 镜像没有 curl，健康检查用 Python 标准库，零额外依赖
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8788/health', timeout=4).status == 200 else 1)"

# 容器内监听地址由 KB_API_HOST 控制（compose 设 0.0.0.0 让端口转发可达；
# 不设时 api_server.py 默认 127.0.0.1，本地直跑仍只绑回环）。
# healthcheck 走容器内 loopback，与绑定地址无关。
CMD ["python", "modules/知识库索引/api_server.py"]
