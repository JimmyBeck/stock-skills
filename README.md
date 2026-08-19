# stock-skills

两个可组合的股票数据 agent skill，外加一份边界契约。与具体 agent 环境无关，可被任意支持 skill 的 agent CLI（Kimi Code、Claude Code、workbuddy 等）加载使用。

## 安装

把本仓库的两个 skill 目录放进你所用 agent 的 skill 加载目录（保持 `stock-field-registry` 与 `stock-data-collector` 同级；BOUNDARY.md 放在它们的上一级；GUIDE.md 是小白使用指南，建议一并复制，agent 回答用户问题时会用到）：

```
<skill目录>/
├── BOUNDARY.md
├── GUIDE.md                  # 小白使用指南（agent 答疑用）
├── 字段说明表.md             # 字段口径速查（agent 答疑用）
├── stock-field-registry/   # SKILL.md 在目录根部
└── stock-data-collector/
```

常见 agent 的用户级 skill 目录（以各 agent 当前文档为准）：

- **Kimi Code**：`~/.agents/skills/`
- **Claude Code**：`~/.claude/skills/`
- **其他 agent**：放项目目录下的 `.agents/skills/`（项目级）或按其 skill 机制加载

装好后对 agent 说"用 stock-data-collector 接入 sh600519 试跑一下"即可触发。要求：Python 3（仅标准库）。如果只想装 collector 一个，它内置了演示字段集（`assets/default_registry.json`）也能跑；两个都装才有完整的字段调研能力。

## 组成

- **stock-field-registry**（Skill 1）— 字段注册与调研：把模糊的数据需求变成验证过的字段定义（口径、信源、证据），产出 `registry.json`；自带共识字段库（`assets/field_library.json`，15 个字段可直接选用，持续生长）。主语永远是"字段"，不碰生产存储、不设定时任务。
- **stock-data-collector**（Skill 2）— 数据采集与落库：字段执行器架构，采集行为由 config（registry 快照）的字段定义驱动——采集型按 `fetcher`+`params` 走信源插件、计算型按 `computed.function` 走公式注册表、列布局按 `layout` 段声明。**新增字段零代码**（已支持的信源族与公式范围内）。运行时只读 config，不读 registry。
- **BOUNDARY.md** — 两个 skill 的边界契约（单一事实源）：目录 + 快照模型、Ready 契约、灰色地带归属。

## 目录结构

```
stock-skills/
├── BOUNDARY.md                  # 两 skill 的边界契约
├── docs/PRD.md                  # 产品需求文档（单一事实源，与钉钉文档同步）
├── GUIDE.md                     # 使用指南（小白版，全程自然语言，无命令行）
├── TESTING.md                   # 新用户验收测试单（小白剧本：A 主线 7 步 + B 刁难 14 场景）
├── verify_demo.sh               # demo 验收脚本（每轮迭代的固定验收载体）
├── 字段说明表.md                 # 种子 registry 的人读视图（render_registry.py 生成）
├── stock-field-registry/
│   ├── SKILL.md
│   ├── scripts/probe_source.py  # 信源探索与交叉验证工具
│   ├── scripts/render_registry.py  # registry → 人读《字段说明表》渲染器
│   ├── references/              # 消歧指南、验证清单、schema、字段口径、信源清单
│   └── assets/field_library.json   # 内置字段库（15 个共识字段，证据实测）
└── stock-data-collector/
    ├── SKILL.md
    ├── scripts/
    │   ├── update_daily.py      # 每日采集主脚本（字段执行器驱动）
    │   ├── check_integrity.py   # 完整性核查（字段 checks 驱动，--fix 重算回写）
    │   ├── onboard_stock.py     # 股票接入（生成完整可执行快照 + layout）
    │   ├── fetchers/            # 信源解析器插件（tencent_qt/tencent_kline/sina_rt/eastmoney）
    │   ├── computed/            # 公式函数注册表（identity/diff/pct_change/amplitude/pe_static/macd）
    │   ├── storage/             # 存储适配层（base / csv_adapter / tdoc_adapter）
    │   └── lib/                 # executor.py（执行器核心）+ common.py（共享工具）
    ├── references/              # 存储适配层设计、腾讯文档操作指南
    └── assets/                  # config 模板、layout 预设、定时任务 prompt 模板
```

## 快速开始

> 不会命令行？直接看 [GUIDE.md](GUIDE.md)（小白版使用指南），全程只用自然语言和你的 agent 对话。

**开箱试跑（demo）**：安装后只给一个股票代码即可——collector 的 `onboard_stock.py --code <代码>` 在 `--registry` 缺省时自动回退（优先用同安装的 field-registry 种子，单装 collector 时用内置演示字段集 `assets/default_registry.json`；15 个预装字段：行情 12 字段 + MACD 日/周/月），`update_daily.py --dry-run` 看到完整产出。`./verify_demo.sh` 可一键跑完整 demo 验收（onboard→dry-run→写入→补录→核查→修复），也是每轮迭代的固定验收载体。

**生产流程**：

1. **加字段**（Skill 1）：用 stock-field-registry 消歧口径、验证信源，产出 registry 条目（须满足 BOUNDARY.md 的 Ready 契约）。`render_registry.py` 可随时把 registry 渲染成人读的《字段说明表》。
2. **接股票**（Skill 2）：用 stock-data-collector 的 `onboard_stock.py --registry <registry.json>` 验证股票代码、对照 registry 做能力核查，生成 config 快照。
3. **试跑**：`python3 scripts/update_daily.py <config.json> --dry-run`，多源交叉验证无误后写入实数据。
4. **定时**：用你所用 agent 的定时能力（如 Kimi Code / Claude Code 的 CronCreate，或系统 crontab）按 `assets/automation_prompt_template.md` 注册每日采集（`0 16 * * 1-5`）与月度核查（`0 10 1 * *`）任务。

## 环境要求

- Python 3（仅标准库）
- **默认 CSV 存储零依赖**：config 的 `storage` 段设 `{"type": "csv"}` 即可全流程本地运行
- 可选腾讯文档存储：`{"type": "tdoc"}`，需自行配置 tdoc CLI（见 `references/tdoc-operations.md`）
- **企业/校园代理网络**：如遇 SSL 证书错误，所有脚本加 `--insecure` 重试（仅关闭证书校验）

## 已知限制

- **市场覆盖**：A股全自动；港/美股仅骨架支持，换手率/市值/PE 等字段需人工补填
- **时区**：脚本判断"今天是否交易日"使用本地时区。美股场景（收盘对应北京时间凌晨）建议用 `--date` 显式指定交易日
- **layout 变更不向后兼容**：接入后变更列布局需先迁移或重建存储，否则新旧数据行混排

## 免责声明

本项目数据来自公开网络行情接口（腾讯/新浪/东方财富/搜狐），准确性以各数据源为准。本项目仅供个人学习与技术研究使用，**不构成任何投资建议**。股市有风险，投资需谨慎。
