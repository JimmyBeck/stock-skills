# Registry JSON Schema

## 顶层结构

```json
{
  "schema_version": "2.0.0",
  "registry_version": "2.0.0",
  "last_updated": "2026-08-16",
  "fields": [ <field_entry>, ... ]
}
```

- `schema_version`：registry **格式版本**（本文件定义的契约版本）。2.0.0 起条目携带可执行段（fetcher/params/computed/checks）。
- `registry_version`（顶层）：历史遗留键，语义等同 `schema_version`，新工具读 `schema_version`。
- 注意与**字段级** `registry_version` 区分：字段级是单字段的口径版本（公式/口径变更时递增），与格式版本无关。

## field_entry 结构

```json
{
  "field_id": "string — 唯一标识，snake_case",
  "name": "string — 中文名称",
  "definition": "string — 精确定义，含公式或采集路径",
  "type": "采集 | 计算 | 人工",
  "formula": "string | null — 计算字段的公式，采集字段为null",
  "unit": "string | null — 单位（元/亿元/%/手/股等）；非数值字段（如日期）为 null",
  "precision": "integer | null — 小数位数",
  "sources": [ <source_entry> ],
  "computed": "object | null — 计算型字段的可执行定义（见下文 computed 规范）；纯采集字段为 null",
  "checks": [ <check_entry> ],  // 数据质量检查声明，供 check_integrity 消费，可为空数组
  "update_frequency": "string — 每日收盘后 / 每季度 / 实时",
  "available_after": "string — T日16:30 / T+1日9:00",
  "backfill_available": "boolean — 历史数据是否可回溯",
  "sample_values": [
    {"date": "YYYY-MM-DD", "value": <number|string>}
  ],
  "cross_validated": "boolean — 是否已多源交叉验证",
  "applicability": {
    "market": ["a", "hk", "us"],
    "sector": ["电力", "银行"] | null,
    "stock": ["sh600900"] | null
  },
  "overrides": [ <override_entry> ],
  "ready_for_production": "boolean — 是否满足 Ready 契约",
  "status": "draft | verified | production | deprecated",
  "registry_version": "string — 该条目的口径版本号，公式/口径/单位变更时递增",
  "last_verified_date": "YYYY-MM-DD",
  "notes": "string — 备注（消歧过程、边界行为、注意事项等）",
  "disambiguation_notes": "string | null — 消歧过程记录"
}
```

## source_entry 结构

```json
{
  "name": "string — 信源名称",
  "url_template": "string | null — URL模板，用{code}/{date}等占位；计算型信源（如\"计算\"）无URL，为 null",
  "field_index": "integer | null — 在返回数组中的索引（人读参考，执行以 params 为准）",
  "encoding": "utf-8 | gbk | null — 计算型信源无编码，为 null",
  "fetcher": "string | null — 解析器标识，枚举：tencent_qt / tencent_kline / sina_rt / eastmoney_rt / eastmoney_kline；计算型伪源（name=\"计算\"）为 null",
  "params": "object | null — 该 fetcher 的取数参数（规范见下文）；fetcher 为 null 时本字段为 null",
  "verified": "boolean — 是否已通过6项验证",
  "verified_date": "YYYY-MM-DD",
  "notes": "string — 边界行为、注意事项等"
}
```

规则：
- **采集型信源** `fetcher` 和 `params` 均为必填。
- **计算型伪源**（`name: "计算"`）`fetcher: null, params: null`，字段的可执行定义在 field_entry 的 `computed` 段。
- `sources` 数组有序：执行器按序尝试，主源失败机械切换备源。

## fetcher params 规范

### tencent_qt — 腾讯实时（qt.gtimg.cn）

`http://qt.gtimg.cn/q={code}` 返回 GBK 文本，`~` 分隔数组。params：

```json
{"index": 49, "segment": 2, "scale": 0.00000001}
```

- `index`（必填，int）：`~` 分隔后的数组索引（索引含义见 data-sources.md「字段映射」表）。
- `segment`（可选，int，0 基）：该元素再按 `/` 分段后取第 N 段。用于成交额这类 `"万/亿/元"` 复合格式（`qt[35]` 取 `segment: 2` 即元数值）。
- `scale`（可选，number，默认 1）：取值后乘以该系数。`0.00000001` 表示 ÷1e8（元→亿元）。
- 三者可组合，执行顺序固定：**取 index → （可选）segment 分段 → 转 float → 乘 scale**。
- 仅当日实时，不支持历史（`supports_history: false`）。仅 A 股字段映射可靠。

