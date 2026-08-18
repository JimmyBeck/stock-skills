# 存储适配层指南

## 设计目标

collector 核心逻辑（fetch + 计算）与数据落库解耦。脚本只面向 `StorageAdapter` 接口编程，新增存储后端只需加 adapter，不动采集和计算逻辑。

## 当前状态

- **CSV adapter**（`scripts/storage/csv_adapter.py`）：默认 adapter，纯标准库，零外部依赖
- **腾讯文档 adapter**（`scripts/storage/tdoc_adapter.py`）：通过腾讯文档 sheet-mcp CLI 读写
- 预留扩展：钉钉文档、Excel、多目标等（见文末"新增 adapter"）

## 架构

```
collector 核心产出（按 config 的 layout 段组装好的行数据）
        ↓
  storage adapter（由 config 的 storage 段决定用哪个）
        ├── CSV adapter     (scripts/storage/csv_adapter.py)
        └── 腾讯文档 adapter (scripts/storage/tdoc_adapter.py)
```

核心点：**collector 不关心存储格式，只产出行数据**（行在到达 adapter 前已按 `layout` 组装完毕，列数由 layout 决定，不是固定值）。adapter 负责映射到具体存储。

## config 中的 storage 配置

```json
{
  "storage": {
    "type": "csv",
    "options": { "data_dir": "./data/sh600900" }
  }
}
```

```json
{
  "storage": {
    "type": "tdoc",
    "options": {
      "file_id": "<your-file-id>",
      "sheet_quote": "<your-quote-sheet-id>",
      "sheet_macd": "<your-macd-sheet-id>",
      "tdocs_dir": "<path-to-tencent-docs-cli>"
    }
  }
}
```

向后兼容：旧版配置只有 `doc` 段（+ 顶层 `tdocs_dir`）而无 `storage` 段时，自动映射为 tdoc adapter 并打印警告。

## 各 adapter 说明

### CSV adapter（默认）

- 每只股票一个目录（`storage.options.data_dir`，相对路径以配置文件所在目录为基准；缺省 `./data/<code>`）
- 目录下两个文件：`quote.csv`（行情）+ `macd.csv`（MACD），首行为表头，列布局由 config 的 `layout` 段决定（默认即预设 16 列/21 列）
- 追加写入；文件不存在时自动创建并写入表头
- 适合本地备份、无腾讯文档环境、dry-run 全流程验证

### 腾讯文档 adapter

- Sheet 结构：每股一文档，4 Sheet（行情/MACD/口径说明/讨论日志），脚本只写前两个
- 写入方式：CLI 调用 `set_range_value_by_csv`；返回码非零或无输出即抛 `StorageError`，绝不静默成功
- 读取分页：`get_cell_data` 单次限 500 行，adapter 内部按 `start_row` 循环递进直到取到空页
- 幂等性：写入前检查日期是否已存在
- CLI 目录由 `storage.options.tdocs_dir` 配置，详见 `tdoc-operations.md`

## adapter 接口

所有 adapter 实现 `scripts/storage/base.py` 中的抽象基类：

```python
class StorageAdapter(ABC):
    def read_sheet(self, sheet, max_col=21) -> list[list[str]]:
        """读取整个表，返回二维列表（含表头，第0行），裁掉尾部全空行"""

    def read_dates(self, sheet) -> list[str]:
        """读取已存储的日期列表（第0列，不含表头），用于幂等性检查"""

    def get_next_row(self, sheet) -> int:
        """获取下一个空行位置（0-indexed，含表头偏移）"""

    def write_record(self, sheet, row: list) -> None:
        """追加写入一行（一个交易日的数据），失败抛 StorageError"""

    def update_cell(self, sheet, row: int, col: int, value) -> None:
        """更新单个单元格（如 --fix 回写、补填板块参照），失败抛 StorageError"""
```

- `sheet` 为逻辑表名：`"quote"`（行情）/ `"macd"`（MACD），adapter 内部映射到具体文件或 sheet_id；列数以 config `layout` 为准
- 行列均 0-indexed；`row` 为显示值列表（日期已按 `date_format` 格式化）
- 基类提供 `date_exists(sheet, display_date)` 辅助方法，子类无需重写
- **写失败必须抛 `StorageError`**：主流程捕获后报"写入失败"并以非零码退出

## 新增 adapter（如钉钉文档/Excel/多目标）

1. 在 `scripts/storage/` 新建 `xxx_adapter.py`，继承 `StorageAdapter` 实现上述 5 个抽象方法
2. 从 `config["storage"]["options"]` 读取自己的配置项；配置缺失/环境不可用在 `__init__` 抛 `StorageError`
3. 在 `scripts/storage/__init__.py` 的 `_ADAPTERS` 注册 `{"xxx": XxxAdapter}`
4. `lib/common.py` 的 `load_config` 中把 `"xxx"` 加入 `storage.type` 合法值
5. 用 csv 全流程验证过后再联调：`update_daily.py --dry-run` → 真实写入 → `check_integrity.py`

## 新增字段时的列管理

**统一约定：新字段列追加在 layout 末尾，不改变已有列的顺序。**

列布局由 config 的 `layout` 段声明（元素为 field_id 或 `{"field": ..., "output": ...}`）。新增字段时在 layout 末尾 append 即可，存量数据无需迁移。**不要调整已有列顺序或往中间插入**——存储中的历史行按写入时的 layout 排列，变更列序会导致新旧行混排；确需变更须先迁移或重建存储。
