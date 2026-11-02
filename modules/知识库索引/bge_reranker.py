#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bge_reranker — Cross-Encoder 精排层（BGE-reranker-v2-m3）
=========================================================

为什么加（2026 生产级 RAG 金标准：Hybrid 检索 → RRF 融合 → rerank 精排 → top-k，
Cohere / OpenAI cookbook / RAGFlow 同款架构）：
  RRF 只按名次融合——两条链路都排前面的文档就靠前，但它看不出
  「关键词都对、答非所问」的擦边文档（kw 命中「大模型」、vec 命中「瞎编」，
  拼出来可能是一篇讲炒股的文章）。
  Cross-Encoder 把 query 和文档**联合编码**过一遍，直接出相关性分，
  是开源自建的质量头名；只对融合池 top-N（默认 30）打分，
  0.23s/对 × 30 ≈ 7s，延迟可控。
  BGE-reranker-v2-m3 本地推理，数据不出本机——隐私红线内做质量。

用法：
  python bge_reranker.py --selftest     # 两对合成句子自测（相关 > 无关 + 0.3）

kb_rag.search_unified 在 RRF 融合后、_enrich 前调用本模块的 rerank()：
文本取值链 chunk_text → summary → title 必须在 _enrich pop 掉这些字段之前做。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))

MODEL_DIR = os.path.join(HERE, "models", "bge-reranker-v2-m3")
BATCH = 16                     # 批推理大小（CPU）：30 条池子两批跑完，8 线程无压力
BATCH_GPU = 32                 # GPU 上可以更大批；fp16 + 32 条约占 2-3GB 显存
_MODEL_CACHE = {}              # 单例：加载一次 19s，同进程后续调用零成本


def load_model():
    """lazy 加载单例：AutoTokenizer + AutoModelForSequenceClassification。
    线程数走 KB_RERANK_THREADS（默认 8），eval 模式（关 dropout，分数才稳定）。

    🔴 device（2026-11-02 补）：此前**只在 CPU 跑**，实测 30 文档精排要 22–23 秒
    （0.77s/文档），而任何 >4 字的查询都会走精排 —— 相当于用户每次长查询等 20 秒以上。
    改为默认 auto（有 CUDA 就上 GPU + fp16），可用 KB_RERANK_DEVICE=cpu 一键回退。
    模型与权重不变，只是换算力；fp16 的分差在 1e-3 量级，不影响排序。
    """
    if "m" in _MODEL_CACHE:
        return _MODEL_CACHE["m"]
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    if not os.path.isdir(MODEL_DIR) or not os.path.isfile(os.path.join(MODEL_DIR, "config.json")):
        raise SystemExit(
            "reranker 模型不存在：把 bge-reranker-v2-m3（含 config.json / model.safetensors / "
            "tokenizer.json / sentencepiece.bpe.model）整个目录放到 %s；"
            "缺模型时精排会自动降级跳过，不影响三链路检索" % MODEL_DIR)
    dev = (os.environ.get("KB_RERANK_DEVICE") or "auto").strip().lower()
    if dev == "auto":
        dev = "cuda" if torch.cuda.is_available() else "cpu"
    if dev == "cuda" and not torch.cuda.is_available():
        dev = "cpu"
    if dev == "cpu":
        torch.set_num_threads(int(os.environ.get("KB_RERANK_THREADS") or "8"))
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR)
    if dev == "cuda":
        mdl = mdl.half().to("cuda")
    mdl.eval()
    _MODEL_CACHE["m"] = (torch, tok, mdl, dev)
    return _MODEL_CACHE["m"]


def _key_of(it):
    """融合池条目按 doc_id 认领（缺 doc_id 时退 chunk_id）——调用方据此重排。"""
    k = it.get("doc_id")
    if k is None:
        k = it.get("chunk_id")
    return k


def _text_of(it):
    """重排文本取值链：chunk_text → summary → title → 空。
    必须在调用方 _enrich pop 掉 chunk_text/summary 之前调用（kb_rag 已保证顺序）。

    B2：这条链**原样保留作兜底**——kb_rag 拿到命中块后会把它写进 chunk_text，
    拿不到时仍然回落到 summary，不因为修输入而让整条链失效。"""
    return it.get("chunk_text") or it.get("summary") or it.get("title") or ""


