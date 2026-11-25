# sci-pdf-parse — 科研文献 PDF 解析 Skill

## 这是什么

一个可被 Agent 调用的 Skill，把科研论文 PDF（单栏/双栏、数字版/扫描版）解析成**带页码与区块溯源的结构化文本**，支撑文献阅读、问答、参数提取与核验任务。聚焦题面四个考察点：

- **提得全**：基于 `pdftotext -bbox` 的词盒级提取，正文、题注、脚注、参考文献逐字保留；页眉页脚按跨页重复模式剔除且全程留痕。
- **排得对**：用"无文字穿越的竖直空白带"检测分栏，再以通栏块切分水平带做带内先左后右的阅读顺序排序，纠正双栏串栏。
- **拆得清**：块级类型标注（heading/abstract/caption/equation/table-candidate/reference），表格词盒聚类重建行列并输出 Markdown。
- **核得回**：每块带 `page + bbox`；引用前用 `pdftoppm` 渲染原页核对；自检脚本报告丢字率与缺页。

## 文件清单

| 路径 | 作用 |
|---|---|
| `SKILL.md` | Skill 本体：触发条件、标准流程（探测→提取→表格→公式→OCR→核对→自检→产出）、异常处理 |
| `scripts/pdf_probe.py` | PDF 探测分类（digital/scanned/mixed/encrypted） |
| `scripts/pdf_extract.py` | 几何提取：分栏阅读顺序、页眉页脚剔除、段落合并，产出 full.md / blocks.jsonl / pages.json |
| `scripts/pdf_tables.py` | 表格候选块的行列重建，产出 tables.md / tables.json |
| `scripts/self_check.py` | 自检：丢字率、缺页、result.json 契约校验 |
| `references/method.md` | 方法细节与已知边界 |
| `references/output_contract.md` | result.json 字段规范 |
| `examples/minimal_example.md` | 最小调用示例（示意输出已标注为示例） |
| `GITHUB_URL.txt` | 本作品的 GitHub 公开链接（附加分用） |

## 依赖与安装

- poppler-utils（`pdfinfo` / `pdftotext` / `pdftoppm`）：评测镜像已预装；本地 Debian/Ubuntu 用 `apt-get install poppler-utils`，Windows 用 poppler-windows 发行版加入 PATH。
- Python ≥3.8，仅标准库，无需 pip 安装。
- 扫描件兜底可选：tesseract（`apt-get install tesseract-ocr`）。

所有脚本均为 UTF-8 文本，无符号链接，路径相对引用，不依赖任何个人文件。

## 快速开始

```bash
python3 scripts/pdf_probe.py paper.pdf            # 1. 探测分类
mkdir -p work && cd work
python3 ../scripts/pdf_extract.py ../paper.pdf --outdir .   # 2. 提取
python3 ../scripts/pdf_tables.py --workdir .      # 3. 表格（可选）
python3 ../scripts/self_check.py ../paper.pdf --workdir .   # 4. 自检
```

然后在 `work/full.md` 中阅读全文，在 `blocks.jsonl` 中检索并引用；结果按 `references/output_contract.md` 写入 `result.json`。

## 适用范围与限制

- 适用于横排、从左至右排版的学术 PDF；竖排/RTL 未专门处理（会落入 `partial` 并说明）。
- 表格还原为启发式，复杂合并单元格可能错位——输出固定标注 `confidence=heuristic`，引用前必须渲染核对。
- 公式保留 Unicode 线性形式，不重建 LaTeX。
- 无 tesseract 时纯扫描件只能标注 `unreadable-region`，不会编造文字。

## 维护

- 版本：v1.0（2026-11）。
- 已知问题与失败模式记录在 `references/method.md` 各节"已知边界"处。
