# 定时任务 Prompt 模板

以下模板用于注册定时采集任务，与具体 agent 环境无关。
将 `{占位符}` 替换为实际值后使用。

**请用你所用 agent 的定时能力注册以下任务**（不同 agent 的定时工具不同：
如 Kimi Code 的 CronCreate、Claude Code 的 CronCreate、workbuddy 的 automation_update，
或直接写入系统 crontab）。下方给出标准 cron 表达式和与宿主无关的自然语言任务描述。

---

## 定时策略

| 频率 | cron 表达式 | 用途 |
|------|------------|------|
| 每交易日16:00 | `0 16 * * 1-5` | 每日数据更新 |
| 每月1日10:00 | `0 10 1 * *` | 月度完整性核查 |

**注意**: 节假日自动化仍会触发，但脚本会自动检测K线最新日期，非交易日会安全退出。

---

## 每日数据更新 Prompt

```
你是股票数据自动更新助手。请执行以下操作：

## 1. 运行更新脚本

执行命令：
```bash
python3 {scripts_dir}/update_daily.py {config_path}
```

其中 `{config_path}` 指向股票配置文件 JSON。

## 2. 处理脚本输出

- 如果脚本输出"无需更新, 退出"：今天是非交易日，正常结束，无需操作
- 如果脚本输出"数据已存在"：今天已更新过，正常结束
- 如果脚本写入成功：继续步骤3
- 如果脚本报错：记录错误信息，通知用户

## 3. 补填板块参照涨跌幅

脚本会在输出中打印板块补填提示，包含：
- 板块名称和 secid
- 板块数据 URL
- 目标行号和列号

用你所在环境的网页抓取工具（WebFetch/FetchURL/curl 均可）获取以下 URL 的数据：
URL: https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=0&beg={YYYYMMDD}&end={YYYYMMDD}

解析返回 JSON 中 data.klines 最后一行，按逗号分隔取第9个字段（索引8）为涨跌幅%。

将此值写入 config 配置的存储（storage 段）：
- 若 storage.type 为 "csv"：更新该股票数据目录下 quote.csv 中脚本输出的行号对应记录的第4列（板块涨跌幅）
- 若 storage.type 为 "tdoc"：写入腾讯文档，file_id: {file_id}，sheet_id: {sheet_quote}，row: {脚本输出的行号}，col: 4

## 4. 验证

确认写入成功后，简要报告：
- 日期
- 收盘价/涨跌%
- MACD日线关键值
- 板块涨跌%
- 是否有警告信息

## 5. 异常处理

- 网络超时：重试1次，仍失败则通知用户
- 接口返回异常：记录详情，通知用户
- 除权除息警告：通知用户确认
- 任何不确定的情况：不要自作主张，通知用户

## 配置

- 股票: {stock_name} {stock_code}
- 存储: 见 {config_path} 的 storage 段（type 为 csv 或 tdoc）
- 配置文件: {config_path}
- 脚本目录: {scripts_dir}
```

---

## 月度核查 Prompt

```
你是股票数据核查助手。请执行完整性核查：

## 执行核查脚本

```bash
python3 {scripts_dir}/check_integrity.py {config_path}
```

## 处理结果

- 全部通过：简要报告"X月数据核查通过"
- 发现问题：逐条列出问题，评估严重性，通知用户

## 核查范围

- 日期连续性：是否有缺失交易日
- 收盘价一致性：行情表与MACD表是否一致
- MACD重算验证：从K线重算与存储值比对
- 空值检测：关键字段是否为空
- 涨跌幅校验：涨跌额/前收是否匹配涨跌%

如发现MACD偏差>0.02或涨跌%偏差>0.05，需重点关注。
```
