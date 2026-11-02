#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_vec_m3 —— bge-m3 全量编码并**直写 vec_m3 表**（T1 语言路由）。

为什么不用 npy 中转（2026-11-01 纠正）：`bge-m3_59187.npy` 是旧库快照，而
chunk_id 是 SQLite rowid、不连续且每次增量 build 会 DELETE+INSERT 重排；npy 里
没存 chunk_id 映射 ⇒ 按行号直导必然张冠李戴。正确做法：加载**当前** chunks →
GPU 全量编码 → 用 (chunk_id, vec) 一一精确对应直写 vec_m3，零中转。

编码口径复用 `outputs/知识库运维/_m3_tower_eval.py` 里已验证的三函数（不改口径）：
  load_chunks  —— `SELECT ... FROM chunks c ORDER BY c.id`；文本 build_vector_text v1
  load_model   —— fp16 + cuda
  encode_texts —— CLS 池化 + L2 归一；m3 **不加** query prefix

用法：
    E:/_venvs/gpu/Scripts/python.exe -u build_vec_m3.py
    （约 30~60 分钟；写完断言 vec_m3 行数 == chunks 行数，对不上 exit 3）
"""
import argparse
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from kb_index import connect            # noqa: E402
import bge_encoder                      # noqa: E402

M3_DIR = os.environ.get("KB_M3_MODEL_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "bge-m3")
M3_MODEL_NAME = "BAAI/bge-m3 (dense 1024, CLS+L2, fp16 cuda)"


# ---------- 以下三函数复制自 _m3_tower_eval.py（不改口径） ----------
def build_vector_text(title, heading, content, mode="v1", max_chars=1000):
    """v1 口径：heading + content，不含 title，前 max_chars 字。"""
    title = (title or "").strip()
    heading = (heading or "").strip()
    content = content or ""
    parts = []
    if heading:
        parts.append(heading)
    parts.append(content)
    text = "\n".join(parts)
    if max_chars > 0:
        text = text[:max_chars]
    return text


def load_chunks(conn):
    """返回 (ids, texts)：ids 为 chunk_id 升序数组，与 texts 一一对应。"""
    rows = conn.execute(
        "SELECT c.id, c.title, c.heading, c.content "
        "FROM chunks c ORDER BY c.id").fetchall()
    ids, texts = [], []
    for cid, title, heading, content in rows:
        ids.append(int(cid))
        texts.append(build_vector_text(title, heading, content, "v1", 1000))
    return ids, texts


def load_model(model_dir, device, dtype=None):
    import torch
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(model_dir)
    mdl = AutoModel.from_pretrained(model_dir, torch_dtype=dtype)
    mdl.to(device).eval()
    return torch, tok, mdl


def encode_texts(torch, tok, mdl, texts, device, batch=32, max_len=512, tag=""):
    import numpy as np
    outs = []
    n = len(texts)
    t0 = time.time()
    for i in range(0, n, batch):
        part = texts[i:i + batch]
        enc = tok(part, padding=True, truncation=True,
                  max_length=max_len, return_tensors="pt")
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.no_grad():
            o = mdl(**enc)
        cls = o.last_hidden_state[:, 0]           # CLS pooling
        cls = torch.nn.functional.normalize(cls, p=2, dim=1)
        outs.append(cls.float().cpu().numpy())
        if (i // batch) % 20 == 0:
            el = time.time() - t0
            done = min(i + batch, n)
            print("    %s %d/%d  %.3f s/块" % (tag, done, n, el / max(done, 1)),
                  end="\r", file=sys.stderr)
    print("", file=sys.stderr)
    return np.concatenate(outs, axis=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--model-dir", default=M3_DIR)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--limit", type=int, default=0, help="调试用：只编码前 N 块（0=全量）")
    args = ap.parse_args()

    import numpy as np
    import torch

    if not torch.cuda.is_available():
        print("✗ cuda 不可用。必须用 E:/_venvs/gpu/Scripts/python.exe")
        return 2

    conn = connect(db_path=args.db)
    bge_encoder.ensure_tables(conn)
    ids, texts = load_chunks(conn)
    n_total = len(ids)
    print("[info] 载入 %d 块，模型 %s" % (n_total, args.model_dir))

    if args.limit:
        ids, texts = ids[:args.limit], texts[:args.limit]
        print("[warn] --limit=%d，只编码前 %d 块" % (args.limit, len(ids)))
    if not texts:
        print("✗ chunks 为空，先跑 kb_rag.py build")
        return 2

    lens = sorted(len(t) for t in texts)
    med = lens[len(lens) // 2]
    print("[info] 块文本长中位数 %d 字" % med)
    if med < 200:
        print("✗ 块文本中位数 %d 字，明显不对，停止（防假数据）" % med)
        return 3

    n = len(ids)
    print("[info] 开始 GPU 全量编码（fp16，batch=%d，max_len=%d）…"
          % (args.batch, args.max_len))
    t0 = time.time()
    torch_, tok, mdl = load_model(args.model_dir, "cuda", torch.float16)
    for _ in range(2):
        encode_texts(torch_, tok, mdl, texts[:min(32, n)], "cuda",
                     batch=args.batch, max_len=args.max_len, tag="预热")
    D = encode_texts(torch_, tok, mdl, texts, "cuda",
                     batch=args.batch, max_len=args.max_len, tag="全库")
    dt = time.time() - t0
    print("[info] 编码完成 %.1f 分钟，%.3f s/块（含预热/加载）"
          % (dt / 60, dt / max(n, 1)))
    assert D.shape == (n, 1024), "✗ D.shape %s 不对（期望 (%d, 1024)）" % (D.shape, n)

    # ---- 写 vec_m3（chunk_id 与向量一一对应，不经 npy）----
    if not args.limit:
        # 写前校验（全量模式）：编码期间 chunks 的 id 序列必须与编码时快照完全一致，
        # 任何 add/delete/re-chunk 都会让快照失效，拒绝写入避免张冠李戴。
        print("[info] 写前校验：编码期间 chunks 不得发生变化…")
        now_ids = [int(r[0]) for r in conn.execute(
            "SELECT id FROM chunks ORDER BY id").fetchall()]
        if now_ids != ids:
            print("✗ chunks 在编码期间发生变化（%d → %d），拒绝写入避免张冠李戴"
                  % (len(ids), len(now_ids)))
            return 3

    conn.execute("PRAGMA busy_timeout=600000")
    t1 = time.time()
    conn.execute("DELETE FROM vec_m3")
    conn.executemany(
        "INSERT OR REPLACE INTO vec_m3(chunk_id, vec) VALUES(?,?)",
        [(cid, D[i].astype("float32").tobytes()) for i, cid in enumerate(ids)])
    n_written = conn.execute("SELECT COUNT(*) FROM vec_m3").fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO m3_model(id,dim,n,model,built_at) VALUES(1,?,?,?,?)",
        (int(D.shape[1]), n_written, M3_MODEL_NAME, time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    print("[info] 写入 vec_m3 %d 行，用时 %.1fs" % (n_written, time.time() - t1))

    # ---- 最终断言（红线）：vec_m3 行数必须 == chunks 行数 ----
    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    if args.limit:
        print("[warn] --limit 模式，跳过与 chunks 总数的一致性断言")
    elif n_written != n_chunks:
        print("✗ 断言失败：vec_m3 %d != chunks %d" % (n_written, n_chunks))
        return 3
    row = conn.execute("SELECT dim,n,model,built_at FROM m3_model WHERE id=1").fetchone()
    print("✓ m3_model：dim=%d n=%d model=%s built_at=%s" % row)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
