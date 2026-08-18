# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- 开源发布准备：LICENSE（MIT）、PRD（docs/PRD.md，含规范分层 L1/L2/L3 与字段库机制）、.gitignore、CHANGELOG、CONTRIBUTING
- 内置字段库正名：`registry.example.json` → `field_library.json`，用户可从库选用或自然语言新建字段
- onboard 自动探测所属东财行业板块并配置 sector（f127 行业名 → 板块列表匹配 BK 代码）
- onboard 数据目录默认改为 `~/stock-data/<代码>`（不再落在 skill 安装目录内）
- GUIDE 新增"切换备源不用慌"FAQ；README 安装布局补《字段说明表.md》

### Fixed
- 凌晨/周末运行时，最新交易日的实时字段（市值/流通市值/量比/PE-TTM）被误判为历史而留空——实时源使用条件从"目标日=今天"修正为"目标日=最新K线日"
- verify_demo 凌晨/非交易时段运行挂起（写入步骤改用 --days 1 锚定最近交易日）

### Changed
- MACD 口径归属澄清：口径定义归 Skill 1（可变决策），Skill 2 仅按 registry 执行；registry macd 条目显式声明价格基础与月线历史深度需求

## [0.8.0] - 2026-08-18

### Added
- MACD 口径锁定为平安证券·不复权（东财 fqt=0 锚点逐位验证：中国海油/长江电力日/周/月）
- K线历史自动前补（搜狐历史行情），月线 EMA 全历史收敛
- 数值按字段 precision 固定小数位格式化（如 3.50）

### Fixed
- 月线 MACD 因腾讯K线 2000 条上限导致老股票 EMA 未充分收敛（长江电力月线 DIFF 0.39→0.40）

## [0.7.0] - 2026-08-17

### Added
- 首次使用引导（欢迎语、必问数据存放位置、aha 展示）
- GUIDE.md 小白使用指南、TESTING.md 小白测试剧本（A 主线 + B 刁难 14 场景）
- 干净会话端到端新用户验证通过

### Fixed
- 退市/长期停牌股拒绝接入（`--force` 可强制）
- `--days 0` 被误当"今天"处理
- config 缺失/乱码的报错友好化
- SSL 报错识别并提示企业代理场景（不再误导为"代码错误"）

## [0.6.0] - 2026-08-17

### Added
- `--days N` 批量补录最近 N 个交易日（幂等）
- README 安装说明、TESTING.md 新用户验收测试单

### Fixed
- CSV 写 BOM，Excel 双击不再乱码（旧文件读取兼容）

## [0.5.0] - 2026-08-17

### Changed
- PE 口径切换为 PE-TTM（直采 qt[53]/东财 f163）；pe_static 标记 deprecated 保留历史

## [0.4.0] - 2026-08-17

### Added
- 种子 registry 补全至 15 字段全部 ready（证据全部实测，含东财 f116/f117/f164 字段号实证）
- 搜狐历史行情 fetcher（东财 WAF 封禁时的历史备胎）
- onboard 种子回退（只给股票代码即可试跑）
- render_registry.py（registry → 人读字段说明表）、verify_demo.sh（8 步验收）

## [0.3.0] - 2026-08-17

### Added
- **字段执行器架构**：registry schema 2.0 可执行声明（fetcher/params、computed.function、checks）
- 信源插件框架（fetchers/）与公式注册表（computed/），新增字段零代码
- config 动态列布局（layout 段），完整可执行快照

## [0.2.0] - 2026-08-16

### Added
- 存储适配层（CSV 默认零依赖 / 腾讯文档 adapter）
- 定时任务模板去宿主化（标准 cron），开源 README

### Fixed
- `--date` 补录误用当前实时值（写入错误历史数据）
- 腾讯文档写入失败被误报为成功
- 新浪源缺 Referer 头 403、交叉验证退出码、`check_integrity --fix` 未实现等 15 项

## [0.1.0] - 2026-08-16 之前

- 初版（workbuddy 环境绑定）：stock-field-registry + stock-data-collector 两个 skill