### tencent_kline — 腾讯前复权日K（web.ifzq.gtimg.cn）

`https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,2000,` 返回 JSON，含日K数组与内嵌 qt 实时数组。params 二选一：

```json
{"kline_col": 2, "date": "2026-08-14"}
{"qt_index": 3, "segment": 2, "scale": 1}
```

- `kline_col`（int）：取K线历史行的列。列含义（以 update_daily.py / lib/common.py 实际解析为准）：
  | 列 | 字段 |
  |----|------|
  | 0 | 日期 |
  | 1 | 开盘 |
  | 2 | 收盘 |
  | 3 | 最高 |
  | 4 | 最低 |
  | 5 | 成交量 |
  | 9 | 成交额（若该行含第10列且为数值；单位需接入时验证） |
- `qt_index`（int）：取响应内嵌 qt 实时数组（索引语义与 tencent_qt 完全一致），可叠加 `segment`/`scale`。**仅当日有效**，内嵌 qt 是实时快照。
- `date`（可选，string）：`"latest"`（默认）取最后一根K线；`"YYYY-MM-DD"` 取指定交易日历史行。仅对 `kline_col` 模式有意义。

### sina_rt — 新浪实时（hq.sinajs.cn）

`http://hq.sinajs.cn/list={code}` 返回 GBK 文本，**逗号**分隔数组。params：

```json
{"index": 3}
```

- `index`（必填，int）：逗号分隔后的数组索引。
- 必须带 `Referer: https://finance.sina.com.cn` 请求头，否则 403——由 fetcher 内置，params 不用管。
- 仅当日实时，不支持历史。

### eastmoney_rt — 东财实时（push2.eastmoney.com）

`https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=...` 返回 JSON，`data` 节点按 f 字段号取值。params：

```json
{"field": "f116", "scale": 0.00000001, "secid_from_config": "sector.secid"}
```

- `field`（必填，string）：f 字段号（如 `"f43"` 最新价、`"f116"` 总市值）。**f 字段号语义由调研确定并记录进 source_entry.notes**。
- `scale`（可选，number，默认 1）：同 tencent_qt。东财市值原始单位为元，`÷1e8` 换算亿元用 `0.00000001`。
- `secid_from_config`（可选，string）：secid 从 config 的指定 JSON 路径读取（见下文「config 引用写法」）。**缺省时** fetcher 按市场规则从股票代码推导（沪市 `1.{code}`、深市 `0.{code}`）。
- 仅当日实时，不支持历史。东财 WAF 封禁时 fetcher 内置回退 push2delay（延时行情）。

### eastmoney_kline — 东财历史K线（push2his.eastmoney.com）

`https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&...` 返回 JSON，`data.klines` 每行逗号分隔。params：

```json
{"kline_col": 8, "klt": 101, "fqt": 1, "date": "2026-08-14", "secid_from_config": "sector.secid"}
```

- `kline_col`（必填，int）：klines 行逗号分隔后的列。列含义（已核实 data-sources.md:106-119）：
  | 列 | 字段 |
  |----|------|
  | 0 | 日期 |
  | 1 | 开盘 |
  | 2 | 收盘 |
  | 3 | 最高 |
  | 4 | 最低 |
  | 5 | 成交量 |
  | 6 | 成交额 |
  | 7 | 振幅% |
  | 8 | 涨跌幅% |
  | 9 | 涨跌额 |
  | 10 | 换手率% |
- `klt`（可选，int，默认 101）：K线周期，101=日线。
- `fqt`（可选，int，默认 1）：复权方式，1=前复权，0=不复权（板块行情用 0）。
- `date`（可选，string）：同 tencent_kline，`"latest"` 默认 / `"YYYY-MM-DD"` 取历史。
- `secid_from_config`（可选，string）：同 eastmoney_rt。

### config 引用写法

params 中任何 `"<参数名>_from_config": "<json.path>"` 形式的键，表示该参数的值在运行时从 config 的指定路径读取，例如：

```json
{"kline_col": 8, "fqt": 0, "secid_from_config": "sector.secid"}
```

表示 secid 取 `config["sector"]["secid"]`（板块 secid 是 per-stock 参数，随个股配置）。路径用点分隔；路径不存在时执行器报错并提示补配置，不猜测默认值。

## computed 规范（计算型字段）

计算型字段（`type: "计算"`）在 field_entry 增加 `computed` 段：

