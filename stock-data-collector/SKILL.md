---
name: stock-data-collector
description: 股票每日数据采集与落库。当用户需要接入新股票、每日自动采集数据、设置定时更新、核查数据完整性、或排查采集问题时触发。基于 registry 字段定义执行机械采集，不发明新方法、不改口径。
---

# Stock Data Collector — 股票数据采集

## Overview

基于字段注册表（registry）中已验证的字段定义，为具体股票执行采集、计算、落库、定时、校验。

**核心职责**：每日采集的稳定、准确；字段变更的消化；多下游存储适配。

**字段执行器架构**：采集行为完全由 config（registry 快照）中的字段定义驱动——采集型字段按 `sources` 的 `fetcher`+`params` 经信源解析器插件（`scripts/fetchers/`）取数，计算型字段按 `computed.function` 走公式注册表（`scripts/computed/`），列布局由 config 的 `layout` 段声明。**新增字段零代码**：只要信源族和公式在已支持范围内，Skill 1 产出 registry 条目、重新接入即可采集，无需修改本 skill 任何代码。新信源族 = 加一个 fetcher 插件文件；新公式 = 注册一个函数（小且隔离，属 Skill 1 调研的结论性产物）。

## 首次使用引导（欢迎流程）

用户第一次接触本 skill 时（尤其是非技术用户），按以下流程接待，**不要跳过**：

1. **欢迎**：用一两句话告诉用户你能做什么——"我可以帮你每天自动收集股票数据（行情、MACD 等 15 个字段），存到本地表格或在线文档。先随便给我一个股票代码，就能跑起来看看效果。"
2. **问存放位置**：在用户给出股票代码后、正式接入前，**必须询问**："数据拉出来后想放在哪里？"
   - 本地表格（CSV，默认，零配置，Excel 可直接打开）——大多数用户选这个
   - 腾讯文档（需要用户提供文档信息，见 references/tdoc-operations.md）
   - 用户说"随便/都行"→ 用本地 CSV，并告知文件位置
3. **快速出活**：接入后立即 dry-run 或拉最近 3 天数据，把结果**用表格形式展示给用户看**（这是用户的 aha 时刻，不要只打印日志）
4. **引导下一步**：告诉用户三句话——数据在哪、怎么每天自动更新、想收别的字段可以提

用户是老手（直接给代码、直接说存哪）时不要强行走流程，直接进入接入步骤。

## 与 stock-field-registry 的关系

详见 `../BOUNDARY.md`。核心要点：
- 本 skill 消费 registry（目录），在接入时读一次拷贝进 config（快照）
- **运行时零耦合**：每日自动化只读 config，不读 registry
- registry 更新不自动传播，加字段是显式重新接入（人工触发）
- **不理解字段语义**：只按 config 定义机械执行，遇到语义问题上报

## 核心原则

1. **正确性第一** — 多源交叉验证，不确定时询问用户，绝不瞎猜
2. **机械执行** — 不发明新方法、不改口径、不自行解释字段语义
3. **配置驱动** — 每只股票一个 JSON 配置，脚本通用
4. **逐步确认** — 关键环节需用户确认后推进
5. **问题闭环** — 排查→修复→验证→确认

## 工作流

### Step 1: 股票接入

拿到股票代码后：

1. **验证股票有效性**（确定性查询，归本 skill）
   - 用 K线接口确认代码格式正确、有数据返回
   - 获取股票名称、所属市场、交易所
   - 查上市日期、是否停牌
   - 这些信息写入 config 的 `validation` 段

2. **能力核查**（对照 registry）
   - 列出 registry 中 `ready_for_production: true` 的字段
   - 检查每个字段的 `applicability` 是否匹配该股票的市场/行业
   - 全部可用 → 进入下一步
   - 有缺/不适用 → **停下，报告缺什么**，不猜测不补做
     - 用户决定是否让 field-registry skill 调研补字段

3. **生成配置文件**（config 快照）
   - 从 registry 拷贝字段定义（完整可执行快照，含 `registry_version` 戳）
   - 填入 per-stock 参数（板块 secid、存储配置等；PE-TTM 直采无需 EPS 参数）
   - 填入 validation 信息（代码验证结果、上市日期、停牌状态）
   - 基于 `assets/stock_config.example.json` 模板
   - **异常股票守卫**：代码无效/无数据直接拒绝；退市或长期停牌（>30天无交易）拒绝接入并提示（仅历史分析可用 `--force`）

