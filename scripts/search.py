#!/usr/bin/env python3
"""检索配方资料库索引与全文语料。

用法:
    python search.py "胡辣汤 洗面"                 # 综合检索
    python search.py "火锅底料" --kind text        # 只看可提取正文的资料
    python search.py "油条" --kind video           # 只找视频教程
    python search.py --stats                      # 主题/类型分布统计
    python search.py --show 128                   # 输出指定 id 文件的正文
    python search.py --show 128 --chars 12000     # 指定输出长度
    python search.py "烤鸡" --limit 5 --context 300

输出中的「原件」是可直接用于读取的绝对路径。
index.json 里的路径是占位符 <RECIPE_ARCHIVE_ROOT>，由环境变量 RECIPE_ARCHIVE_ROOT
或 skill 目录下 .local_root（本机私有、不入库）还原；未配置时原样显示占位符。
资料库根目录变化时，先运行 build_index.py 重建索引。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DATA = Path(__file__).resolve().parent.parent / "data"
PLACEHOLDER = "<RECIPE_ARCHIVE_ROOT>"
LOCAL_ROOT_FILE = Path(__file__).resolve().parent.parent / ".local_root"
KIND_WEIGHT = {"text": 1.0, "image": 0.55, "video": 0.55, "ebook": 0.5, "program": 0.35}


def load():
    idx_path = DATA / "index.json"
    if not idx_path.exists():
        print("ERROR: 索引不存在，请先运行 build_index.py", file=sys.stderr)
        sys.exit(2)
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    by_id = {r["i"]: r for r in index["files"]}
    texts = {}
    corpus = DATA / "corpus.jsonl"
    if corpus.exists():
        with corpus.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    texts[row["i"]] = row["t"]
    return index, by_id, texts


def real_root():
    env = os.environ.get("RECIPE_ARCHIVE_ROOT")
    if env:
        return env
    if LOCAL_ROOT_FILE.exists():
        return LOCAL_ROOT_FILE.read_text(encoding="utf-8").strip() or None
    return None


def display_path(s: str) -> str:
    root = real_root()
    if not root:
        return s
    resolved = s.replace(PLACEHOLDER, root.rstrip("\\/"))
    return resolved.replace("/", "\\") if os.name == "nt" else resolved


def human_size(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GiB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MiB"
    return f"{n / 1024:.0f} KiB"


def snippet(text: str, token: str, width: int) -> str:
    pos = text.find(token)
    if pos < 0:
        return ""
    start = max(0, pos - width // 3)
    end = min(len(text), pos + width)
    s = text[start:end].replace("\n", " ")
    return ("…" if start else "") + re.sub(r"\s+", " ", s) + ("…" if end < len(text) else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="*", help="关键词，空格分隔（OR 关系，命中越多排名越靠前）")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--context", type=int, default=220, help="命中片段长度")
    ap.add_argument("--kind", default="all", choices=["all", "text", "image", "video", "ebook", "program"])
    ap.add_argument("--category", default="")
    ap.add_argument("--dedup", action="store_true", default=True)
    ap.add_argument("--keep-duplicates", dest="dedup", action="store_false")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--show", type=int, default=-1, help="输出指定文件 id 的正文")
    ap.add_argument("--chars", type=int, default=6000)
    ap.add_argument("--offset", type=int, default=0)
    args = ap.parse_args()

    index, by_id, texts = load()

    if args.stats:
        print(f"资料库: {display_path(index['root'])}")
        print(f"文件: {index['total_files']}  总大小: {human_size(index['total_bytes'])}  索引时间: {index['generated_at']}")
        print("主题分布: " + ", ".join(f"{k}={v}" for k, v in sorted(index["categories"].items(), key=lambda x: -x[1])))
        kinds = {}
        for r in index["files"]:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        print("类型分布: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])))
        print(f"可全文检索: {len(texts)} 个文件")
        return 0

    if args.show >= 0:
        rec = by_id.get(args.show)
        if not rec:
            print(f"ERROR: 无此 id {args.show}", file=sys.stderr)
            return 2
        print(f"# [{rec['i']}] {rec['rel']}")
        print(f"# 类别={rec['category']} 类型={rec['kind']} 提取={rec['extraction']} 大小={human_size(rec['size'])}")
        print(f"# 原件: {display_path(rec['abs'])}")
        if "dup_of" in rec:
            print(f"# 注意: 与 id {rec['dup_of']} 内容完全重复")
        body = texts.get(rec["i"], "")
        if not body:
            print("# 无文本层。扫描件/图片请用视觉方式查看原件；视频/程序包需人工打开。")
        else:
            print(body[args.offset:args.offset + args.chars])
            if len(body) > args.offset + args.chars:
                print(f"\n# [截断] 全文 {len(body)} 字符，可用 --offset/--chars 继续读取")
        return 0

    tokens = [t.strip() for t in " ".join(args.query).replace(",", " ").replace("，", " ").split() if t.strip()]
    if not tokens:
        print("ERROR: 请提供关键词，或使用 --stats / --show", file=sys.stderr)
        return 2

    # 中文长词常因写法差异无法整串命中（如「兰州牛肉面」对应文件名「兰州牛肉拉面」），
    # 取首尾二字作弱特征，帮助文件名相近的资料排到前面
    aux_tokens = []
    for t in tokens:
        if len(t) >= 4 and re.fullmatch(r"[\u4e00-\u9fff]+", t):
            aux_tokens.extend([t[:2], t[-2:]])

    results = []
    for rec in index["files"]:
        if rec["kind"] != args.kind and args.kind != "all":
            continue
        if args.category and args.category not in rec["category"]:
            continue
        if args.dedup and "dup_of" in rec:
            continue
        name = rec["rel"]
        tags = " ".join(rec["tags"])
        body = texts.get(rec["i"], "")
        score = 0.0
        hits = []
        for t in tokens:
            tl = t.lower()
            n = 0
            if tl in name.lower():
                score += 10
                n += 1
            if tl in tags:
                score += 5
                n += 1
            if tl in rec["category"]:
                score += 3
                n += 1
            cnt = body.lower().count(tl)
            if cnt:
                score += min(6, 2 + cnt)
                n += 1
            if n:
                hits.append(t)
        for aux in aux_tokens:
            if aux in name.lower():
                score += 2
        if not hits:
            continue
        # 多关键词同时命中优先；无正文的扫描件/提取失败件降权，避免淹没可读资料
        score += 4 * (len(hits) - 1)
        score *= KIND_WEIGHT.get(rec["kind"], 1.0)
        if rec["extraction"] in ("failed", "scanned_or_oversize"):
            score *= 0.6
        if rec["extraction"] == "ok" and rec["text_chars"] > 0:
            score += 1.5
        results.append((score, rec, hits, body))

    results.sort(key=lambda x: (-x[0], -x[1]["text_chars"]))
    print(f"查询: {' '.join(tokens)}   命中 {len(results)} 条（显示前 {min(args.limit, len(results))} 条）")
    if not results:
        print("无命中。可试更短的词（如 '汤料'、'香料'），或 --keep-duplicates、--kind all。")
        return 0
    for rank, (score, rec, hits, body) in enumerate(results[:args.limit], 1):
        print(f"\n[{rank}] id={rec['i']} 得分={score:.1f} | {rec['category']}/{rec['kind']} | {human_size(rec['size'])} | 正文 {rec['text_chars']} 字")
        print(f"    路径: {rec['rel']}")
        print(f"    原件: {display_path(rec['abs'])}")
        for t in hits:
            s = snippet(body, t, args.context)
            if s:
                print(f"    〈{t}〉 {s}")
        if not body:
            print(f"    提取状态: {rec['extraction']}（无文本层，需打开原件查看）")
    print(f"\n提示: 用 python search.py --show <id> 读取完整正文；图片/扫描件/视频需打开原件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
