#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据完整性核查脚本（字段执行器架构，checks 驱动）
检查存储（本地CSV / 腾讯文档）中的股票数据是否完整、准确、连续。

使用方法:
    python3 check_integrity.py <config.json>                 # 核查全部数据
    python3 check_integrity.py <config.json> --last 30       # 只核查最近30天
    python3 check_integrity.py <config.json> --last 7 --fix  # 核查并尝试修复
    python3 check_integrity.py <config.json> --insecure      # 关闭SSL证书校验（不推荐）

检查项:
    1. 日期连续性: 与K线交易日历比对，找出缺失的交易日
    2. 收盘价一致性: layout 中 close 字段在行情/MACD两个Sheet间是否一致
    3. 字段级检查（由 config fields 快照中每条 field_entry 的 checks 声明驱动）:
       - non_null: 值不得为空（markets 可选，仅对指定市场强制，港/美股可豁免）
       - range: 数值须在 [min, max] 区间
       - cross_recompute: 计算型字段用注册函数从K线重算并与存储值比对
         （MACD 日/周/月重算已并入本机制，由 macd 字段的 checks 声明触发）

--fix: 对 cross_recompute 检出的不一致行，以重算值回写（泛化到所有声明了的计算型字段，
       含MACD日/周/月）；其余问题（日期缺失、空值、越界）只报告不修复。
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import common
from lib import executor
from computed import function_outputs
from storage import get_adapter, StorageError

TOL = 0.02  # 数值比对容差（存储值保留2位小数）
_MACD_NUMERIC_OUTPUTS = ("DIFF", "DEA", "MACD")  # macd 输出中的数值列，其余为标签列精确比对


# ==================== 检查项 ====================
def check_date_continuity(cfg, sheet1_pairs, kline_dates):
    """检查日期连续性：Sheet中的日期是否覆盖了所有交易日
    只检查Sheet最早日期到最晚日期之间的交易日，不检查Sheet开始前的历史。"""
    issues = []
    kline_set = set(kline_dates)
    sheet_set = set()

    for _, row in sheet1_pairs:
        d = row[0] if row else ""
        std = common.to_standard(cfg, d) if d else None
        if std:
            sheet_set.add(std)

    if not sheet_set:
        issues.append("无法从Sheet中解析出任何有效日期，请检查date_format配置")
        return issues

    # 只检查Sheet数据范围内的交易日
    min_sheet = min(sheet_set)
    max_sheet = max(sheet_set)
    kline_in_range = {d for d in kline_set if min_sheet <= d <= max_sheet}

    # 找出K线中有但Sheet中没有的交易日（仅在Sheet范围内）
    missing = sorted(kline_in_range - sheet_set)
    if missing:
        issues.append(f"缺失交易日({len(missing)}天): {', '.join(missing[:10])}" +
                      ("..." if len(missing) > 10 else ""))

    # 检查Sheet中是否有K线没有的日期（可能是错误数据）
    extra = sorted(sheet_set - kline_set)
    if extra:
        issues.append(f"Sheet中有非交易日数据({len(extra)}天): {', '.join(extra[:5])}")

    return issues


def _close_col(cfg, sheet):
    """layout 中 close 字段（单列引用）所在的列号；未声明返回 None"""
    for i, fid, output in executor.field_columns(executor.layout_elements(cfg, sheet)):
        if fid == "close" and output is None:
            return i
    return None


def check_close_consistency(cfg, sheet1_pairs, sheet2_pairs):
    """检查行情Sheet与MACD Sheet的收盘价是否一致（对 layout 中声明的 close 列生效）"""
    issues = []
    c1, c2 = _close_col(cfg, "quote"), _close_col(cfg, "macd")
    if c1 is None or c2 is None:
        return issues  # layout 未在两个 sheet 都声明 close 列，跳过
    s1_map = {}
    for _, row in sheet1_pairs:
        if row and row[0]:
            s1_map[row[0]] = row[c1] if len(row) > c1 else ""

    for _, row in sheet2_pairs:
        if row and row[0]:
            date = row[0]
            close2 = row[c2] if len(row) > c2 else ""
            if date in s1_map:
                close1 = s1_map[date]
                try:
                    if close1 and close2 and abs(float(close1) - float(close2)) > 0.01:
                        issues.append(f"{date}: Sheet1收盘={close1} vs Sheet2收盘={close2} 不一致")
                except (ValueError, TypeError):
                    pass
    return issues


def _field_sheet_columns(cfg, fid):
    """字段在各 sheet layout 中的列: {sheet: [(列号, output)]}"""
    result = {}
    for sheet in ("quote", "macd"):
        cols = [(i, o) for i, f, o in executor.field_columns(executor.layout_elements(cfg, sheet))
                if f == fid]
        if cols:
            result[sheet] = cols
    return result