4. **与用户确认** — 展示配置，确认无误后进入下一步

接入脚本：`scripts/onboard_stock.py`
配置模板：`assets/stock_config.example.json`

### Step 2: 试跑验证

```bash
# dry-run：只计算不写入
python3 scripts/update_daily.py <config.json> --date <最近交易日> --dry-run
```

验证要点：
- K线数据正常拉取（≥1500条确保MACD收敛）
- 行情字段完整（无异常空值）
- MACD值合理（与公开行情软件交叉验证）
- 除权除息检测正常
- 港/美股特别关注无法自动获取的字段

**多源交叉验证** — 至少 2 个独立来源数据一致才能确认。

### Step 3: 定时任务设置

根据字段更新节奏设计定时策略：

| 数据类型 | 更新频率 | 建议采集时间 |
|----------|----------|-------------|
| 日线行情/MACD | 每交易日 | 16:00（收盘后） |
| 板块参照 | 每交易日 | 16:00（随日线一起） |
| 周线MACD | 每周 | 随日线更新，周五自动反映 |
| 月线MACD | 每月 | 随日线更新，月末自动反映 |
| EPS/财务数据 | 每季度 | 财报发布后手动更新配置 |

用你所用 agent 的定时能力创建定时任务（不同 agent 的定时工具不同：如 Kimi Code 的 CronCreate、Claude Code 的 CronCreate、workbuddy 的 automation_update，或系统 crontab），prompt 基于 `assets/automation_prompt_template.md`（内含标准 cron 表达式）。
时间要基于 `registry` 中字段的 `available_after` 设定。

### Step 4: 试跑定时任务（≥3 个完整周期）

**必须连续运行至少 3 个交易日**，验证：
- [ ] 定时触发正常
- [ ] 数据写入正确（与手动 dry-run 比对）
- [ ] 板块参照补填正常
- [ ] 重复写入检测生效
- [ ] 网络超时等异常有合理处理
- [ ] 节假日自动跳过

### Step 5: 长期复查机制

```bash
# 每月核查
python3 scripts/check_integrity.py <config.json>
# 或只看最近30天
python3 scripts/check_integrity.py <config.json> --last 30
```

建议频率：
- 每月 1 次：常规核查（日期连续性、MACD重算验证）
- 每季度 1 次：全面核查 + 口径回顾
- 节假日后：确认节后第一个交易日数据正常

可注册月度定时任务自动执行 check_integrity.py。

### Step 6: 问题排查与修复

遇到问题时：

1. **定位问题** — 运行 check_integrity.py 缩小范围
2. **信源失败处理**：
   - 主源失败 → 机械切换 config 中已验证的备源
   - 备源也失败 → 停采该字段（`enabled: false`），其余照常
   - 连续 N 次失败（阈值可配置）→ 告警通知用户
   - 告警带诊断信息：其他字段正常 → 大概率源的问题；全部失败 → 环境/网络问题
3. **分析根因** — 接口变更？配置错误？计算逻辑 bug？
4. **制定修复方案** — 评估影响范围
5. **试跑验证** — `--dry-run` 验证修复
6. **执行修复** — 写入修正数据
7. **复查确认** — 再次运行 check_integrity.py

**注意**：如果信源挂了需要换新源或改方法，那是 field-registry skill 的事。
本 skill 只做机械备源切换 + 告警，**不发明新方法**。

### Step 7: 字段变更消化

当 field-registry skill 产出了新字段或变更了已有字段：

| 变更类型 | 本 skill 的处理 |
|---------|----------------|
| 新增字段（简单采集值） | 显式重新接入 → 从 registry 拷贝进 config → 加列 → 从今日开始采集 |
| 新增字段（需二次计算） | 显式重新接入 → 确认公式实现 → 更新 config → 可能需要回填历史 |
| 修改字段公式 | **不动手，通知用户决策**（重算历史？从今日切换？分成两个字段？） |
| 切换信源 | 显式重新接入 → 更新 config 中的 sources → 保留旧源为备源 |
| 删除字段 | config 中 `enabled: false`，停止采集，保留历史数据 |

