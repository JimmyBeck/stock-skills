# -*- coding: utf-8 -*-
"""计算型字段的公式函数（受控名单，禁止 eval）。
函数名单与 registry_schema.md 2.0.0 对齐：
  identity / diff / pct_change / amplitude / pe_static / macd
新公式 = 在此注册新函数（显式代码动作，符合边界契约"方法归 Skill 1"）。

函数签名：fn(ctx, params) -> 单值（float）或多列 dict（如 macd 的 6 输出列）
ctx 为执行器 ExecContext，提供：
  ctx.resolve_input(name)  解析输入："prev_close" 内建衍生输入 或 其他 field_id
  ctx.prev_close()         调整后前收（除权日按 config.params.expected_div 处理）
  ctx.kline / ctx.today_idx / ctx.closes / ctx.dates / ctx.cfg
  ctx.collected            identity 用：sources 采集链已取到的值
"""

from lib import common


def fn_identity(ctx, params):
    """直采占位：值直接来自 sources 采集链，computed 仅声明走执行器管线"""
    return ctx.collected


def fn_diff(ctx, params):
    """base - ref，如 close - prev_close（涨跌额）"""
    base = ctx.resolve_input(params["base"])
    ref = ctx.resolve_input(params["ref"])
    return round(base - ref, 2)


def fn_pct_change(ctx, params):
    """(base - ref) / ref × 100（涨跌%）"""
    base = ctx.resolve_input(params["base"])
    ref = ctx.resolve_input(params["ref"])
    if not ref:
        raise ValueError("pct_change 的 ref 为0或空，无法计算")
    return round((base - ref) / ref * 100, 2)


def fn_amplitude(ctx, params):
    """(high - low) / prev_close × 100（振幅%），输入固定为当根K线高/低与 prev_close"""
    row = ctx.kline[ctx.today_idx]
    high, low = float(row[3]), float(row[4])
    prev = ctx.prev_close()
    if not prev:
        raise ValueError("prev_close 为0或空，无法计算振幅")
    return round((high - low) / prev * 100, 2)


def fn_pe_static(ctx, params):
    """close / eps；eps_param 指定从 config["params"][eps_param] 读取（per-stock，每季度手动更新）"""
    base = ctx.resolve_input(params.get("base", "close"))
    eps_key = params.get("eps_param", "eps")
    eps = ctx.cfg.get("params", {}).get(eps_key)
    if eps is None:
        raise ValueError(f"config params 缺少 {eps_key}（每季度财报后手动更新），无法计算静态市盈率")
    return round(base / float(eps), 2)


def fn_macd(ctx, params):
    """标准 EMA(12,26,9)：DIFF=EMA12-EMA26，DEA=EMA(DIFF,9)，柱=2×(DIFF-DEA)。
    输入为前复权 close 序列（需≥1500条确保收敛）；周/月用 provisional close 方法
    （与 lib/common 的算法一致）。输出固定 6 列 DIFF/DEA/MACD/ZERO/CROSS/MOMENTUM。"""
    period = params.get("period", "daily")
    dates = ctx.dates[:ctx.today_idx + 1]
    closes = ctx.closes[:ctx.today_idx + 1]
    if period == "daily":
        diff_l, dea_l, macd_l = common.calc_macd(closes)
    elif period == "weekly":
        diff_l, dea_l, macd_l = common.calc_rolling_macd_provisional(dates, closes, common.get_week_key)
    elif period == "monthly":
        diff_l, dea_l, macd_l = common.calc_rolling_macd_provisional(dates, closes, common.get_month_key)
    else:
        raise ValueError(f"macd 不支持 period={period}（仅 daily/weekly/monthly）")

    i = ctx.today_idx
    vd, ve, vm = round(diff_l[i], 2), round(dea_l[i], 2), round(macd_l[i], 2)
    return {
        "DIFF": vd,
        "DEA": ve,
        "MACD": vm,
        "ZERO": common.zero_label(vd),
        "CROSS": common.cross_label(diff_l[i], dea_l[i], diff_l[i - 1], dea_l[i - 1]) if i > 0 else "",
        "MOMENTUM": common.momentum_label(macd_l[i], macd_l[i - 1]) if i > 0 else "",
    }


# 函数注册表：名字 -> (函数, 输出列声明)。输出列 None = 单值字段。
FUNCTIONS = {
    "identity": (fn_identity, None),
    "diff": (fn_diff, None),
    "pct_change": (fn_pct_change, None),
    "amplitude": (fn_amplitude, None),
    "pe_static": (fn_pe_static, None),
    "macd": (fn_macd, ["DIFF", "DEA", "MACD", "ZERO", "CROSS", "MOMENTUM"]),
}

# 输入依赖声明（文档/审计用途）：
#   diff/pct_change: params.base, params.ref（field_id 或内建输入 prev_close）
#   amplitude:       当根K线 high/low + prev_close（固定）
#   pe_static:       params.base + config["params"][params.eps_param]
#   macd:            前复权 close 序列（K线，≥1500条收敛）