```json
"computed": {"function": "pct_change", "params": {"base": "close", "ref": "prev_close"}}
```

- `function`（必填，string）：公式注册表中的函数名，**必须是下列名单之一，原样使用**（新公式 = 注册新函数，是显式代码动作）。
- `params`（必填，object，可为 `{}`）：函数参数。`base`/`ref` 等引用其他 field_id 或内建衍生输入。

### 函数名单（与实现方对齐的契约）

| function | params | 输出 | 说明 |
|----------|--------|------|------|
| `identity` | `{}` | 单值 | 直采占位：值直接来自 sources 采集，computed 仅声明走执行器管线 |
| `diff` | `{"base": "<field_id>", "ref": "<field_id\\|内建输入>"}` | 单值 | base - ref，如 close - prev_close |
| `pct_change` | `{"base": "...", "ref": "..."}` | 单值 | (base - ref) / ref × 100 |
| `amplitude` | `{}`（可空） | 单值 | (high - low) / prev_close × 100，输入固定为当根K线高/低与 prev_close |
| `pe_static` | `{"base": "close", "eps_param": "eps"}` | 单值 | close / eps；`eps_param` 指定从 `config["params"][eps_param]` 读取 EPS（per-stock，每季度财报后手动更新） |
| `macd` | `{"period": "daily" \\| "weekly" \\| "monthly"}` | **多列** | 标准 EMA(12,26,9)：DIFF=EMA12-EMA26，DEA=EMA(DIFF,9)，柱=2×(DIFF-DEA)。输入为前复权 close 序列（需≥1500条确保收敛） |

### 内建衍生输入

- `prev_close`：目标日的前一交易日收盘价，取自K线**倒数第二根**的收盘列（tencent_kline `kline_col: 2`，date 为目标日前一行）。除权除息日由执行器检测（K线昨收 ≠ 实时接口昨收）并按 `config["params"]["expected_div"]` 调整为 `adj_prev_close = prev_close - expected_div`，调整后参与 diff / pct_change / amplitude 计算。

### macd 多列输出结构

`macd` 函数输出固定 6 列，输出列名（layout 引用用）：

| 输出列名 | 含义 |
|----------|------|
| `DIFF` | DIFF 值 |
| `DEA` | DEA 值 |
| `MACD` | MACD 柱 = 2×(DIFF-DEA) |
| `ZERO` | 零轴状态（DIFF 零上/零下） |
| `CROSS` | 金叉/死叉状态 |
| `MOMENTUM` | 动能（柱较前值放大/缩小） |

## checks 规范

field_entry 的 `checks` 为 list，供 check_integrity 按声明执行。元素三种：

```json
{"type": "non_null", "markets": ["a"]}
{"type": "range", "min": 0, "max": 100}
{"type": "cross_recompute"}
```

- `non_null`：值不得为空。`markets` 可选，省略 = 全部市场；指定后仅对这些市场强制（如港/美股豁免）。
- `range`：数值须在 [min, max] 区间。
- `cross_recompute`：计算型字段用。用注册函数从原始数据重算并与存储值比对；`--fix` 时以重算值回写。

## 多列输出与 layout 引用约定

config 的 `layout` 段声明各 sheet 的列序。layout 元素两种形式（**定稿，实现方按此执行**）：

1. **字符串**：`"close"` —— 单列字段，直接写 field_id。
2. **对象**：`{"field": "macd_daily", "output": "DIFF"}` —— 多列输出字段的一列。`field` 为 field_id，`output` 为该字段 computed 函数的输出列名（如 macd 的 DIFF/DEA/MACD/ZERO/CROSS/MOMENTUM）。

一个多列字段在 layout 中占几列就展开为几个对象元素，顺序即列序。示例：

```json
"layout": {
  "quote_sheet": ["date", "close", "change", "change_pct"],
  "macd_sheet": [
    "date", "close",
    {"field": "macd_daily", "output": "DIFF"},
    {"field": "macd_daily", "output": "DEA"},
    {"field": "macd_daily", "output": "MACD"},
    {"field": "macd_daily", "output": "ZERO"},
    {"field": "macd_daily", "output": "CROSS"},
    {"field": "macd_daily", "output": "MOMENTUM"}
  ]
}
```

不使用 `"macd_daily.DIFF"` 点号字符串形式——点号形式不予支持，避免与 field_id 解析歧义。

## override_entry 结构（方法个性化）

当某只股票的标准信源失效，需要用不同方法时：

