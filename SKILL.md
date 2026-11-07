---
name: chinese-recipe-archive
description: 检索并蒸馏本机 7.5 GiB 中式小吃与菜谱资料库（860 个文件）。当用户询问小吃配方、火锅底料、卤味酱货、汤面米粉、面点饼类、烧烤油炸、蘸料香料、冷饮甜品、八大菜系菜谱、开店投资方案，或要求"查一下某个配方/这道菜怎么做/这个料包比例是多少"时使用本技能。提供全库文件索引、140 份可全文检索的正文语料、关键词检索脚本和一份提炼好的核心配方精要；图片、扫描件、视频与 exe 资料只提供路径导航，需打开原件查看。
agent_created: true
---

# 中式配方资料库检索与蒸馏

## 用途

把本机资料库变成可查询的知识源：先检索定位，再读取正文，最后给出带来源、带风险提示的配方答案。资料库 7.5 GiB / 860 文件，其中只有 140 份有可提取正文；**不要把整个库当文本读，也不要复制原始文件到 skill 目录**。

## 何时使用

- 用户问某道菜/小吃的配方、配比、工艺流程、香料包、蘸料调制。
- 用户问"资料里有没有 XX"、"帮我找 XX 的做法"、"这个库有哪些配方 / 能问什么"（后者读 `references/CATALOG.md` 按主题作答）。
- 用户要做菜单、开店方案、成本核算、技术培训材料，且素材来自这个资料库。
- 用户明确提到 `配方` 目录、资料库根目录或 sicang8 / 小吃技术资料。

## 资料库与文件位置

- 根目录：由环境变量 `RECIPE_ARCHIVE_ROOT` 或 skill 目录下 `.local_root` 指定；`index.json` 中记为 `<RECIPE_ARCHIVE_ROOT>`
- 技能目录：`~/.workbuddy/skills/chinese-recipe-archive/`
- `data/index.json`：860 条文件元数据（相对路径、类别、类型、大小、提取状态、去重标记；`abs` 为占位符路径）
- `data/corpus.jsonl`：140 份正文，共约 342 万字，按文件 id 索引
- `references/RECIPES.md`：**优先读这里**，已蒸馏好的核心配比与通用工艺规律
- `references/CATALOG.md`：**配方总目录**，12 类主题逐条列出可问的配方名与 `[id]`。用户问「有哪些配方」「能问什么」「库里都有啥」、或需要确认某配方是否在库、需要主动推荐可问内容时读这里
- `references/INDEX.md`：主题导航、资料清单、图片/视频/扫描件分布
- `references/CAVEATS.md`：可靠性分级与食品安全合规红线，**给出可执行配方前必读**

## 工作流

### 1. 先查精要

读取 `references/RECIPES.md`，判断问题是否已被蒸馏覆盖。命中则直接作答并附上来源 id。若用户问「有哪些配方 / 能问什么」，改读 `references/CATALOG.md`，按 12 类主题给出具体配方名并附示例问句，不要只回一个笼统的主题列表。

### 2. 再检索定位

```bash
python scripts/search.py "胡辣汤 洗面"
python scripts/search.py "火锅底料" --kind text --limit 8
python scripts/search.py --stats
python scripts/search.py --show 831 --chars 3000
```

常用参数：`--kind text|image|video|ebook|program|all`、`--category 火锅汤底`、`--keep-duplicates`、`--context`、`--limit`、`--show <id> --offset/--chars`。
输出中的 `原件` 由 `RECIPE_ARCHIVE_ROOT` 或 `.local_root` 还原为可直接读取的绝对路径；未配置时显示占位符。`--show` 用于读取完整正文；大文件用 `--offset` 翻页。

### 3. 处理无正文的资料

- `extraction=scanned_or_oversize` 的 PDF、`.jpg/.gif/.bmp` 配方图：用视觉方式查看原件图片路径。
- `.dat/.mpg/.wmv/.rmvb/.flv/.mp4` 视频：只报路径与主题，说明需人工播放，不要臆造内容。
- `.exe/.chm` 打包资料：`小吃技术合集（文字）5/2650道小吃技术配方(完整无限制版).exe` 等是自解压电子书，不执行、不解压，只说明存在。
- 老式 `.doc` 已用 piece table 还原，个别提取失败的文件标 `failed`，可尝试用 WPS/Word 打开原件。

### 4. 组织答案

1. 先给结论与关键配比，用表格或分步骤列出，不照抄大段原文。
2. 标注来源：文件名 + `id`，必要时给绝对路径。
3. 换算单位（1 斤=500 g，1 两=50 g，1 钱=5 g）；纠正明显 OCR 错字（"干克"=千克、"鄙县"=郫县、"滋粑辣椒"=糍粑辣椒）。
4. 附上适用边界：资料为 2005–2011 年网购/加盟技术资料，须小批量试制验证；涉及食品添加剂、中药材、老汤循环使用时，按 `references/CAVEATS.md` 给出合规提醒。
5. 检索不到时明确说"库中未找到"，不要用通用菜谱知识冒充库内配方；可以提供常识性做法但必须标明"非本库内容"。

## 关键约束

- 不编造配比。数字只能来自 `corpus.jsonl`、`RECIPES.md` 或原件。
- 不把"绝密/祖传/破解版/专利配方"当作事实背书，这些是营销措辞。
- 不输出含罂粟壳、罂粟油、罂粟籽油脂等违禁成分的可操作配方，直接指出其违法并给出替代思路。
- 不执行库内 `.exe`、不批量解压、不修改 `<RECIPE_ARCHIVE_ROOT>` 下任何原文件。

## 维护

资料库增删文件后重建索引：

```bash
python scripts/build_index.py
python scripts/build_index.py --root "D:\其他路径\配方"
```

依赖 `pypdf`（PDF 文本层）与 `olefile`（老式 doc）；缺失时脚本仍可运行，只跳过对应格式。扫描型大 PDF、图片和视频不进语料，路径仍进索引。
