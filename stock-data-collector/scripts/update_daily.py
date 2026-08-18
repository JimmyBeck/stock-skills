#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票每日数据自动更新脚本（字段执行器架构，配置驱动）
采集行为由 config["fields"] 快照（registry 完整 field_entry，schema 2.0.0）驱动：
采集型走 fetcher 主备链，计算型走公式注册表，列布局由 config["layout"] 声明。
新增已支持信源族/公式集内的字段 = 更新 config 快照，无需改本脚本。
支持市场: a=沪深A股 / hk=港股 / us=美股（港美股部分字段留空，见输出提示）

使用方法:
    python3 update_daily.py <config.json>                    # 常规每日更新
    python3 update_daily.py <config.json> --dry-run          # 只计算不写入（试跑验证用）
    python3 update_daily.py <config.json> --date 2026-08-14  # 补录指定交易日
    python3 update_daily.py <config.json> --days 5           # 补录最近5个交易日（幂等，已存在的自动跳过）
    python3 update_daily.py <config.json> --insecure         # 关闭SSL证书校验（不推荐）

依赖: 仅Python标准库
输出: 由 config storage 段决定（本地CSV / 腾讯文档Sheet）
注意: 板块参照涨跌幅需通过网页抓取从东财获取后补填（脚本会打印提示）
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import common
from lib import executor
from storage import get_adapter, StorageError


# ==================== 单日执行 ====================
def run_one(cfg, adapter, kline, qt_arr, target, dates, dry_run, insecure):
    """采集/计算并写入一个交易日。返回 True=有写入，False=跳过（已存在/dry-run）"""
    stock = cfg["stock"]
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"\n--- 目标交易日: {target} (K线索引: {dates.index(target)}) ---")
    if target != today and target == dates[-1]:
        print("  最近交易日补录: 实时qt对应该日收盘快照，实时类信源可用")
    elif target != today:
        print("  历史补录模式: 实时qt只代表当前行情，实时类信源自动跳过，"
              "不支持历史回填的字段将留空")

    # 检查是否已更新
    target_display = common.to_display(cfg, target)
    if adapter is None:
        print("  存储不可用，跳过查重")
    else:
        try:
            if adapter.date_exists("quote", target_display):
                print(f"  {target} 的数据已存在, 无需更新。")
                return False
        except Exception as e:
            print(f"  ⚠️ 读取存储失败: {e}，继续更新（注意可能产生重复行）")

    # 逐字段执行（采集型走 fetcher 主备链，计算型走注册函数）
    print("  执行字段采集/计算...")
    try:
        ctx = executor.ExecContext(cfg, target, kline, qt_arr=qt_arr, insecure=insecure)
    except executor.FieldReject as e:
        print(f"  ❌ {e}")
        sys.exit(1)
    not_onboarded = executor.execute_layout(ctx)

    fmap = executor.field_map(cfg)
    for fid, value in ctx.values.items():
        meta = ctx.meta.get(fid, {})
        name = fmap.get(fid, {}).get("name", fid)
        status = meta.get("status", "")
        if status == "ok":
            if isinstance(value, dict):
                brief = " ".join(f"{k}={v}" for k, v in value.items())
            else:
                brief = value
            print(f"    {fid}({name}) = {brief}   [来源: {meta.get('source')}]")
        elif status == "disabled":
            print(f"    {fid}({name}): 已禁用(enabled=false)，留空")
        elif status == "skipped_market":
            print(f"    {fid}({name}): 不适用于 {stock['market'].upper()} 股，留空")
        elif status == "no_history":
            print(f"    {fid}({name}): 信源不支持历史回填，留空")
        elif status == "not_onboarded":
            pass  # 末尾汇总
        else:
            print(f"    {fid}({name}): 执行失败，留空")
    for w in ctx.warnings:
        print(f"    ⚠️ {w}")

    # 按 layout 组装行并写入
    quote_row = executor.assemble_row(ctx, "quote")
    macd_row = executor.assemble_row(ctx, "macd")

    if dry_run:
        print("  [DRY-RUN] 跳过写入。将写入的行内容:")
        print(f"    Sheet1(行情): {quote_row}")
        print(f"    Sheet2(MACD): {macd_row}")
    else:
        try:
            adapter.write_record("quote", quote_row)
            print("  Sheet1 行情数据写入 ✓")
            adapter.write_record("macd", macd_row)
            print("  Sheet2 MACD数据写入 ✓")
        except StorageError as e:
            print(f"  ❌ 写入失败: {e}")
            sys.exit(1)

    # 摘要与待办
    close_v = ctx.values.get("close", "")
    change_v = ctx.values.get("change", "")
    pct_v = ctx.values.get("change_pct", "")
    print(f"  日期: {target_display}  收盘: {close_v}  涨跌: {change_v}({pct_v}%)")
    for fid, value in ctx.values.items():
        if isinstance(value, dict) and fid.startswith("macd"):
            print(f"  {fmap.get(fid, {}).get('name', fid)}: "
                  f"DIFF={value.get('DIFF')} DEA={value.get('DEA')} MACD={value.get('MACD')} "
                  f"{value.get('ZERO')} {value.get('CROSS')} {value.get('MOMENTUM')}")

    todo = []
    # 板块参照列在 layout 中保留，仍由自动化任务补填（现状保留）
    sec_entry = fmap.get("sector_change_pct")
    sec_enabled = sec_entry.get("enabled", True) if sec_entry else True
    if cfg["sector"] and not dry_run and sec_enabled:
        sec = cfg["sector"]
        ymd = target.replace("-", "")
        todo.append(
            f"板块参照({sec['name']})未填入: 行情Sheet第{sec.get('col', 4)}列, "
            f"取东财 {sec['secid']} 当日涨跌幅%\n"
            f"  网页抓取: https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={sec['secid']}"
            f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt=101&fqt=0&beg={ymd}&end={ymd}\n"
            f"  解析klines最后一行, 取第9个字段(涨跌幅%)填入")
    # 历史补录留空汇总（取代旧版写死的留空提示）
    no_history = [f"{fid}({fmap.get(fid, {}).get('name', fid)})"
                  for fid, m in ctx.meta.items() if m.get("status") == "no_history"]
    if no_history:
        todo.append("以下字段的实时信源不提供历史值，无法回填，已留空（属预期，非错误）: "
                    + ", ".join(no_history))
    if stock["market"] != "a":
        skipped = [f"{fid}({fmap.get(fid, {}).get('name', fid)})"
                   for fid, m in ctx.meta.items() if m.get("status") == "skipped_market"]
        if skipped:
            todo.append(f"港/美股模式: 以下字段未自动获取，留空待人工补填或确认后扩展: "
                        + ", ".join(skipped))
    if not_onboarded:
        todo.append("layout 中未接入的字段列（留空，如需采集请用 stock-field-registry 调研接入）: "
                    + ", ".join(not_onboarded))
    for w in ctx.warnings:
        if "人工" in w or "复核" in w or "存疑" in w:
            todo.append(w)
    if todo:
        print("  待办:")
        for t in todo:
            print(f"    - {t}")
    return not dry_run


