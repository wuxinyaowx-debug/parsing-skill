---
name: sci-pdf-parse
description: 科研 PDF 文献解析 Skill。对数字版与扫描版论文 PDF 做探测分类、几何级文字提取、多栏阅读顺序重建、页眉页脚剔除、表格结构还原、公式与特殊字符保真提取，并为每个结果保留页码与区块级溯源。适用于文献阅读、问答、翻译、参数提取与结构化知识构建等需要"提得全、排得对、拆得清、核得回"的任务。
---

# sci-pdf-parse：科研文献 PDF 解析

把一份科研论文 PDF 解析成**带页码与区块溯源的结构化文本**，再回答任务问题。核心原则：所有结论必须能指回 `full.md` / `blocks.jsonl` 中的具体块（含页码、bbox），读不出的部分如实标注，绝不猜测补齐。

## 适用条件与触发

- 输入是论文、预印本、报告等学术 PDF（单栏、双栏或多栏；数字版或扫描版）。
- 任务要求解析、提取、问答、翻译或核验 PDF 内容，并要求证据定位。
- 不适用于：纯图片理解（图表科学含义解读）——图表只按结构元素（位置+题注）识别。

## 环境与依赖

- `poppler-utils`：`pdfinfo`、`pdftotext`、`pdftoppm`（评测镜像已预装）。
- `python3`（仅用标准库）；脚本在本 Skill 的 `scripts/` 下。
- 扫描件兜底：`tesseract`（`ocrmypdf` 可选）。缺失时对扫描页标注 `unreadable-region` 而不是编造文字。
- 运行约定：在 `/app/work` 下工作，最终产物写入 `/app/output`；脚本路径以 `/skills/weekly-skill/scripts/` 为准（若平台挂载路径不同，用实际路径）。

## 标准流程（按序执行）

### 0. 读取任务

读取 `/input/case.json` 与 `/input/task.md`，确认 `case_id`、`output_contract` 字段清单和需要回答的问题。**先确认 PDF 实际文件路径**，不要按标签臆测内容。

### 1. 探测分类（必做）

```bash
python3 scripts/pdf_probe.py /input/<paper>.pdf
```

按返回的 `classification` 选路：
- `digital` → 走第 2 步。
- `mixed` → 第 2 步 + 对 `<50` 字符页走第 5 步 OCR 后合并。
- `scanned` → 直接走第 5 步 OCR 路线。
- `encrypted` → 先解密（`qpdf --decrypt` 或 `pdftotext -upw`）；失败则 `status=unreadable` 并说明。

### 2. 几何提取（核心一步）

```bash
cd /app/work
python3 /skills/weekly-skill/scripts/pdf_extract.py /input/<paper>.pdf --outdir .
```

产出：
- `full.md` — 全文按正确阅读顺序排列，每个块前有一条溯源注释 `<!-- p3 b12 paragraph bbox=[...] -->`。
- `blocks.jsonl` — 每块一行 JSON：`{id, page, bbox, kind, text}`；kind ∈ `heading / abstract / paragraph / caption / equation / table-candidate / reference`。
- `pages.json` — 每页尺寸、检测到的分栏中线、被剔除的页眉页脚清单。

方法细节（栏检测、带状排序、断字合并）见 `references/method.md`。

### 3. 表格结构还原（有 `table-candidate` 时）

```bash
python3 /skills/weekly-skill/scripts/pdf_tables.py --workdir /app/work
```

产出 `tables.md` / `tables.json`（Markdown 表 + 单元格网格，含页码与 bbox，置信度标为 `heuristic`）。**引用表格数据前必须按第 6 步渲染对应页面截图核对**，合并单元格可能归属错误。

### 4. 公式与特殊字符

- `kind=equation` 的块已按公式编号保留原文（含希腊字母、上下标的 Unicode 形式）。
- 若出现乱码（缺字形映射的字体，常表现为连续私用区字符 U+E000–U+F8FF），**不要臆造符号**；渲染该页截图人工/视觉判读，并在 `uncertainty` 中记录该公式编号。
- 符号映射规则见 `references/method.md` 第 6 节。

