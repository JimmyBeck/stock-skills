---
name: stock-field-registry
description: 股票字段注册与调研。当用户想新增一个数据字段、确认字段口径、调研字段信源、或对已有字段进行增删改时触发。产出 registry 条目（含验证证据），供 stock-data-collector 消费。不触碰生产存储，不设定时任务。
---

# Stock Field Registry — 股票字段注册与调研

## Overview

把用户对数据的模糊需求，变成精确的、经过验证的、可交给采集 skill 使用的字段定义。

**主语永远是"字段"，不是"股票"。** 这个 skill 不"接入股票"——它只回答"某个字段能不能拿到、怎么拿"。

**内置字段库**：`assets/field_library.json` 维护一份共识字段库（口径+信源+实测证据，持续生长）。用户既可以**从库中直接选用**字段，也可以**用自然语言描述新字段**（走下方工作流调研）；库中同名字段的不同口径以 field_id 变体区分（如 pe_static/pe_ttm）。库条目附验证日期与证据，但接入时仍按 Ready 契约重新核查（信源可能已变化）；调研产出的新字段验证达标后回流库中。

## 与 stock-data-collector 的关系

详见 `../BOUNDARY.md`。核心要点：
- 本 skill 产出 `registry.json`（目录），collector 在接入时读一次拷贝进 config（快照）
- 运行时零耦合：collector 每日自动化不读 registry
- registry 更新不自动传播，加字段是显式重新接入
- **不触碰生产存储**：验证数据只进 registry 的 sample_values

## 核心原则

1. **带着调研结果问，不空手反问** — 用户描述模糊时，先调研可能的解释和可得性，再带着选项问
2. **验证 = 能取到 + 语义一致 + 边界行为** — 不只是"URL 能通"，要确认单位、语义、边界
3. **高风险变更新版本不覆盖** — 公式/口径变更产生新版本，保留历史可追溯
4. **不确定时询问用户** — 不自作主张

## 工作流

### Step 0: 字段清单输入与匹配

用户的需求可能是一段文字描述，也可能是一个附件（如 Excel 表头）。**读取附件是宿主 agent 的通用能力，本 skill 不封装**——agent 从附件解析出字段名列表后进入本流程。两种输入统一为一个字段名清单（可带用户自述口径）。

清单到手后，先逐字段做**匹配**，三分支：

| 匹配结果 | 处理 |
|---------|------|
| **命中** registry 已有字段 | 直接复用，向用户确认口径是否一致 |
| **疑似同义**（如用户写"成交额"，registry 有"交易额"） | 带选项问用户：是同一字段还是要新字段 |
| **全新** | 进入 Step 1 消歧 + Step 2 验证 |

匹配完成向用户汇报一张对照表：哪些直接可用、哪些需要确认、哪些需要调研——用户确认后再进入后续步骤。

### Step 1: 口径消歧（模糊 → 精确）

用户说"我想要北向资金"，实际含义可能有 5 种。不要直接反问，先调研：

```
用户模糊描述
    ↓
[拆解] 列出所有可能的口径解释（基于领域知识）
    ↓
[调研] 每种解释在信源中的实际可得性
    ↓
[呈现] 把"可得的选项 + 各自含义 + 示例值"摆给用户
    ↓
[确认] 用户选定 → 锁定口径
```

消歧方法论见 `references/disambiguation_guide.md`。

**识别不存在的概念**：如果用户要的东西没有现成数据对应（如"主力意志力指标"），引导用户转向可得的替代品（如主力净流入、大单占比），不要硬造。

### Step 2: 信源验证

验证清单（6 项缺一不可）：

- [ ] **能取到** — URL/参数/字段索引跑通
- [ ] **单位正确** — 元/亿元/万手？实测验证
- [ ] **语义正确** — 与第二信源交叉对比，偏差在合理范围内
- [ ] **更新节奏明确** — 何时可得？T日16:00还是T+1日9:00？
- [ ] **历史可回溯** — 要不要回填？能回溯多远？
- [ ] **边界行为** — 停牌日返回什么？空值还是重复前值？除权日？涨跌停？

验证方法见 `references/source_verification_checklist.md`。
探索性 fetch 工具见 `scripts/probe_source.py`。

### Step 3: 产出 registry 条目

一个完整的 registry 条目 = 字段定义 + **可执行声明** + 信源 + 证据（schema 2.0 起，可执行声明是 collector 零代码采集的前提）：

