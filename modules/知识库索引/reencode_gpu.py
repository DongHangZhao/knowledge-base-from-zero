#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reencode_gpu.py —— T6 GPU 重编码通道（§5.2）。

复用 `outputs/知识库运维/2026-10-30-gpu_reencode_ab.py` 已验证的口径：
  build_vector_text(v1) / max_chars=1000 / CLS pooling / L2 归一 / fp16 实验通道。
并修掉原脚本两个坑：
  ① DB 路径三级 fallback 会静默指向不存在的路径 → 本脚本 **显式 --db，找不到即
     SystemExit，禁止 fallback**。
  ② 顶层 import torch 无自检 → 启动即打印 torch.__version__ + cuda.is_available()
     到 stderr 并落日志，cuda 不可用直接 SystemExit。

🔴 生产写库纪律（§5.3）：
  - `--apply` 写 chunk_bge_new **只允许 fp32 on cuda**；fp16 只用于 `--validate` 实验。
  - `--validate` 闸门：与库内 chunk_bge 生产向量逐块余弦 **min ≥ 0.9999**。
    余弦≈1 在数值上意味着 D_gpu≈D_db，best_rank 必然一致（故不单独再比 best_rank）。

用法（必须 GPU venv：E:/_venvs/gpu/Scripts/python.exe -u）：
  # 0) 口径零偏差自检（旧 chunks 重编码 vs 旧 chunk_bge，须 min≥0.9999）
  python reencode_gpu.py --db <kb.db> --validate
  # 1) 全量重编码新分块 → chunk_bge_new（rechunk_all.py --apply 之后跑）
  python reencode_gpu.py --db <kb.db> --apply
