# 最小调用示例（示例，非正式案例结果）

以下为说明接口与流程的示意，所有输出内容均为**示例**，不来自正式测试案例。

## 输入

- `/input/case.json`：`{"case_id": "demo-001", "paper": "paper.pdf", "question": "该论文使用的优化器与学习率是多少？", "output_contract": ["optimizer", "learning_rate"]}`
- `/input/paper.pdf`：一篇双栏数字版论文
- `/input/task.md`：任务说明

## 调用

```bash
python3 /skills/weekly-skill/scripts/pdf_probe.py /input/paper.pdf
# -> {"classification": "digital", "pages": 10, ...}

cd /app/work
python3 /skills/weekly-skill/scripts/pdf_extract.py /input/paper.pdf --outdir .
# -> {"blocks": 214, "kinds": {"caption": 12, "equation": 8, "heading": 14,
#     "paragraph": 168, "reference": 32, "table-candidate": 3},
#     "dropped_headers_footers": 18}

python3 /skills/weekly-skill/scripts/pdf_tables.py --workdir /app/work
# -> {"tables": 3}

# 在 blocks.jsonl 中检索关键词
grep -i "adam\|learning rate" /app/work/blocks.jsonl
# -> {"id":"b87","page":4,"bbox":[58.1,402.3,296.0,449.7],"kind":"paragraph",
#     "text":"We train all models with Adam [21] and a learning rate of 1e-4 ..."}

python3 /skills/weekly-skill/scripts/self_check.py /input/paper.pdf --workdir /app/work --result /app/output/result.json
# -> {"ok": true, ...}
```

## 示意输出（/app/output/result.json，示例）

```json
{
  "case_id": "demo-001",
  "status": "success",
  "result": {
    "optimizer": "Adam",
    "learning_rate": "1e-4"
  },
  "sources": [
    {
      "claim": "optimizer/learning_rate",
      "page": 4,
      "block_id": "b87",
      "bbox": [58.1, 402.3, 296.0, 449.7],
      "evidence": "We train all models with Adam [21] and a learning rate of 1e-4"
    }
  ],
  "uncertainty": [],
  "change_log": []
}
```

以上数值均为示意，仅用于说明字段格式。
