# AGENTS.md — 给 agent 的项目规则

> 本文件会被主流 agent CLI 自动加载。你是来迭代这个项目的 agent，先读完本文件再动手。

## 项目是什么

两个可组合的股票数据 agent skill（stock-field-registry + stock-data-collector）+ 边界契约（BOUNDARY.md）。环境无关，纯 Python 标准库。

## 关键文档地图（改动前先读相关的）

- `docs/PRD.md` — 需求单一事实源（含规范分层 L1/L2/L3 原则）
- `BOUNDARY.md` — 两 skill 边界契约（架构级，极少变）
- `stock-field-registry/assets/field_library.json` — 共识字段库
- `CHANGELOG.md` — 迭代历史
- `TESTING.md` — 用户测试剧本（修 bug 时对照用例编号）

## 迭代规矩（任何变更必须遵守）

1. 改完必须跑 `./verify_demo.sh`，**全绿才算完成**（网络环境 SSL 报错可加 `--insecure`）
2. 同步更新相关文档：PRD（需求变化）、SKILL.md（能力变化）、CHANGELOG（每轮必写）、字段说明表.md（字段库变化时用 `render_registry.py` 重新渲染）
3. 重大新能力要给 `verify_demo.sh` 加对应验收步骤
4. 字段口径/信源的变更走 stock-field-registry 流程（消歧→验证→证据），不许绕过
5. 提交信息用中文、说明改了什么；**push 前必须先问用户**
6. 不编造数据/信源/证据；采集失败留空并告警，绝不填假数据
7. 不提交用户运行时数据（`data/`、`*_config.json` 已在 .gitignore）

## 红线

- BOUNDARY.md 的架构边界（知识归 Skill 1、执行归 Skill 2、运行时零耦合）不可破坏
- registry schema 变更必须向后兼容或提供迁移说明
