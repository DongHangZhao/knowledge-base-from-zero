#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""export_rerank_onnx.py — 把 bge-reranker-v2-m3 导出成 ONNX，供 bge_reranker.py 的 ONNX 分支用。
================================================================================================
为什么需要（W26 2026-11-18）：onnxruntime CPU 推理比 torch 冷启动快 3.4s、稳态快 1.31x、
排序无损；但导出产物 ONNX_DIR/model.onnx 必须存在，否则 bge_reranker 自动回退 torch。

🔴 已踩过的坑（务必先读，别重蹈）：
1. **中文路径 + external_data 写入失败**：模型 2.2G > 2GB，torch.onnx.export 会拆出 external
   data 文件。直接导出到含中文的路径（如 `知识库/modules/...`）会报
   `Could not open file ... Constant_659_attr__value`。解法：先导到纯 ASCII 路径
   （默认 F:/_DevCache/w26onnx），成功后再整目录搬到 ONNX_DIR。本脚本已内建这段两步走。
2. **依赖**：torch 2.14 的 legacy exporter（dynamo=False）可用；需 onnx + onnxscript
   （pip 装，落 E/F 盘 venv，不违反 C 盘零增长）。缺 onnxruntime 不影响导出，只影响推理。
3. **dynamic_axes 必须带**：seq/batch 两轴都设动态，rerank 实际 padding 到 512，但短文本
   也要能跑；实测 32/19/24 三种 seq 长度下 ONNX 与 torch 排序全一致、diff <1e-7。
4. **opsync 版本**：opset_version=17；更高版本某些算子 onnxruntime 未实现会报错。

用法：
    python export_rerank_onnx.py                 # 导出到默认 ONNX_DIR（已存在则 skip）
    python export_rerank_onnx.py --force          # 已存在也重导
    python export_rerank_onnx.py --smoke          # 小 shape (2,32) 冒烟到临时目录，验导出链
    python export_rerank_onnx.py --staging D:/tmp # 自定义 ASCII 中转目录

幂等：目标 model.onnx 已存在且未 --force → [skip] 直接退出，不重复导出。
"""
import os
import sys
import time
import shutil
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(HERE, "models", "bge-reranker-v2-m3")
ONNX_DIR = os.path.join(HERE, "models", "bge-reranker-v2-m3-onnx")
STAGING_DEFAULT = "F:/_DevCache/w26onnx"   # 纯 ASCII 中转（中文路径 external_data 会失败）


def _ascii_only(path):
    """纯 ASCII 路径判定——external_data 写出对非 ASCII 路径不兼容。"""
    try:
        path.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def export(staging, onnx_dir, seq_len, force):
    import torch
    from transformers import AutoModelForSequenceClassification

    target = os.path.join(onnx_dir, "model.onnx")
    if os.path.isfile(target) and not force:
        print("[skip] 已存在：%s（要重导加 --force）" % target)
        return 0
    if not _ascii_only(staging):
        print("[FAIL] 中转目录含非 ASCII 字符，external_data 写入会失败：%s" % staging,
              file=sys.stderr)
        return 2
    if not os.path.isfile(os.path.join(MODEL_DIR, "config.json")):
        print("[FAIL] 源模型不存在：%s（先把 bge-reranker-v2-m3 整个目录放进来）" % MODEL_DIR,
              file=sys.stderr)
        return 2

    os.makedirs(staging, exist_ok=True)
    os.makedirs(onnx_dir, exist_ok=True)
    onnx_stage = os.path.join(staging, "model.onnx")

    print("[EXPORT] load model from %s" % MODEL_DIR, flush=True)
    t0 = time.time()
    mdl = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR)
    mdl.eval()
    dummy = {
        "input_ids": torch.ones((2, seq_len), dtype=torch.long),
        "attention_mask": torch.ones((2, seq_len), dtype=torch.long),
    }
    print("[EXPORT] export -> %s (seq=%d)" % (onnx_stage, seq_len), flush=True)
    with torch.no_grad():
        torch.onnx.export(
            mdl, (dummy,), onnx_stage,
            input_names=["input_ids", "attention_mask"],
            output_names=["logits"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "seq"},
                "attention_mask": {0: "batch", 1: "seq"},
                "logits": {0: "batch"},
            },
            opset_version=17, do_constant_folding=True,
            dynamo=False, external_data=True,
        )
    print("[EXPORT] DONE %.1fs" % (time.time() - t0), flush=True)

    # 两步走：ASCII 中转 → 目标（中文目录）。整目录搬，external data 一个都不能少。
    for f in os.listdir(staging):
        src = os.path.join(staging, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(onnx_dir, f))
    n_files = sum(1 for f in os.listdir(onnx_dir) if os.path.isfile(os.path.join(onnx_dir, f)))
    print("[VERIFY] 目标目录 %d 文件，model.onnx %.1fMB" %
          (n_files, os.path.getsize(target) / 1e6), flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="已存在也重新导出")
    ap.add_argument("--smoke", action="store_true", help="小 shape (2,32) 冒烟，验导出链不产正式模型")
    ap.add_argument("--staging", default=STAGING_DEFAULT, help="纯 ASCII 中转目录")
    args = ap.parse_args()
    staging = args.staging
    onnx_dir = os.path.join(staging, "_smoke_out") if args.smoke else ONNX_DIR
    seq_len = 32 if args.smoke else 64
    return export(staging, onnx_dir, seq_len, args.force)


if __name__ == "__main__":
    sys.exit(main())
