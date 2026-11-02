#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reencode_m3.py —— 重切后 bge-m3 跨语塔重编码通道（对应 reencode_gpu 的 bge 部分）。

对齐 rechunk_all 管道：rechunk_all --apply 产出 chunks_new，本脚本把 chunks_new
的 m3 向量编进 vec_m3_new（不直接碰 vec_m3 正式表，swap 时原子换表）。

编码口径逐字沿用 build_vec_m3.py（单一真相源，不许改）：
  build_vector_text v1（heading+content，前 1000 字，不含 title）
  CLS 池化 + L2 归一；m3 **不加** query prefix；fp16 + cuda；batch=32；max_len=512

🔴 生产写库纪律（同 reencode_gpu）：必须 GPU venv（E:/_venvs/gpu/Scripts/python.exe），
cuda 不可用直接 SystemExit，禁止静默退化到 CPU。

用法：
  python reencode_m3.py --db <kb.db> --apply
"""
import argparse
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import torch  # 顶层 import：启动即自检 cuda，别静默退化成 CPU
from kb_index import CHUNKER_FP
import build_vec_m3 as m3  # 复用 v1 口径（build_vector_text / encode_texts / load_model）

M3_DIR = os.environ.get("KB_M3_MODEL_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "bge-m3")
M3_MODEL_NAME = "BAAI/bge-m3 (dense 1024, CLS+L2, fp16 cuda)"


def _log(msg):
    print("[reencode_m3] %s" % msg, file=sys.stderr, flush=True)


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def cmd_apply(args, conn):
    if not _table_exists(conn, "chunks_new"):
        raise SystemExit("✗ chunks_new 不存在：先跑 rechunk_all.py --apply")
    if not _table_exists(conn, "vec_m3_new"):
        raise SystemExit("✗ vec_m3_new 不存在：rechunk_all --apply 未建影子表")
    rows = conn.execute(
        "SELECT id, title, heading, content FROM chunks_new ORDER BY id").fetchall()
    if not rows:
        raise SystemExit("✗ chunks_new 为空")
    ids = [int(r[0]) for r in rows]
    texts = [m3.build_vector_text(r[1], r[2], r[3], "v1", 1000) for r in rows]
    _log("apply：载入新 %d 块，fp16 on cuda 重编码 m3…" % len(ids))
    t0 = time.time()
    torch_, tok, mdl = m3.load_model(M3_DIR, "cuda", torch.float16)
    for _ in range(2):
        m3.encode_texts(torch_, tok, mdl, texts[:min(32, len(texts))], "cuda",
                        batch=args.batch, max_len=args.max_len, tag="预热")
    D = m3.encode_texts(torch_, tok, mdl, texts, "cuda",
                        batch=args.batch, max_len=args.max_len, tag="全库")
    assert D.shape == (len(ids), 1024), "✗ D.shape %s 不对（期望 (%d, 1024)）" % (D.shape, len(ids))

    conn.execute("PRAGMA busy_timeout=600000")
    t1 = time.time()
    conn.execute("DELETE FROM vec_m3_new")
    conn.executemany(
        "INSERT INTO vec_m3_new(chunk_id, vec) VALUES(?,?)",
        [(cid, D[i].astype("float32").tobytes()) for i, cid in enumerate(ids)])
    n = conn.execute("SELECT COUNT(*) FROM vec_m3_new").fetchone()[0]
    conn.commit()
    _log("apply 完成：写 vec_m3_new %d 块 × %d 维，%.1fs（%.0f 块/秒）" %
         (n, D.shape[1], time.time() - t0, len(ids) / max(time.time() - t0, 1e-6)))

    # 终态断言（红线）：vec_m3_new 行数必须 == chunks_new 行数
    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks_new").fetchone()[0]
    if n != n_chunks:
        _log("✗ 断言失败：vec_m3_new %d != chunks_new %d" % (n, n_chunks))
        return 3
    _log("✓ vec_m3_new 行数 == chunks_new 行数（%d）" % n)
    return 0


def main():
    ap = argparse.ArgumentParser(description="重切后 bge-m3 重编码（chunks_new→vec_m3_new，fp16 cuda）")
    ap.add_argument("--db", required=True, help="kb.db 绝对路径（🔴 显式指定，找不到即 SystemExit，禁止 fallback）")
    ap.add_argument("--model-dir", default=M3_DIR, help="bge-m3 模型目录")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--apply", action="store_true", required=True,
                   help="全量重编码 chunks_new → vec_m3_new（fp16 on cuda）")
    args = ap.parse_args()

    print("[env] torch=%s cuda_available=%s device_count=%d" %
          (torch.__version__, torch.cuda.is_available(), torch.cuda.device_count()),
          file=sys.stderr, flush=True)
    if not torch.cuda.is_available():
        raise SystemExit("✗ CUDA 不可用：reencode_m3 必须跑 GPU venv（torch +cu126），不要用 CPU venv")
    if not os.path.exists(args.db):
        raise SystemExit("✗ --db 路径不存在：%s（显式指定，禁止 fallback）" % args.db)

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        return cmd_apply(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
