# 输出契约：result.json 与产物清单

每个案例在 `/app/output` 下交付：

```
/app/output/
  result.json          # 主结果（必需）
  full.md              # 全文结构化解析稿（可复制自 /app/work）
  blocks.jsonl         # 块级结构化数据（含页码与 bbox）
  tables.md            # 表格还原结果（如有）
  check-p*.png         # 核对用页面渲染图（关键页）
```

## result.json 字段

| 字段 | 类型 | 要求 |
|---|---|---|
| `case_id` | string | 与 `/input/case.json` 完全一致 |
| `status` | string | `success` / `partial` / `cannot-determine` / `refused` / `failed` |
| `result` | object | **必须包含 case.json 中 output_contract 列出的全部字段**，逐字段作答；未报告的值用 `"not-reported"` |
| `sources` | array | 关键结论的证据定位，见下 |
| `uncertainty` | array | 未报告、冲突、无法判读项，见下 |
| `change_log` | array | 运行中对提取结果做过的修正（去页眉误删、跨页表合并、截图判读替换乱码等），每条含 `what` 与 `why` |

### sources 条目

```json
{
  "claim": "结论或字段名",
  "page": 3,
  "block_id": "b12",
  "bbox": [72.0, 100.4, 540.2, 180.9],
  "evidence": "原文中逐字引用的句子或数值"
}
```

- `page` 为 PDF 物理页码（从 1 开始）；若与印刷页码不一致，在 `uncertainty` 说明。
- `evidence` 必须能在 `blocks.jsonl` 对应块的 `text` 中逐字找到。
- 每个关键结论至少一条 source；表格数据另附 `tables.md` 中对应表的位置。

### uncertainty 条目

```json
{
  "item": "哪个字段/结论",
  "reason": "not-reported | conflicting-materials | garbled-font | ocr-unreliable | table-heuristic | page-order",
  "location": {"page": 5, "block_id": "b40"},
  "action": "需要人工确认的具体事项"
}
```

### status 判定

- `success`：output_contract 全部字段完成且关键结论有溯源。
- `partial`：部分字段完成；必须列出未完成项及原因。
- `cannot-determine`：材料中不存在答案且已确认；说明检索范围（页、块）。
- `failed`：解析本身失败（加密、损坏、无 OCR 工具的纯扫描件）；给出可人工接管的命令与位置。

## 禁止事项

1. 不用 `"N/A"`、`"unknown"` 以外的占位符冒充已解析内容；统一用 `"not-reported"` 并配 `uncertainty` 条目。
2. 不写入 `blocks.jsonl` 中不存在的数字、版本号或引文。
3. 不修改 `full.md` 的溯源注释格式；修正正文时同步修正 `blocks.jsonl` 并记 `change_log`。