# ==================== 主流程 ====================
def main():
    parser = argparse.ArgumentParser(
        description="股票每日数据自动更新（字段执行器架构，配置驱动，支持A股/港股/美股）")
    parser.add_argument("config", help="配置文件路径 (*.json)")
    parser.add_argument("--dry-run", action="store_true", help="只计算不写入（试跑验证用）")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="补录指定交易日")
    parser.add_argument("--days", metavar="N", type=int,
                        help="补录最近N个交易日（幂等，已存在的自动跳过；与 --date 互斥）")
    parser.add_argument("--insecure", action="store_true",
                        help="关闭SSL证书校验（不推荐，仅个别企业网络）")
    args = parser.parse_args()

    if args.date and args.days:
        print("❌ --date 与 --days 互斥，请只用一个")
        sys.exit(1)

    cfg = common.load_config(args.config)
    stock = cfg["stock"]
    insecure = args.insecure or cfg.get("insecure", False)
    storage_type = cfg["storage"]["type"]

    # 配置可执行性校验（拒收规则：旧格式/缺可执行段直接报错，不猜测）
    errors = executor.validate_config(cfg)
    if errors:
        print("❌ 配置不符合 schema 2.0.0 可执行要求:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    # 创建存储 adapter；dry-run 时存储不可用只警告不退出
    adapter = None
    try:
        adapter = get_adapter(cfg)
    except StorageError as e:
        if args.dry_run:
            print(f"⚠️ 存储不可用({e})，dry-run 模式继续（不读写存储）")
        else:
            print(f"❌ {e}")
            sys.exit(1)

    print("=" * 60)
    print(f"{stock['name']} {stock['code'].upper()} 每日数据自动更新 ({stock['market'].upper()}股)")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
          + ("  [DRY-RUN 只算不写]" if args.dry_run else ""))
    print("=" * 60)

    # ---- Step 1: 获取K线数据 ----
    print("\n[1/2] 获取K线数据（不复权，腾讯2000条+搜狐历史前补）...")
    try:
        kline, qt_arr = common.fetch_kline(stock["code"], insecure=insecure)
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)
    dates = [r[0] for r in kline]
    print(f"  获取 {len(kline)} 条记录, 日期范围: {dates[0]} ~ {dates[-1]}")
    if len(kline) < 1500:
        print(f"  ⚠️ 历史数据不足1500条，周/月线MACD可能未充分收敛，解读时注意")

    # ---- Step 2: 确定目标交易日列表并逐日执行 ----
    print("\n[2/2] 逐日执行...")
    today = datetime.now().strftime("%Y-%m-%d")
    if args.date:
        if args.date not in dates:
            print(f"  ❌ {args.date} 不在K线中（非交易日或超出范围），最新K线日期: {dates[-1]}")
            sys.exit(1)
        targets = [args.date]
    elif args.days is not None:
        if args.days < 1:
            print("  ❌ --days 必须 ≥ 1")
            sys.exit(1)
        targets = dates[-args.days:]
        print(f"  最近 {len(targets)} 个交易日: {targets[0]} ~ {targets[-1]}")
    else:
        if dates[-1] != today:
            print(f"  ⚠️ 今天({today})不是交易日, K线最后日期为{dates[-1]}")
            print("  无需更新, 退出。")
            return
        targets = [today]

    written = 0
    for target in targets:
        if run_one(cfg, adapter, kline, qt_arr, target, dates, args.dry_run, insecure):
            written += 1

    print("\n" + "=" * 60)
    if args.dry_run:
        print(f"完成（dry-run 未写入，共处理 {len(targets)} 个交易日）")
    else:
        print(f"完成：新写入 {written} 天，跳过 {len(targets) - written} 天（已存在）")
    if storage_type == "tdoc":
        print(f"腾讯文档: https://docs.qq.com/sheet/{cfg['storage']['options']['file_id']}")
    elif adapter is not None:
        print(f"CSV数据目录: {adapter.data_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
