#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vector_backfill.py — 只补「缺失的向量」，不重编全库

为什么需要（2026-11-02 夜间体检发现）：
  T6 分块改造 swap 之后，增量 build 又陆续写入了新块（id > 115551），但
  chunk_bge / vec_m3 没跟着编 —— 实测 chunks 115,849 vs chunk_bge 115,097
  （缺 752）vs vec_m3 115,551（缺 1,014）。这些块 FTS5 搜得到、语义路全盲，
  是最难发现的一类退化（不报错、不告警，只是「有些内容怎么都搜不出来」）。

  🔴 全量重编要 30-60 分钟且会顺带改写全部已有向量；本脚本只补缺失项，
  已有向量一个字节都不碰 —— 最小改动，可反复跑（幂等）。

口径：严格复用生产函数，不自己另起一套
  - bge-small-zh：`bge_encoder.build_vector_text()` + `encode()`（CPU，生产同款）
  - bge-m3：v1 口径（heading+content，不含 title，前 1000 字）+ GPU fp16 CLS+L2，
           与 build_vec_m3.py 完全一致

用法：
    E:/_venvs/gpu/Scripts/python.exe -u vector_backfill.py [--dry-run]
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
import vec_tables                       # noqa: E402  #37：缺口口径的唯一来源

M3_DIR = os.environ.get("KB_M3_MODEL_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "bge-m3")


def m3_text(title, heading, content):
    """v1 口径（与 build_vec_m3.py 逐字一致，不许改）。"""
    heading = (heading or "").strip()
    content = content or ""
    parts = [heading] if heading else []
    parts.append(content)
    return ("\n".join(parts))[:1000]


def load_m3_gpu(model_dir=M3_DIR, device="cuda", dtype_name="float16"):
    import torch
    from transformers import AutoTokenizer, AutoModel
    dtype = getattr(torch, dtype_name)
    tok = AutoTokenizer.from_pretrained(model_dir)
    mdl = AutoModel.from_pretrained(model_dir, torch_dtype=dtype)
    mdl.to(device).eval()
    return torch, tok, mdl


def encode_m3_gpu(torch, tok, mdl, texts, device="cuda", batch=32, max_len=512):
    import numpy as np
    outs = []
    for i in range(0, len(texts), batch):
        part = texts[i:i + batch]
        enc = tok(part, padding=True, truncation=True,
                  max_length=max_len, return_tensors="pt")
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.no_grad():
            o = mdl(**enc)
        cls = o.last_hidden_state[:, 0]
        cls = torch.nn.functional.normalize(cls, p=2, dim=1)
        outs.append(cls.float().cpu().numpy())
    return np.concatenate(outs, axis=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只报缺口，不编码不写库")
    ap.add_argument("--skip-bge", action="store_true")
    ap.add_argument("--skip-m3", action="store_true")
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    conn = connect()
    conn.execute("PRAGMA busy_timeout=600000")

    # 🔴 #37（2026-11-03）：缺口统计与终态校验全部改用 `vec_tables`——
    # 「补写的 id 集合」与「校验的 id 集合」必须是同一个 SQL 口径，否则就是
    # 补一套、验一套：两边一起漂也没人发现（本脚本原本自己写了一份 NOT IN）。
    vec_tables.ensure(conn)
    total = vec_tables.counts(conn)["chunks"]
    miss_bge = vec_tables.missing_ids(conn, "chunk_bge")
    miss_m3 = vec_tables.missing_ids(conn, "vec_m3")
    n_miss_tri = len(vec_tables.missing_ids(conn, "chunks_trigram"))
    print("[info] chunks=%d  缺 bge=%d  缺 m3=%d  缺 trigram=%d"
          % (total, len(miss_bge), len(miss_m3), n_miss_tri))

    if not miss_bge and not miss_m3:
        print("[ok] 向量表已完整，无需补编码")
        return 0
    if args.dry_run:
        print("[dry-run] 只报缺口，不写库")
        return 0

    def texts_of(ids, fn):
        rows = conn.execute(
            "SELECT id, title, heading, content FROM chunks WHERE id IN (%s) "
            "ORDER BY id" % ",".join("?" * len(ids)), ids).fetchall()
        return [r[0] for r in rows], [fn(r[1], r[2], r[3]) for r in rows]

    # ---------- bge-small-zh ----------
    if miss_bge and not args.skip_bge:
        t0 = time.time()
        ids, texts = texts_of(miss_bge, lambda t, h, c: bge_encoder.build_vector_text(t, h, c))
        print("[bge] 编码 %d 块（生产口径 %s）…" % (len(ids), bge_encoder.vector_fingerprint()))
        vecs = bge_encoder.encode(texts, args.batch)
        conn.executemany(
            "INSERT OR REPLACE INTO chunk_bge(chunk_id, vec) VALUES(?,?)",
            [(cid, v.astype("float32").tobytes()) for cid, v in zip(ids, vecs)])
        n_now = conn.execute("SELECT COUNT(*) FROM chunk_bge").fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO bge_model(id,dim,n,model,built_at,tie_breaker) "
            "VALUES(1,?,?,?,?,?)",
            (int(vecs.shape[1]), n_now, "BAAI/bge-small-zh (ModelScope)",
             time.strftime("%Y-%m-%d %H:%M:%S"), bge_encoder.vector_fingerprint()))
        conn.commit()
        print("[bge] ✓ 补 %d 块，用时 %.1fs，现共 %d 行" % (len(ids), time.time() - t0, n_now))

    # ---------- bge-m3 ----------
    if miss_m3 and not args.skip_m3:
        import torch
        if not torch.cuda.is_available():
            print("[m3] ✗ cuda 不可用，跳过 m3 补编码（请用 E:/_venvs/gpu/Scripts/python.exe）")
            return 4
        t0 = time.time()
        torch_, tok, mdl = load_m3_gpu()
        warmed = {"done": False}

        def encode(texts):
            """vec_tables 注入的编码器：首批发一次 warmup（CUDA 首次分配很慢）。"""
            if not warmed["done"]:
                encode_m3_gpu(torch_, tok, mdl, texts[:min(8, len(texts))])
                warmed["done"] = True
            return encode_m3_gpu(torch_, tok, mdl, texts, batch=args.batch)

        # 🔴 编码口径一个字都不动：text_fn 仍是本文件的 m3_text
        #    （heading+content，前 1000 字，v1），与 build_vec_m3.py 逐字一致。
        st = vec_tables.backfill_m3(
            conn, encode, limit=0, batch=args.batch, text_fn=m3_text, log=print,
            model="BAAI/bge-m3 (dense 1024, CLS+L2, fp16 cuda)")
        print("[m3] ✓ 补 %d 块，用时 %.1fs，缺口 %d → %d"
              % (st["encoded"], time.time() - t0, st["lag_before"], st["lag_after"]))

    # ---------- 终态校验（与补写同一口径） ----------
    print("\n[verify] 四表终态")
    rep = vec_tables.diff_report(conn)
    c = rep["counts"]
    print("  chunks=%d chunk_bge=%d chunks_trigram=%d vec_m3=%d"
          % (c["chunks"], c["chunk_bge"], c["chunks_trigram"], c["vec_m3"]))
    if rep["ok"]:
        print("  ✓ 四表一致（无缺失 id）")
    else:
        print("  ⚠️ 仍有差异：缺 bge=%d 缺 m3=%d 缺 trigram=%d，请复查"
              % (rep["lag_bge"], rep["lag_m3"], rep["lag_tri"]))
    conn.close()
    return 0 if rep["ok"] else 5


if __name__ == "__main__":
    sys.exit(main())