```json
{
  "field_id": "north_bound_holding_pct",
  "name": "北向资金持股比例",
  "type": "采集",
  "formula": null,
  "unit": "%",
  "sources": [
    {"name": "东财个股北向", "url_template": "...", "field_index": 5,
     "fetcher": "eastmoney_rt", "params": {"field": "fxxx"}, "verified": true},
    {"name": "新浪北向", "url_template": "...", "field_index": 3,
     "fetcher": "sina_rt", "params": {"index": 3}, "verified": true}
  ],
  "computed": null,
  "checks": [{"type": "non_null", "markets": ["a"]}, {"type": "range", "min": 0, "max": 100}],
  "update_frequency": "每日收盘后",
  "available_after": "T日16:30",
  "backfill_available": true,
  "sample_values": [
    {"date": "2026-08-13", "value": 3.27},
    {"date": "2026-08-14", "value": 3.31}
  ],
  "cross_validated": true,
  "applicability": {"market": ["a"], "sector": null, "stock": null},
  "overrides": [],
  "ready_for_production": true,
  "registry_version": "1.0.0",
  "last_verified_date": "2026-08-16",
  "notes": "东财为主源，新浪为备源，两源偏差<0.01"
}
```

- **采集型**：每个 source 必须带 `fetcher`（信源解析器标识）+ `params`（取数参数）——collector 的字段执行器按此取数
- **计算型**：`sources` 为计算伪源，`computed` 段声明 `{"function": "<注册函数名>", "params": {...}}`（如 `diff`/`pct_change`/`macd`）
- **可执行声明不在已支持范围内时**（新信源族/新公式）：registry 条目照常产出，但需在 notes 注明"需新增 fetcher 插件/公式函数"，这是本 skill 调研的结论性产物

`sample_values` 和 `cross_validated` 是关键——collector 拿到时不是"理论上能拿"，而是"已经拿到过、验过、有样本"。

registry schema 完整定义见 `references/registry_schema.md`。
内置字段库（当前 15 个共识字段，证据全部实测）见 `assets/field_library.json`。

### Step 4: 变更管理（增删改）

| 变更 | 复杂度 | 流程 |
|------|--------|------|
| **增** | 低 | 走 Step 1-3，加进 registry，通知用户可接入 collector |
| **删** | 中 | 标记 deprecated（保留历史数据），通知用户决定存量怎么办 |
| **改** | 高 | 见下方风险分级 |

"改"的风险分级：

| 改什么 | 风险 | 处理 |
|--------|------|------|
| 改文档描述 | 低 | 直接改 registry 文字 |
| 改信源（同语义换源） | 中 | 更新 sources，需重新验证等价性 |
| 改公式（如PE从静态改滚动） | 高 | 产生新版本，**不覆盖**；通知用户决策（重算历史？从今日切换？分成两个字段？） |
| 改单位（如市值从亿改万） | 高 | 产生新版本，历史数据需归一化 |
| 改口径（如换手率分母改了） | 高 | 产生新版本，数据出现断层，需用户决策 |

**高风险变更走"暂停-决策-执行"**：
1. Skill 1 识别到高风险变更 → registry 中产生新版本条目
2. 通知用户 → 用户决策（重算历史？从今日切换？分两个字段？）
3. 用户决策后 → 通知 collector 重新接入（显式触发，不自动传播）

## 不做什么（反向禁令）

- ❌ 不写生产存储（腾讯文档等）——那是 collector 的事
- ❌ 不设定时任务——同上
- ❌ 不管理 per-stock 配置——collector 的事
- ❌ 不"处理股票"——主语永远是字段
- ❌ 不互相调用——用户是总线

## 快速开始

**新增字段**（最常见场景）：
1. 用户说"我想收集 XX"（或给附件由 agent 解析出字段清单）→ Step 0 匹配
2. 全新字段走 Step 1 消歧 → Step 2 验证信源（用 probe_source.py 试跑）
3. Step 3 产出 registry 条目 → 用户确认
4. 告知用户：可以让 collector 接入了

## Resources

### scripts/
- `probe_source.py` — 探索性 fetch 工具：试各种源、参数、取样本值、多源交叉验证
- `render_registry.py` — 把 registry.json 渲染成人读的《字段说明表.md》（registry 是唯一事实源，表格只是视图，改后重新渲染）

### references/
- `disambiguation_guide.md` — 口径消歧方法论 + 常见歧义案例库
- `source_verification_checklist.md` — 信源验证 6 项清单 + 执行方法
- `registry_schema.md` — registry JSON schema 完整定义
- `field-standards.md` — 长江电力项目已定稿的字段口径（作为 registry 种子参考）
- `data-sources.md` — 已知信源清单（URL/参数/字段映射/更新节奏）

### assets/
- `field_library.json` — 内置字段库：共识字段的口径与信源（当前 15 个，含 MACD 日/周/月）；用户可从中选用，也可自然语言新建字段