"""
import argparse
import os
import sqlite3
import sys
import time

# 🔴 环境自检（修坑②）：顶层 import torch，启动即自检 cuda，别静默退化成 CPU。
import torch  # noqa: E402

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
KB_INDEX_DIR = HERE   # 本脚本就在模块目录里，HERE 即知识库索引目录
sys.path.insert(0, KB_INDEX_DIR)

from kb_index import CHUNKER_FP  # noqa: E402  单一真相源（向量口径指纹要用）

QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
MODEL_DIR = os.environ.get("KB_BGE_MODEL_DIR") or os.path.join(KB_INDEX_DIR, "models", "bge-small-zh")


def _log(msg):
    print("[reencode] %s" % msg, file=sys.stderr, flush=True)


def build_vector_text(title, heading, content, mode="v1", max_chars=1000):
    """照抄 bge_encoder.build_vector_text（v1 与现状全量路径等价）。"""
    mode = (mode or "v1").strip().lower()
    if mode not in ("v1", "v2", "v3"):
        mode = "v1"
    max_chars = 1000 if max_chars is None else int(max_chars)
    title = (title or "").strip()
    heading = (heading or "").strip()
    content = content or ""
    parts = []
    if mode == "v3":
        if title and len(title) <= 30:
            parts.append(title)
        if title:
            parts.append(title)
    elif mode == "v2":
        if title:
            parts.append(title)
    if heading:
        parts.append(heading)
    parts.append(content)
    text = "\n".join(parts)
    if max_chars > 0:
        text = text[:max_chars]
    return text


def load_model(model_dir, device, dtype=None):
    from transformers import AutoTokenizer, AutoModel
    if not os.path.isdir(model_dir):
        raise SystemExit("模型目录不存在: %s" % model_dir)
    tok = AutoTokenizer.from_pretrained(model_dir)
    mdl = AutoModel.from_pretrained(model_dir, torch_dtype=dtype) if dtype is not None \
        else AutoModel.from_pretrained(model_dir)
    mdl.to(device).eval()
    return tok, mdl


def encode_texts(tok, mdl, texts, device, batch=128, is_query=False, max_len=512):
    import numpy as np
    if is_query:
        texts = [QUERY_PREFIX + t for t in texts]
    outs = []
    n = len(texts)
    for i in range(0, n, batch):
        chunk = texts[i:i + batch]
        enc = tok(chunk, padding=True, truncation=True, max_length=max_len, return_tensors="pt")
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.no_grad():
            o = mdl(**enc)
        cls = o.last_hidden_state[:, 0]          # CLS pooling（与生产口径一致）
        cls = torch.nn.functional.normalize(cls, p=2, dim=1)   # L2 归一
        outs.append(cls.cpu().numpy().astype("float32"))
        if (i // batch) % 5 == 0:
            print("    encode %d/%d" % (min(i + batch, n), n), end="\r", file=sys.stderr)
    print("", file=sys.stderr)
    return np.concatenate(outs, axis=0)


def _load_rows(conn, table):
    return conn.execute(
        "SELECT id, title, heading, content FROM %s ORDER BY id" % table).fetchall()


def _read_prod_vectors(conn):
    import numpy as np
    rows = conn.execute("SELECT chunk_id, vec FROM chunk_bge ORDER BY chunk_id").fetchall()
    ids = [int(c) for c, _ in rows]
    vecs = [np.frombuffer(v, dtype="float32") for _, v in rows]
    return ids, vecs


def cmd_validate(args, conn, tok, mdl, device):
    import numpy as np
    rows = _load_rows(conn, "chunks")
    if not rows:
        raise SystemExit("✗ chunks 为空，无法 validate")
    ids = [int(r[0]) for r in rows]
    texts = [build_vector_text(r[1], r[2], r[3]) for r in rows]
    _log("validate：载入旧 %d 块，fp32 on %s 重编码…" % (len(ids), device))
    t0 = time.time()
    D = encode_texts(tok, mdl, texts, device, batch=args.batch)
    prod_ids, prod_vecs = _read_prod_vectors(conn)
    _log("库内生产向量 %d 块" % len(prod_ids))
    if set(prod_ids) != set(ids):
        _log("⚠️ chunk_bge 的 chunk_id 与 chunks 不一致（%d vs %d），按交集比" % (len(prod_ids), len(ids)))
    pid2vec = dict(zip(prod_ids, prod_vecs))
    cos = []
    miss = 0
    for cid, v in zip(ids, D):
        pv = pid2vec.get(cid)
        if pv is None:
            miss += 1
            continue
        a = v / (np.linalg.norm(v) + 1e-9)
        b = pv / (np.linalg.norm(pv) + 1e-9)
        cos.append(float(a @ b))
    cos = np.array(cos)
    _log("validate 完成，%.1fs（%.0f 块/秒）" % (time.time() - t0, len(ids) / max(time.time() - t0, 1e-6)))
    print("[validate] 缺失 %d 块" % miss, file=sys.stderr)
    print("[validate] 逐块余弦 min=%.6f mean=%.6f max=%.6f" % (cos.min(), cos.mean(), cos.max()),
          file=sys.stderr)
    if cos.min() < 0.9999:
        print("[validate] ✗ 余弦 min < 0.9999：编码口径与库内生产向量不一致，禁止 proceed",
              file=sys.stderr)
        return 1
    print("[validate] ✅ 编码口径零偏差（余弦 min ≥ 0.9999 ⇒ best_rank 必然一致）",
          file=sys.stderr)
    return 0


def cmd_apply(args, conn, tok, mdl, device, dtype_desc):
    import numpy as np
    # 生产写库只允许 fp32 on cuda
    if device != "cuda" or dtype_desc != "fp32":
        raise SystemExit("✗ 生产写库只允许 fp32 on cuda（当前 device=%s dtype=%s）" % (device, dtype_desc))
    if not _table_exists(conn, "chunks_new"):
        raise SystemExit("✗ chunks_new 不存在：先跑 rechunk_all.py --apply")
    rows = _load_rows(conn, "chunks_new")
    if not rows:
        raise SystemExit("✗ chunks_new 为空")
    ids = [int(r[0]) for r in rows]
    texts = [build_vector_text(r[1], r[2], r[3]) for r in rows]
    _log("apply：载入新 %d 块，fp32 on cuda 重编码…" % len(ids))
    t0 = time.time()
    D = encode_texts(tok, mdl, texts, device, batch=args.batch)
    conn.execute("DELETE FROM chunk_bge_new")
    conn.executemany(
        "INSERT INTO chunk_bge_new(chunk_id, vec) VALUES(?,?)",
        [(cid, v.astype("float32").tobytes()) for cid, v in zip(ids, D)])
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM chunk_bge_new").fetchone()[0]
    _log("apply 完成：写 chunk_bge_new %d 块 × %d 维，%.1fs（%.0f 块/秒）" %
         (n, D.shape[1], time.time() - t0, len(ids) / max(time.time() - t0, 1e-6)))
    print("[apply] chunk_bge_new=%d 块，dim=%d" % (n, D.shape[1]), file=sys.stderr)
    return 0


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def main():
    ap = argparse.ArgumentParser(description="T6 GPU 重编码通道（small-zh，CLS+L2，fp32 on cuda）")
    ap.add_argument("--db", required=True,
                    help="kb.db 绝对路径（🔴 显式指定，找不到即 SystemExit，禁止 fallback）")
    ap.add_argument("--model-dir", default=MODEL_DIR, help="bge-small-zh 模型目录")
    ap.add_argument("--batch", type=int, default=128)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--validate", action="store_true",
                   help="口径零偏差自检：旧 chunks 重编码 vs 旧 chunk_bge（余弦 min≥0.9999）")
    g.add_argument("--apply", action="store_true",
                   help="全量重编码 chunks_new → chunk_bge_new（fp32 on cuda）")
    ap.add_argument("--fp16", action="store_true",
                   help="实验用 fp16（仅 --validate 允许；--apply 生产写库强制 fp32）")
    args = ap.parse_args()

    # 🔴 环境自检（修坑②）：cuda 不可用直接退，绝不在 CPU venv 里静默退化
    print("[env] torch=%s cuda_available=%s device_count=%d" %
          (torch.__version__, torch.cuda.is_available(), torch.cuda.device_count()),
          file=sys.stderr, flush=True)
    if not torch.cuda.is_available():
        raise SystemExit("✗ CUDA 不可用：reencode 必须跑 GPU venv（torch +cu126），不要用 CPU venv")
    device = "cuda"
    print("[env] device=%s name=%s" % (device, torch.cuda.get_device_name(0)), file=sys.stderr)

    # 🔴 DB 路径（修坑①）：显式 --db，找不到即退，禁止 fallback
    if not os.path.exists(args.db):
        raise SystemExit("✗ --db 路径不存在：%s（显式指定，禁止 fallback）" % args.db)

    dtype_desc = "fp16" if args.fp16 else "fp32"
    if args.apply and args.fp16:
        raise SystemExit("✗ --apply 生产写库只允许 fp32 on cuda，去掉 --fp16")

    dtype = torch.float16 if args.fp16 else torch.float32
    _log("加载模型 %s（%s）…" % (args.model_dir, dtype_desc))
    tok, mdl = load_model(args.model_dir, device, dtype)
    _log("模型已就绪，chunker_fp=%s" % CHUNKER_FP)

    # 生产写库连接：WAL + 长 busy_timeout（与其他写库进程互斥）
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        if args.validate:
            return cmd_validate(args, conn, tok, mdl, device)
        return cmd_apply(args, conn, tok, mdl, device, dtype_desc)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