def check_field_declared(cfg, pairs_map):
    """字段级检查：non_null（markets 例外）、range，由 field_entry.checks 声明驱动。
    返回 (issues_nonnull, issues_range)"""
    issues_nonnull = []
    issues_range = []
    market = cfg["stock"]["market"]
    for fid, entry in executor.field_map(cfg).items():
        checks = entry.get("checks") or []
        if not checks:
            continue
        name = entry.get("name", fid)
        sheet_cols = _field_sheet_columns(cfg, fid)
        if not sheet_cols:
            continue
        for chk in checks:
            ctype = chk.get("type")
            if ctype == "non_null":
                markets = chk.get("markets")
                if markets and market not in markets:
                    continue  # markets 例外：仅对指定市场强制（如港/美股豁免）
                # 不可回填字段（backfill_available: false）在接入快照日之前的数据行
                # 天然为空（当时未接入，无法补采），不算问题
                snapshot_date = (cfg.get("registry_snapshot") or {}).get("snapshot_date")
                skip_before = snapshot_date if entry.get("backfill_available") is False else None
                for sheet, cols in sheet_cols.items():
                    for _, row in pairs_map[sheet]:
                        if not row or not row[0]:
                            continue
                        if skip_before:
                            std = common.to_standard(cfg, row[0])
                            if std and std < skip_before:
                                continue
                        for col, _ in cols:
                            if col >= len(row) or not str(row[col]).strip():
                                issues_nonnull.append(f"{row[0]}: {name}为空({sheet}表第{col}列)")
            elif ctype == "range":
                lo, hi = chk.get("min"), chk.get("max")
                for sheet, cols in sheet_cols.items():
                    for _, row in pairs_map[sheet]:
                        if not row or not row[0]:
                            continue
                        for col, _ in cols:
                            if col >= len(row) or not str(row[col]).strip():
                                continue
                            try:
                                v = float(row[col])
                            except (ValueError, TypeError):
                                issues_range.append(f"{row[0]}: {name}={row[col]} 非数值，无法做区间校验")
                                continue
                            if (lo is not None and v < lo) or (hi is not None and v > hi):
                                issues_range.append(f"{row[0]}: {name}={v} 超出区间[{lo}, {hi}]")
    return issues_nonnull, issues_range


def _values_mismatch(stored, calc, numeric):
    """比对存储值与重算值：数值列按容差，标签列按字符串精确。stored 空 = 不比对（由 non_null 覆盖）"""
    if stored is None or str(stored).strip() == "":
        return False
    if numeric:
        try:
            return abs(float(stored) - float(calc)) > TOL
        except (ValueError, TypeError):
            return True
    return str(stored) != str(calc)


def check_cross_recompute(cfg, pairs_map, kline, insecure=False):
    """cross_recompute：计算型字段用注册函数从K线重算并与存储值比对。
    MACD 日/周/月重算已并入本机制（由 macd 字段的 checks 声明触发）。
    返回 (issues, fixes, notes)；fixes 供 --fix 回写: (sheet, 行号, 列号, 重算值)"""
    issues = []
    fixes = []
    notes = []
    kline_dates = {r[0] for r in kline}

    for fid, entry in executor.field_map(cfg).items():
        checks = entry.get("checks") or []
        if not any(c.get("type") == "cross_recompute" for c in checks):
            continue
        name = entry.get("name", fid)
        comp = entry.get("computed") or {}
        func = comp.get("function")
        if not func or func == "identity":
            notes.append(f"字段 {fid}({name}): 采集型字段的 cross_recompute 暂不支持（仅计算型），跳过")
            continue
        sheet_cols = _field_sheet_columns(cfg, fid)
        if not sheet_cols:
            notes.append(f"字段 {fid}({name}): 声明了 cross_recompute 但未出现在 layout 中，跳过")
            continue
        outputs = function_outputs(func)  # None=单值；list=多列输出（如MACD六列）

        for sheet, cols in sheet_cols.items():
            for row_idx, row in pairs_map[sheet]:
                if not row or not row[0]:
                    continue
                display_date = row[0]
                std_date = common.to_standard(cfg, display_date)
                if not std_date or std_date not in kline_dates:
                    continue
                try:
                    value, _ = executor.recompute_field(cfg, fid, std_date, kline, insecure=insecure)
                except Exception as e:
                    notes.append(f"{display_date}: 字段 {fid} 重算失败({e})，跳过本行")
                    continue
                for col, output in cols:
                    stored = row[col] if col < len(row) else ""
                    if outputs:
                        if not isinstance(value, dict):
                            continue
                        calc = value.get(output, "")
                        numeric = output in _MACD_NUMERIC_OUTPUTS
                    else:
                        calc = value
                        numeric = True
                    if calc == "" or calc is None:
                        continue
                    if _values_mismatch(stored, calc, numeric):
                        label = f"{name}.{output}" if output else name
                        issues.append(f"{display_date}: {label} 存储值={stored} 重算值={calc}")
                        fixes.append((sheet, row_idx, col, calc))
    return issues, fixes, notes