```json
{
  "scope": "string — 股票代码（如 sz000XXX）或市场（如 hk）",
  "reason": "string — 为什么需要override",
  "sources": [ <source_entry> ],
  "notes": "string"
}
```

override 内的 source_entry 同样必须携带 `fetcher`/`params`（schema_version ≥ 2.0.0）。

## status 状态流转

```
draft ──(通过6项验证)──→ verified ──(collector接入)──→ production
                                                          │
                                                   (高风险变更)
                                                          ↓
                                                    deprecated (旧版本保留)
                                                    + 新版本 draft→verified→production
```

- **draft**: 口径/信源调研中，collector 不可用
- **verified**: 满足 Ready 契约，待 collector 接入
- **production**: collector 已接入，每日采集中
- **deprecated**: 已下线，历史数据保留

## 版本规则

- 文档描述变更：不递增版本号
- 信源切换（同语义）：不递增字段口径版本号，更新 sources
- **公式/口径/单位变更**：递增字段级 `registry_version`，旧版本标记 deprecated 保留
- **格式变更**（本 schema 的结构变化）：递增顶层 `schema_version`

## 迁移说明（1.x → 2.0.0）

2.0.0 起条目携带可执行段。collector 加载 config 快照 / registry 时按以下规则校验：

- 采集型 source_entry 缺 `fetcher` 或 `params` → **拒收该字段**，报错提示"该字段条目为旧格式，需按 schema 2.0.0 重新调研补充可执行段"。**collector 不猜测取数方式**。
- `type: "计算"` 的字段缺 `computed` 段 → 同上拒收。
- `computed.function` 不在函数注册表名单内 → 拒收并提示"新公式需先注册函数"。
- 缺 `checks` 段 → 视为空数组（不拒收，但不执行任何字段级检查）。
- 1.x 格式的人读字段（`url_template`/`field_index`/`encoding` 等）全部保留，执行以 `fetcher`/`params` 为准，人读字段仅供审计与再调研。

## Ready 契约检查

`ready_for_production: true` 需同时满足：
1. type 和 formula（或 sources）已定义
2. sources 中至少 2 个 `verified: true`
3. sample_values 至少 5 天
4. cross_validated: true
5. available_after 已填写
6. applicability 已标注

## Ready 例外路径

标准契约的第 2/3/4 条对两类字段客观不可达。走例外路径的字段，满足对应替代验证后同样可置 `ready_for_production: true`，但 **notes 必须显式注明走了哪条例外路径**，并记录替代验证的证据。

### 例外路径 1：无历史信源字段

**适用条件**：字段只有当日实时接口，已调研信源均不提供历史序列（如 market_cap / circ_market_cap / vol_ratio）。`backfill_available` 必须为 `false`。

**替代验证**（三条全满足，替代标准契约第 2/3/4 条）：
1. **多源当日交叉**：≥2 个 `verified: true` 信源在同一时点取值一致（偏差阈值同交叉验证规则；同一厂商的不同端点，如腾讯K线内嵌 qt 与 qt.gtimg.cn，可分别计为一个源，但 notes 须注明同源厂商）。
2. **口径核算**：用独立可核的恒等式复算（如 总市值 ≈ 总股本 × 收盘价、流通市值 ≈ 流通股本 × 收盘价），偏差在阈值内，恒等式与数值记入 notes。
3. **样本**：`sample_values` 至少 1 天当日实测（盘中实测须注明"盘中"），`cross_validated: true`。

### 例外路径 2：计算型字段

**适用条件**：`type: "计算"` 的字段（如 macd_daily/weekly/monthly、pe_static、amplitude、change）。值由注册公式从其他字段推导，不存在"第二信源"概念——外部接口提供的同名字段只是同一公式的另一实现。

**替代验证**（三条全满足，替代标准契约第 2/3/4 条）：
1. **输入数据双源一致**：公式的输入数据（如K线收盘/高/低、prev_close）已经 ≥2 个 verified 信源交叉一致（通常复用 close 等基础字段的既有交叉结论）。
2. **重算样本自洽**：用注册公式从原始数据独立重算 ≥5 天样本，与 `sample_values` 一致；重算脚本/口径记入 notes。
3. **公式已注册**：`computed.function` 在函数名单内，`formula` 字段完整可读。

外部接口的同名字段（如腾讯 qt[39] 的 PE、东财K线 col7 的振幅）可列入 sources 作旁证交叉，但不替代上述三条。满足后 `cross_validated` 可置 `true`。