def _pairs_of(query, items):
    """展开 (key, text) 打分对。

    条目带 `rerank_texts`（B2 滑窗后的多段正文）时，**每段各出一对**，
    后面按 key 取 max —— 长块不再靠截断丢信息，也不用放宽 max_length
    （512 是 CPU 推理成本红线，放宽到 8192 会让延迟涨一个数量级）。
    没有 rerank_texts 的条目走原单文本取值链，行为与修前逐位一致。"""
    out = []
    for it in items:
        k = _key_of(it)
        if k is None:
            continue
        texts = [t for t in (it.get("rerank_texts") or []) if t]
        if texts:
            for t in texts:
                out.append((k, t))
        else:
            out.append((k, _text_of(it)))
    return out


def rerank(query, items, topk, max_length=512):
    """Cross-Encoder 精排：query+文档联合编码，sigmoid 出相关性分。

    items: list[dict]，至少含 doc_id（或 chunk_id）与文本字段
           （chunk_text / summary / title 任一）；
           可选 `rerank_texts`: list[str]，滑窗多段正文（各段打分后取 max）。
    返回 [(key, score), ...] 按分数降序、截到 topk；
    模型缺失或推理异常 → 返回 None（让调用方优雅降级记 note，不崩）。
    """
    if not items or not (query or "").strip():
        return []
    try:
        torch, tok, mdl, dev = load_model()
    except SystemExit as e:
        # 模型没下载/没放对位置：降级返 None，检索照常返回 RRF 结果
        print("[rerank] 模型不可用（%s），本次跳过精排" % e, file=sys.stderr)
        return None
    except Exception as e:
        print("[rerank] 模型加载失败，本次跳过精排：%s" % str(e)[:120], file=sys.stderr)
        return None
    try:
        flat = _pairs_of(query, items)
        if not flat:
            return []
        pairs = [[query, t] for _k, t in flat]
        scores = []
        batch = BATCH if dev == "cpu" else max(BATCH, BATCH_GPU)
        for i in range(0, len(pairs), batch):
            part = pairs[i:i + batch]
            enc = tok(part, padding=True, truncation=True,
                      max_length=max_length, return_tensors="pt")
            if dev != "cpu":
                enc = {k: (v.to(dev) if hasattr(v, "to") else v) for k, v in enc.items()}
            with torch.no_grad():
                logits = mdl(**enc).logits.view(-1)
            scores.extend(torch.sigmoid(logits.float()).tolist())
            # 进度一律走 stderr：stdout 是 --json 机器通道（同 bge_encoder 的教训）
            print("    rerank %d/%d" % (min(i + batch, len(pairs)), len(pairs)),
                  end="\r", file=sys.stderr)
        print("", file=sys.stderr)   # 收尾换行，别把 stderr 停在 \r 上
        # 滑窗多段 → 按 key 取 max（QAnything 父子 chunk 同款 max-pooling）
        best = {}
        for (k, _t), s in zip(flat, scores):
            s = float(s)
            if k not in best or s > best[k]:
                best[k] = s
        out = sorted(best.items(), key=lambda x: -x[1])
        return out[:max(1, int(topk))]
    except Exception as e:
        print("[rerank] 推理异常，本次跳过精排：%s" % str(e)[:120], file=sys.stderr)
        return None


def _selftest():
    """两对合成句子自测：相关对 sigmoid 应显著高于无关对。
    主理人冒烟实测：相关对 0.999 vs 无关对 0.0014，+0.3 的判据很稳。"""
    q = "如何防止大模型瞎编？"
    items = [
        {"doc_id": "rel",
         "chunk_text": "防止大模型幻觉的方法：检索增强生成（RAG）让每句话都带引用，无据不答。"},
        {"doc_id": "irrel",
         "chunk_text": "今天天气不错，适合出门跑步，晚上记得收衣服。"},
    ]
    rr = rerank(q, items, topk=2)
    if not rr:
        print("FAIL：模型不可用或推理异常（rerank 返回 %r）" % (rr,))
        return 1
    if len(rr) < 2:
        print("FAIL：精排结果不足 2 条（%r）" % (rr,))
        return 1
    top_key, top_score = rr[0]
    other = next(s for k, s in rr if k != top_key)
    ok = top_key == "rel" and top_score > other + 0.3
    print("相关 %.4f vs 无关 %.4f（判据：相关 > 无关 + 0.3）→ %s"
          % (top_score, other, "PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    import argparse
    ap = argparse.ArgumentParser(description="BGE Cross-Encoder 精排层（bge-reranker-v2-m3）")
    ap.add_argument("--selftest", action="store_true",
                    help="两对合成句子自测：相关对分数应高于无关对 +0.3")
    args = ap.parse_args()
    if not args.selftest:
        ap.print_help()
        return 0
    return _selftest()


if __name__ == "__main__":
    sys.exit(main())