# ==================== 主流程 ====================
def main():
    parser = argparse.ArgumentParser(
        description="股票数据完整性核查（日期连续性/收盘一致性/字段checks: non_null、range、cross_recompute）")
    parser.add_argument("config", help="配置文件路径 (*.json)")
    parser.add_argument("--last", type=int, metavar="N", help="只核查最近N天")
    parser.add_argument("--fix", action="store_true",
                        help="尝试修复（对 cross_recompute 检出的不一致行以重算值回写，其余问题只报告）")
    parser.add_argument("--insecure", action="store_true",
                        help="关闭SSL证书校验（不推荐，仅个别企业网络）")
    args = parser.parse_args()

    cfg = common.load_config(args.config)
    stock = cfg["stock"]
    insecure = args.insecure or cfg.get("insecure", False)

    errors = executor.validate_config(cfg)
    if errors:
        print("❌ 配置不符合 schema 2.0.0 可执行要求:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    try:
        adapter = get_adapter(cfg)
    except StorageError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print("=" * 60)
    print(f"{stock['name']} {stock['code'].upper()} 数据完整性核查")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 读取K线
    print("\n[1/5] 获取K线数据(交易日历)...")
    try:
        kline, _ = common.fetch_kline(stock["code"], insecure=insecure)
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)
    kline_dates = [r[0] for r in kline]
    print(f"  K线范围: {kline_dates[0]} ~ {kline_dates[-1]}, 共{len(kline_dates)}天")

    # 2. 读取Sheet1和Sheet2
    print("\n[2/5] 读取存储数据...")
    sheet1_rows = adapter.read_sheet("quote")
    sheet2_rows = adapter.read_sheet("macd")
    print(f"  Sheet1: {len(sheet1_rows)}行 (含表头)")
    print(f"  Sheet2: {len(sheet2_rows)}行 (含表头)")

    # (原始行号, 行内容) 对，跳过表头；--fix 回写需要原始行号
    sheet1_pairs = [(i, r) for i, r in enumerate(sheet1_rows) if i > 0 and r and r[0]]
    sheet2_pairs = [(i, r) for i, r in enumerate(sheet2_rows) if i > 0 and r and r[0]]

    if args.last:
        sheet1_pairs = sheet1_pairs[-args.last:]
        sheet2_pairs = sheet2_pairs[-args.last:]
        print(f"  [仅核查最近{args.last}天]")
    pairs_map = {"quote": sheet1_pairs, "macd": sheet2_pairs}

    # 3. 日期连续性
    print("\n[3/5] 检查日期连续性...")
    issues_dates = check_date_continuity(cfg, sheet1_pairs, kline_dates)
    if issues_dates:
        for i in issues_dates:
            print(f"  ❌ {i}")
    else:
        print("  ✅ 日期连续，无缺失交易日")

    # 4. 收盘价一致性 & 字段级检查（non_null / range）
    print("\n[4/5] 检查收盘价一致性 & 字段级检查(non_null/range)...")
    issues_close = check_close_consistency(cfg, sheet1_pairs, sheet2_pairs)
    issues_nonnull, issues_range = check_field_declared(cfg, pairs_map)

    for i in issues_close:
        print(f"  ❌ {i}")
    for i in issues_nonnull:
        print(f"  ⚠️ {i}")
    for i in issues_range:
        print(f"  ❌ {i}")

    if not issues_close and not issues_nonnull and not issues_range:
        print("  ✅ 收盘价一致、字段级检查通过")

    # 5. cross_recompute（计算型字段重算比对，含MACD日/周/月）
    print("\n[5/5] cross_recompute 重算验证(计算型字段)...")
    issues_recompute, fixes, notes = check_cross_recompute(cfg, pairs_map, kline, insecure=insecure)
    for n in notes:
        print(f"  ⚠️ {n}")
    if issues_recompute:
        for i in issues_recompute:
            print(f"  ❌ {i}")
    else:
        print("  ✅ 声明了 cross_recompute 的字段与重算结果一致")

    # --fix: 以重算值回写不一致行（泛化到所有声明了 cross_recompute 的计算型字段）
    if args.fix:
        if fixes:
            print(f"\n[--fix] 以重算值回写不一致单元格(共{len(fixes)}处)...")
            try:
                for sheet, row_idx, col, calc in fixes:
                    adapter.update_cell(sheet, row_idx, col, calc)
                    print(f"  已回写 {sheet} 行{row_idx} 列{col}: {calc}")
                print("  回写完成，建议重新运行核查确认")
            except StorageError as e:
                print(f"  ❌ 回写失败: {e}")
                sys.exit(1)
        else:
            print("\n[--fix] 无 cross_recompute 不一致项，无需修复")
            other = len(issues_dates) + len(issues_close) + len(issues_nonnull) + len(issues_range)
            if other:
                print("  （日期缺失/空值/越界/收盘不一致问题不支持自动修复，请人工处理）")

    # 汇总
    print("\n" + "=" * 60)
    total_issues = (len(issues_dates) + len(issues_close) + len(issues_nonnull)
                    + len(issues_range) + len(issues_recompute))
    if total_issues == 0:
        print("✅ 全部检查通过，数据完整性正常")
    else:
        print(f"❌ 发现 {total_issues} 个问题需要关注")
        print(f"  日期连续性: {len(issues_dates)}")
        print(f"  收盘价一致性: {len(issues_close)}")
        print(f"  空值(non_null): {len(issues_nonnull)}")
        print(f"  越界(range): {len(issues_range)}")
        print(f"  重算不一致(cross_recompute): {len(issues_recompute)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
