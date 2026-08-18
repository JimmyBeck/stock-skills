# 贡献指南

感谢关注！本项目是两个 agent skill 的组合，贡献前建议先读 `BOUNDARY.md`（边界契约）和 `docs/PRD.md`（产品需求文档）。

## 最常见的三类贡献

**1. 新增数据字段**（不需要改代码）
- 用 stock-field-registry 的流程调研：口径消歧 → probe_source.py 验证信源 → 产出符合 Ready 契约的 registry 条目
- 条目必须含可执行声明（fetcher/params 或 computed.function）和实测证据（样本值、交叉验证）

**2. 新增信源族**（一个 fetcher 插件文件）
- 在 `stock-data-collector/scripts/fetchers/` 新建文件，实现 `base.py` 的 Fetcher 接口，在 `__init__.py` 注册
- 参考现有实现（tencent_qt.py / sohu_kline.py）

**3. 新增计算公式**（注册一个函数）
- 在 `stock-data-collector/scripts/computed/` 注册新函数；受控名单制，禁止 eval

## 提交前必做

```bash
./verify_demo.sh        # 8 步验收必须全绿
find . -name "*.py" | xargs python3 -m py_compile
```

- 纯 Python 3 标准库，不引入第三方依赖
- 文档与代码同步更新（SKILL.md 的 Resources 清单、references/、CHANGELOG.md）
- 数据类证据（样本值、交叉验证）必须真实实测，禁止编造

## 行为准则

- 不编造数据源、不写入未验证的字段定义
- 采集失败留空并告警，绝不填假数据
