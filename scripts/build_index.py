#!/usr/bin/env python3
"""扫描配方资料库，生成可检索索引与全文语料。

用法:
    python build_index.py [--root "<资料库根目录>"] [--out <skill>/data]

根目录按 --root > RECIPE_ARCHIVE_ROOT > skill 目录下 .local_root 的顺序确定。
生成的 index.json 中 root / abs 写为占位符 <RECIPE_ARCHIVE_ROOT>，不含本机绝对路径。
生成两个文件:
    data/index.json    - 860+ 条文件元数据（路径/类型/大小/主题/标签/是否可全文检索）
    data/corpus.jsonl  - 每行 {"i": 文件id, "t": 提取出的正文}，供 search.py 全文检索

依赖: 可选 pypdf（PDF 文本层提取）、olefile（老式 .doc 提取）。
缺依赖时脚本仍可运行，只跳过对应格式并在 stdout 报告。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
from datetime import datetime
from pathlib import Path

PLACEHOLDER = "<RECIPE_ARCHIVE_ROOT>"
LOCAL_ROOT_FILE = Path(__file__).resolve().parent.parent / ".local_root"


def configured_root():
    """真实资料库根目录：环境变量优先，其次本机私有 .local_root（不入库）。"""
    env = os.environ.get("RECIPE_ARCHIVE_ROOT")
    if env:
        return env
    if LOCAL_ROOT_FILE.exists():
        value = LOCAL_ROOT_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    return None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None
try:
    import olefile
except ImportError:
    olefile = None

# 顺序敏感：越具体的主题越靠前
CATEGORY_RULES = [
    ("开店经营", ["开店", "加盟", "利润", "投资", "选址", "策划", "报价单", "成本", "经营", "创业", "活动方案", "流程表", "生意法则", "方案汇总"]),
    ("火锅汤底", ["火锅", "锅底", "涮烫", "涮汤", "麻辣烫", "羊蝎子", "关东煮", "烧鸡公", "串串", "粥火锅"]),
    ("冷饮甜品", ["刨冰", "炒冰", "冰粥", "奶茶", "果冻", "布丁", "冰淇淋", "冷饮", "酸奶", "奶昔", "松饼", "巧克力", "钵仔糕", "甜品"]),
    ("汤面米粉", ["米线", "拉面", "烩面", "板面", "牛肉面", "酸辣粉", "牛肉粉", "鸡汤面", "鱼汤面", "胡辣汤", "糊辣汤", "瓦罐", "老火靓汤", "煲汤", "泡馍", "面馆", "面条"]),
    ("面点饼类", ["油条", "馅饼", "烧饼", "煎饼", "锅贴", "包子", "枣糕", "发糕", "镜糕", "烧饼", "饼", "馒头", "花卷", "面点", "枣发糕", "小鱼饼"]),
    ("卤味酱货", ["卤味", "卤", "酱肉", "熏鸡", "烤鸡", "把子肉", "骨酥鱼", "猪蹄", "酱香", "咸蛋", "臭豆腐", "牛骨头", "牛杂", "张飞牛肉"]),
    ("烧烤油炸", ["烧烤", "炸串", "油炸", "炸鸡", "骨肉相连", "鸡柳", "里脊", "炭烤", "烤"]),
    ("酱料香料", ["辣椒油", "红油", "甜面酱", "辣椒酱", "蘸料", "麻酱", "香料", "调味", "底料", "汤料", "酱", "咖喱", "飘香"]),
    ("异国料理", ["韩国", "韩式", "日本", "和食", "刺身", "越南", "台湾", "東瀛", "东瀛", "西餐", "葡萄酒"]),
    ("营养食疗", ["营养", "孕妇", "怀孕", "健身", "相克", "保健", "药膳"]),
    ("菜系菜谱", ["菜谱", "八大菜系", "名菜", "炒饭", "盖浇饭", "家常", "美食", "料理", "素菜", "野菜", "茶", "烹饪", "招牌菜", "做菜"]),
    ("小吃综合", ["小吃", "技术", "配方", "大全", "合集", "秘方", "工艺", "制作"]),
]

MEDIA_EXT = {"jpg", "jpeg", "gif", "bmp", "png"}
VIDEO_EXT = {"dat", "mpg", "wmv", "rmvb", "rm", "mp4", "flv", "f4v", "avi", "mkv"}
PROGRAM_EXT = {"exe", "chm", "zip", "rar", "db", "htm", "html"}
TEXT_EXT = {"txt", "doc", "docx", "pdf", "htm", "html"}

CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
SPACE_RE = re.compile(r"[ \t\u3000]{2,}")
NL_RE = re.compile(r"\n{3,}")


def clean(text: str) -> str:
    text = CONTROL_RE.sub(" ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = SPACE_RE.sub(" ", text)
    return NL_RE.sub("\n\n", text).strip()


def decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030", "utf-16", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", "replace")


def extract_txt(path: Path) -> str:
    return decode_bytes(path.read_bytes())


def extract_htm(path: Path) -> str:
    s = decode_bytes(path.read_bytes())
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    return re.sub(r"(?s)<[^>]+>", " ", s)


def extract_doc(path: Path) -> str:
    """Word 97-2003 二进制 .doc：从 CLX piece table 还原正文。"""
    if olefile is None:
        return ""
    ole = olefile.OleFileIO(str(path))
    try:
        if not ole.exists("WordDocument"):
            return ""
        wd = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", wd, 0x0A)[0]
        table_name = "1Table" if (flags >> 9) & 1 else "0Table"
        if not ole.exists(table_name):
            table_name = "0Table" if ole.exists("0Table") else "1Table"
        if not ole.exists(table_name):
            return ""
        tbl = ole.openstream(table_name).read()
        fc_clx, lcb_clx = struct.unpack_from("<II", wd, 0x1A2)
        clx = tbl[fc_clx:fc_clx + lcb_clx]
        i = 0
        while i + 2 < len(clx) and clx[i] == 1:
            i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
        if i >= len(clx) or clx[i] != 2:
            return ""
        lcb = struct.unpack_from("<I", clx, i + 1)[0]
        plc = clx[i + 5:i + 5 + lcb]
        n = (lcb - 4) // 12
        if n <= 0 or n > 100000:
            return ""
        cps = [struct.unpack_from("<I", plc, 4 * k)[0] for k in range(n + 1)]
        base = 4 * (n + 1)
        parts = []
        for k in range(n):
            fc = struct.unpack_from("<I", plc, base + 8 * k + 2)[0]
            compressed = bool(fc & 0x40000000)
            off = fc & 0x3FFFFFFF
            length = cps[k + 1] - cps[k]
            if length <= 0:
                continue
            if compressed:
                off //= 2
                parts.append(wd[off:off + length].decode("gb18030", "replace"))
            else:
                parts.append(wd[off:off + 2 * length].decode("utf-16-le", "replace"))
        return "".join(parts)
    finally:
        ole.close()


def pdf_has_text_layer(path: Path) -> bool:
    """廉价启发式：头部出现字体/ToUnicode 才可能是文本型 PDF。"""
    with path.open("rb") as f:
        head = f.read(1024 * 512)
    return b"/Font" in head or b"/ToUnicode" in head


def extract_pdf(path: Path) -> str:
    if PdfReader is None:
        return ""
    if path.stat().st_size > 25 * 1024 * 1024 or not pdf_has_text_layer(path):
        return ""
    try:
        reader = PdfReader(str(path))
        out = []
        for page in reader.pages[:250]:
            try:
                out.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(out)
    except Exception:
        return ""


def classify(rel: str, ext: str) -> tuple[str, list[str]]:
    hay = rel.lower()
    tags: list[str] = []
    for category, keywords in CATEGORY_RULES:
        hits = [kw for kw in keywords if kw in hay]
        if hits:
            tags = hits[:6]
            return category, tags
    if ext in VIDEO_EXT:
        return "视频资料", []
    if ext in MEDIA_EXT:
        return "图片资料", []
    if ext in PROGRAM_EXT:
        return "程序与电子书", []
    return "其他资料", []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None, help="资料库根目录；默认读 RECIPE_ARCHIVE_ROOT 或 .local_root")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data"))
    args = parser.parse_args()

    root_arg = args.root or configured_root()
    if not root_arg:
        print("ERROR: 未配置资料库根目录。请设置环境变量 RECIPE_ARCHIVE_ROOT，"
              "或在 skill 目录创建 .local_root 写入根目录路径。", file=sys.stderr)
        return 2
    root = Path(root_arg)
    out_dir = Path(args.out)
    if not root.is_dir():
        print(f"ERROR: 资料库根目录不存在: {root}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p).lower())
    index = []
    corpus_rows = []
    counts: dict[str, int] = {}
    skipped = {"pdf_no_text_layer": 0, "pdf_bad": 0, "doc_no_text": 0, "unsupported": 0}

    for fid, path in enumerate(files):
        rel = str(path.relative_to(root)).replace("\\", "/")
        ext = path.suffix.lower().lstrip(".")
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        category, tags = classify(rel, ext)
        top = rel.split("/")[0] if "/" in rel else ""
        if ext in TEXT_EXT:
            kind = "text"
        elif ext in MEDIA_EXT:
            kind = "image"
        elif ext in VIDEO_EXT:
            kind = "video"
        elif ext == "chm":
            kind = "ebook"
        else:
            kind = "program"
        rec = {
            "i": fid,
            "rel": rel,
            "abs": PLACEHOLDER + "/" + rel,
            "ext": ext,
            "size": size,
            "category": category,
            "tags": tags,
            "top": top,
            "kind": kind,
            "readable": ext in {"txt", "doc", "htm", "html", "pdf"},
            "extraction": "pending",
            "text_chars": 0,
        }

        text = ""
        if ext == "txt":
            text = extract_txt(path)
            rec["extraction"] = "ok"
        elif ext in {"htm", "html"}:
            text = extract_htm(path)
            rec["extraction"] = "ok"
        elif ext == "doc":
            try:
                text = extract_doc(path)
            except Exception:
                text = ""
            if len(text.strip()) < 30:
                skipped["doc_no_text"] += 1
                rec["extraction"] = "failed"
            else:
                rec["extraction"] = "ok"
        elif ext == "pdf":
            if not pdf_has_text_layer(path) or size > 25 * 1024 * 1024:
                skipped["pdf_no_text_layer"] += 1
                rec["readable"] = False
                rec["extraction"] = "scanned_or_oversize"
            else:
                text = extract_pdf(path)
                if len(text.strip()) < 30:
                    skipped["pdf_bad"] += 1
                    rec["extraction"] = "failed"
                else:
                    rec["extraction"] = "ok"
        else:
            skipped["unsupported"] += 1
            rec["readable"] = False
            rec["extraction"] = "not_supported"

        text = clean(text)
        rec["text_chars"] = len(text)
        rec["has_text"] = len(text) >= 30
        if rec["has_text"]:
            corpus_rows.append({"i": fid, "t": text})
        index.append(rec)
        counts[category] = counts.get(category, 0) + 1

    # 同一份资料常在多处重复存放（如 御品轩、刨冰配方、麦多馅饼），打上 dup_of 便于检索去重
    seen_hash: dict[str, int] = {}
    dup_count = 0
    for row in corpus_rows:
        digest = hashlib.md5(row["t"].encode("utf-8")).hexdigest()
        rec = index[row["i"]]
        if digest in seen_hash:
            rec["dup_of"] = seen_hash[digest]
            dup_count += 1
        else:
            seen_hash[digest] = row["i"]

    with (out_dir / "index.json").open("w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "root": PLACEHOLDER,
            "total_files": len(index),
            "total_bytes": sum(r["size"] for r in index),
            "categories": counts,
            "files": index,
        }, f, ensure_ascii=False, indent=1)
    with (out_dir / "corpus.jsonl").open("w", encoding="utf-8") as f:
        for row in corpus_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"索引完成: {len(index)} 个文件 / {sum(r['size'] for r in index)/1073741824:.2f} GiB")
    print(f"可全文检索: {len(corpus_rows)} 个文件 / {sum(len(r['t']) for r in corpus_rows):,} 字符（其中重复 {dup_count} 个）")
    print("主题分布: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))
    print("跳过: " + json.dumps(skipped, ensure_ascii=False))
    print(f"输出: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
