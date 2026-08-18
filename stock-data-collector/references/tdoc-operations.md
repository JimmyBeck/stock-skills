# 腾讯文档操作指南

本文档说明如何通过腾讯文档 CLI 操作 Sheet 数据。

## 前置条件

腾讯文档 CLI 已安装并连接。CLI 目录由配置文件 `storage.options.tdocs_dir` 指定
（旧版配置为顶层 `tdocs_dir`），脚本不再内置默认路径，未配置或目录不存在会报错提示。

## Sheet 结构

每只股票建一个腾讯文档，包含4个Sheet：

| Sheet | 名称 | 用途 | 列数 |
|-------|------|------|------|
| Sheet1 | {股票名称}{代码} | 每日行情 | 16列 |
| Sheet2 | {股票名称}{代码}-MACD | MACD日/周/月 | 21列 |
| Sheet3 | 口径与信源说明 | 字段口径参考 | — |
| Sheet4 | 讨论日志与待确认 | 变更记录 | — |

Sheet3 和 Sheet4 为人工维护，脚本不自动写入。

## CLI 调用方式

### 读取数据

```python
# 读取Sheet区域
args = {
    "file_id": doc["file_id"],
    "sheet_id": sheet_id,
    "start_row": 0,
    "start_col": 0,
    "end_row": 500,
    "end_col": 20,
    "return_csv": True
}
result = tdoc_call(cfg, "get_cell_data", args)
# 解析 result.stdout 中的 structuredContent.csv_data
```

### 写入数据

```python
# 写入一行数据
args = {
    "file_id": doc["file_id"],
    "sheet_id": sheet_id,
    "start_row": next_empty_row,  # 0-indexed
    "start_col": 0,
    "csv_data": "value1,value2,value3,...\n"
}
tdoc_call(cfg, "set_range_value_by_csv", args)
```

### 写入单个单元格

```python
# 补填板块参照涨跌幅
args = {
    "file_id": doc["file_id"],
    "sheet_id": sheet_id,
    "row": row_idx,   # 0-indexed
    "col": col_idx,   # 0-indexed
    "value": "0.39"
}
tdoc_call(cfg, "set_cell_value", args)
```

## 响应解析

CLI 返回的 stdout 是 JSON 字符串，结构如下：

```json
{
  "result": {
    "structuredContent": {
      "csv_data": "..."
    },
    // 或者
    "content": [
      { "text": "{\"csv_data\": \"...\"}" }
    ]
  }
}
```

`parse_tdoc_response()` 函数处理两种格式，统一返回解析后的字典。

## 写入逻辑

### 幂等性
- 写入前检查目标日期是否已存在（读取第0列比对）
- 若已存在则跳过，避免重复写入

### 写入位置
- 通过 `get_next_empty_row()` 找到第0列最后一个非空行的下一行
- Sheet1 和 Sheet2 分别独立查找写入位置

### 日期格式
- `short` 格式: `YY.M.D`（如 `26.8.13`）— 沿用长江电力项目
- `iso` 格式: `YYYY-MM-DD`（如 `2026-08-13`）— 新股票推荐
- 由配置文件 `date_format` 字段决定

## 常见问题

### CLI 路径不存在
错误: `腾讯文档CLI目录不存在`
解决: 在配置文件 `storage.options.tdocs_dir` 中设置正确的腾讯文档 CLI 目录

### 写入失败
检查：
1. `file_id` 是否正确（从腾讯文档 URL 获取）
2. `sheet_id` 是否正确（需通过API或URL获取）
3. 网络连接是否正常

### 读取返回空
检查：
1. Sheet 是否有数据
2. `start_row` / `end_row` 范围是否覆盖了数据行
3. `return_csv` 是否设置为 `True`

## 创建新文档

为新股票创建腾讯文档时：

1. 使用腾讯文档 skill 创建在线表格
2. 创建4个Sheet，按上述结构命名
3. 在 Sheet1 第一行写入表头
4. 在 Sheet2 第一行写入表头
5. 获取 `file_id` 和各 Sheet 的 `sheet_id`，填入配置文件

**Sheet1 表头**:
```
日期,收盘价,涨跌额,涨跌%,板块涨跌%,总市值亿,流通市值亿,交易额亿,换手率%,量比,振幅%,市盈率,交易记录1,交易记录2,交易记录3,交易记录4
```

**Sheet2 表头**:
```
日期,收盘价,交易额亿,日DIFF,日DEA,日MACD,日零轴,日金叉死叉,日动能,周DIFF,周DEA,周MACD,周零轴,周金叉死叉,周动能,月DIFF,月DEA,月MACD,月零轴,月金叉死叉,月动能
```