**关键原则**：registry 更新不自动传播。每次变更都是显式的重新接入，人工触发。
config 里的 `registry_version` 戳保证任何时候都能追溯每行历史数据当时用的是哪个口径版本。

## 不做什么（反向禁令）

- ❌ 不发明新字段/新方法 — 那是 field-registry 的事
- ❌ 不改口径/公式 — 同上
- ❌ 不理解字段语义 — 机械执行，遇到语义问题上报
- ❌ 运行时不读 registry — 只读 config 快照
- ❌ 不互相调用 — 用户是总线

## 快速开始

**一句话试跑（demo）**：只给股票代码即可——`onboard_stock.py --code sh600900` 缺省回退到内置演示字段集（15 个预装字段，`assets/default_registry.json`），生成 config 后 `update_daily.py --dry-run` 即可看到完整产出。

**生产接入**（指定自己的 registry）：

1. 确认股票代码和市场 → `onboard_stock.py --code <代码> --registry <registry.json>` 验证 + 能力核查
2. 生成 config → `--dry-run` 试跑 → 多源验证
3. 用户确认 → 配置存储（默认 CSV，可选腾讯文档；config 的 `storage` 段为 `{"type": "csv"|"tdoc"}`）→ 写入实数据试跑
4. 设置定时任务 → 连跑 3 天验证
5. 设置月度复查

## 已知限制（Known Limitations）

- **市场覆盖**：A股全自动；港/美股仅骨架支持，换手率/市值/PE 等字段需人工补填
- **时区**：脚本判断"今天是否交易日"使用本地时区。美股场景（收盘对应北京时间凌晨）需注意，建议用 `--date` 显式指定交易日
- **layout 变更不向后兼容**：config 的 `layout` 段决定存储的列序。接入后若变更 layout（增删列/调列序），存量数据行与新行会混排，需先迁移或重建存储再切换
- **退市/长期停牌股**：`onboard_stock.py` 对超过 30 天无交易的股票拒绝接入（生成 config 无意义）；确需接入做历史数据分析时用 `--force` 强制

## Resources

### scripts/
- `update_daily.py` — 每日数据更新主脚本（字段执行器驱动，`--dry-run` `--date` `--days N`（批量补录最近N个交易日）`--insecure`；A股全自动，港/美股骨架支持）
- `check_integrity.py` — 数据完整性核查（日期连续性、收盘价一致性 + 字段 checks 驱动：non_null/range/cross_recompute，`--fix` 重算回写；`--insecure` 同主脚本）
- `onboard_stock.py` — 股票接入脚本（验证代码 + 能力核查 + 生成完整可执行 config 快照与 layout）
- `fetchers/` — 信源解析器插件：`tencent_qt`/`tencent_kline`/`sina_rt`/`eastmoney_rt`/`eastmoney_kline`，`base.py` 定义接口与注册表
- `computed/` — 公式函数注册表：`identity`/`diff`/`pct_change`/`amplitude`/`pe_static`/`macd`（受控名单，禁止 eval）
- `lib/executor.py` — 字段执行器核心（按字段定义调度 fetcher/computed、主备源切换、prev_close 衍生输入与除权调整）
- `lib/common.py` — 共享工具模块（fetch、K线拉取、MACD 计算、config 加载等）
- `storage/` — 存储适配层：`base.py`（StorageAdapter 抽象基类）、`csv_adapter.py`（默认 adapter，纯标准库零依赖）、`tdoc_adapter.py`（腾讯文档 adapter）

### references/
- `tdoc-operations.md` — 腾讯文档操作指南（Sheet结构、CLI调用、写入逻辑）
- `storage_adapters.md` — 存储适配层设计（腾讯文档/CSV/钉钉文档，扩展指南）

### assets/
- `stock_config.example.json` — per-stock 配置模板（含 validation、完整可执行 fields 快照、layout 段）
- `default_registry.json` — 内置演示字段集（15 个预装字段；由 stock-field-registry 种子同步，`--registry` 缺省时回退使用）
- `layout_preset.json` — 预设列布局（16 列行情 + 21 列 MACD，onboard 默认拷贝进 config 的 layout 段）
- `automation_prompt_template.md` — 定时任务 prompt 模板