### 5. 扫描件 / 无文字层页面（兜底）

```bash
pdftoppm -r 300 -f <p> -l <p> -png /input/<paper>.pdf /app/work/page
tesseract /app/work/page-<p>.png stdout -l eng+chi_sim --psm 3
```

OCR 结果写入对应页的块，`kind` 标记为 `ocr-text`，并在 `uncertainty` 记录"该页为 OCR 结果，错字风险高"。低分辨率页面先 `pdftoppm -r 400` 再 OCR。详细预处理流程见 `references/method.md` 第 5 节。

### 6. 渲染核对（可核验性的关键）

对要引用的关键页（含表格、公式、题注的页）渲染图片核对提取结果：

```bash
pdftoppm -r 150 -f <p> -l <p> -png /input/<paper>.pdf /app/output/check-p<p>
```

逐条比对：数字、上下标、行列归属是否与图像一致。核对结论（一致/已修正/无法确认）写入 `sources` 或 `uncertainty`。

### 7. 自检

```bash
python3 /skills/weekly-skill/scripts/self_check.py /input/<paper>.pdf --workdir /app/work --result /app/output/result.json
```

`coverage` 检查丢字率（剔除页眉页脚后正常为 2–8%；>25% 说明误删正文，须用 `--keep-headers` 重跑对比）；`page-presence` 检查缺页；`result-json` 检查输出契约。

### 8. 产出 result.json

按 `case.json` 的 `output_contract` 逐字段填写，外加本 Skill 规定的通用字段；字段规范见 `references/output_contract.md`。骨架：

```json
{
  "case_id": "<from case.json>",
  "status": "success | partial | cannot-determine | refused | failed",
  "result": { "...output_contract 字段，答案逐条给出..." },
  "sources": [{"claim": "...", "page": 3, "block_id": "b12", "bbox": [72,100,540,180], "evidence": "原文引句"}],
  "uncertainty": [{"item": "...", "reason": "未报告/材料冲突/乱码/OCR", "action": "需要人工确认"}],
  "change_log": [{"what": "...", "why": "..."}]
}
```

铁律：
1. 答案中的每个关键结论至少在 `sources` 中有一条 `page + block_id` 溯源；引用数字、参数、版本号时必须能从块文本中逐字找到。
2. 材料未报告的内容写 `not-reported`，材料冲突写入 `uncertainty`，绝不补造数值。
3. `partial` / `failed` 必须给出原因与可人工接管的位置（页码、块、命令）。

## 异常处理速查

| 情况 | 处置 |
|---|---|
| 加密 PDF | `qpdf --decrypt`；失败 → `status=failed`，说明权限缺失 |
| 无文字层扫描件 | 第 5 步 OCR；无 tesseract → 相关页标 `unreadable-region`，`status=partial` |
| 私用区乱码字体 | 渲染截图判读；判读不出的符号在 `uncertainty` 逐条列出 |
| 页码错乱/插页 | 以 PDF 物理页序为准并在 `uncertainty` 说明与印刷页码的差异 |
| 图片型表格 | `table-candidate` 提取失败时渲染截图，按图像人工转录并标 `confidence=manual` |
| 提取后丢字率 >25% | 用 `--keep-headers` 重跑并对比 `pages.json` 中剔除清单 |

## 最小调用示例

见 `examples/minimal_example.md`（输入、命令、示意输出；示意输出已标注为示例）。

## 限制

- 复杂表格（多层表头、跨页续表）为启发式还原，引用前必须渲染核对（第 6 步）。
- 公式以 Unicode 线性形式保留，不重建 LaTeX 结构；需要 LaTeX 时按编号对照原文截图人工转写。
- 竖排、从右至左排版、严重倾斜扫描件未做专门处理，会落入 `partial` 并在 `uncertainty` 说明。
