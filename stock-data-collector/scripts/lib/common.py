#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三脚本共享模块（update_daily / check_integrity / onboard_stock 共用）
集中：网络请求(带SSL开关/超时/重试)、K线获取、MACD计算、日期互转、配置加载
依赖: 仅Python标准库
"""

import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime


# ==================== 网络请求 ====================
def fetch(url, insecure=False, timeout=30, encoding="utf-8", retries=2, headers=None):
    """HTTP GET，返回解码后的文本。
    SSL证书校验默认开启；insecure=True 时关闭（仅应对个别企业网络，不推荐）。
    headers 可追加/覆盖请求头（如新浪实时必须的 Referer）。"""
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx = None  # urlopen 默认即校验证书
    req_headers = {"User-Agent": "Mozilla/5.0"}
    if headers:
        req_headers.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                return resp.read().decode(encoding)
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1)
    raise last_err


def fetch_kline(code, count=2000, insecure=False, extend_history=True):
    """
    获取日K线数据（不复权，真实成交价；平安证券口径）。
    返回 (kline, qt_arr): qt_arr为响应内嵌的实时行情数组(与qt.gtimg.cn字段索引一致),可能为None
    失败抛 ValueError，由调用方决定如何报错退出。

    腾讯接口单次数上限约2000条（约8年）。月线MACD的EMA需要更深历史才能充分收敛，
    extend_history=True 时（默认）自动用搜狐历史行情前补更早的K线（同样不复权）；
    搜狐不可用时静默回退为仅腾讯2000条（月/周线MACD最后一位可能有±0.01偏差）。
    """
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{count},"
    raw = json.loads(fetch(url, insecure=insecure, timeout=30))
    node = raw.get("data", {}).get(code)
    if not node:
        raise ValueError(
            f"K线接口未返回 {code} 数据，返回的keys: {list(raw.get('data', {}).keys())}"
            "；请核对股票代码格式: A股=sh600900/sz000001, 港股=hk00700, 美股=usAAPL.OQ")
    kline = node.get("day") or node.get("qfqday")
    if not kline:
        raise ValueError(f"K线数据为空, 节点keys: {list(node.keys())}")
    qt_arr = (node.get("qt") or {}).get(code)
    if not isinstance(qt_arr, list):
        qt_arr = None
    if extend_history:
        kline = _prepend_sohu_history(code, kline, insecure=insecure)
    return kline, qt_arr


def _prepend_sohu_history(code, kline, insecure=False):
    """用搜狐历史行情前补腾讯2000条上限之前的K线（仅A股；失败静默返回原kline）。
    搜狐列: 0日期 1开 2收 3涨跌额 4涨跌幅 5最低 6最高 7量(手) 8额(万) 9换手 →
    腾讯列: 0日期 1开 2收 3高 4低 5量 ... 9额（额换算: 万→元）"""
    if not (code.startswith("sh") or code.startswith("sz")):
        return kline
    try:
        first_date = kline[0][0]
        url = (f"https://q.stock.sohu.com/hisHq?code=cn_{code[2:]}"
               f"&start=19900101&end={first_date.replace('-', '')}")
        raw = fetch(url, insecure=insecure, timeout=60)
        data = json.loads(raw)
        if not isinstance(data, list) or not data or data[0].get("status") != 0:
            return kline
        older = []
        for r in reversed(data[0].get("hq") or []):  # 搜狐返回倒序
            if r[0] >= first_date:
                continue
            row = [r[0], r[1], r[2], r[6], r[5], r[7]]
            while len(row) < 10:
                row.append("")
            try:
                row[9] = str(float(r[8]) * 10000)  # 万→元，与腾讯K线第9列单位一致
            except (ValueError, TypeError):
                row[9] = ""
            older.append(row)
        if older:
            return older + list(kline)
    except Exception:
        pass  # 前补失败不影响主流程
    return kline


def fetch_realtime_a(code, insecure=False):
    """备用: 直接请求实时行情(腾讯qt.gtimg.cn, GBK编码)。仅A股字段映射可靠。"""
    raw = fetch(f"http://qt.gtimg.cn/q={code}", insecure=insecure, timeout=10, encoding="gbk")
    data_str = raw.split('"')[1]
    return data_str.split("~")


# ==================== MACD计算 ====================
def ema(values, period):
    alpha = 2.0 / (period + 1)
    result = [values[0]]
    for i in range(1, len(values)):
        result.append(alpha * values[i] + (1 - alpha) * result[-1])
    return result


def calc_macd(closes):
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    diff = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    dea = ema(diff, 9)
    macd = [2 * (d - e) for d, e in zip(diff, dea)]
    return diff, dea, macd


def calc_rolling_macd_provisional(dates, closes, period_func):
    """
    Provisional close方法:
    对于每个交易日,序列 = [所有已完成周期的收盘价] + [当日收盘价作为本周/月临时收盘价]
    """
    period_groups = {}
    for i, d in enumerate(dates):
        key = period_func(d)
        if key not in period_groups:
            period_groups[key] = []
        period_groups[key].append((i, closes[i]))
    period_keys_in_order = sorted(period_groups.keys())

    diff_list = [0.0] * len(dates)
    dea_list = [0.0] * len(dates)
    macd_list = [0.0] * len(dates)

    for idx in range(len(dates)):
        current_key = period_func(dates[idx])
        series_closes = []
        for pk in period_keys_in_order:
            if pk < current_key:
                series_closes.append(period_groups[pk][-1][1])
            elif pk == current_key:
                series_closes.append(closes[idx])  # 当日收盘价作为临时收盘价
                break
            else:
                break
        if len(series_closes) >= 30:
            diff, dea, macd = calc_macd(series_closes)
            diff_list[idx] = diff[-1]
            dea_list[idx] = dea[-1]
            macd_list[idx] = macd[-1]
    return diff_list, dea_list, macd_list


def get_week_key(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def get_month_key(date_str):
    return date_str[:7]


# ==================== 标签函数 ====================
def zero_label(diff_val):
    if diff_val > 0:
        return '零轴上方'
    elif diff_val < 0:
        return '零轴下方'
    else:
        return '零轴附近'


def cross_label(diff_val, dea_val, prev_diff, prev_dea):
    if prev_diff <= prev_dea and diff_val > dea_val:
        return '金叉'
    elif prev_diff >= prev_dea and diff_val < dea_val:
        return '死叉'
    else:
        return '无'


def momentum_label(current_macd, prev_macd):
    if current_macd > prev_macd:
        return '动能上升'
    elif current_macd < prev_macd:
        return '动能下降'
    else:
        return '动能未变'


# ==================== 日期工具 ====================
def short_date_from_standard(standard_date):
    dt = datetime.strptime(standard_date, "%Y-%m-%d")
    return f"{dt.year % 100}.{dt.month}.{dt.day}"


def standard_from_short(short):
    """short(YY.M.D)转标准(YYYY-MM-DD)，格式不符返回None"""
    parts = short.split(".")
    if len(parts) != 3:
        return None
    year = 2000 + int(parts[0])
    month = int(parts[1])
    day = int(parts[2])
    return f"{year:04d}-{month:02d}-{day:02d}"


def to_display(cfg, standard_date):
    return short_date_from_standard(standard_date) if cfg["date_format"] == "short" else standard_date


def to_standard(cfg, display_date):
    if cfg.get("date_format") == "short":
        return standard_from_short(display_date)
    return display_date


# ==================== 配置加载 ====================
def load_config(path):
    """
    加载并校验配置。存储配置解析规则:
      - 有 storage 段: 按 storage.type / storage.options 使用
      - 无 storage 段但有 doc 段(旧版配置): 自动映射为 tdoc adapter 并打印警告
      - 两者皆无: 报错退出
    本函数不校验具体存储的可用性(如tdoc CLI目录)，由 adapter 初始化时校验。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        print(f"❌ 配置文件不存在: {path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ 配置文件不是合法 JSON: {path}（{e}）")
        sys.exit(1)
    if "stock" not in cfg:
        print(f"❌ 配置缺少 stock 段: {path}")
        sys.exit(1)
    for k in ("code", "name", "market"):
        if k not in cfg["stock"]:
            print(f"❌ 配置 stock 段缺少 {k}")
            sys.exit(1)
    cfg["stock"]["market"] = cfg["stock"]["market"].lower()
    if cfg["stock"]["market"] not in ("a", "hk", "us"):
        print(f"❌ 未知市场: {cfg['stock']['market']} (仅支持 a/hk/us)")
        sys.exit(1)
    cfg.setdefault("params", {})
    cfg.setdefault("sector", None)
    cfg.setdefault("date_format", "short")  # short=YY.M.D(沿用长江电力) / iso=YYYY-MM-DD；新股票建议在配置中显式写 iso

    if "storage" in cfg:
        stype = cfg["storage"].get("type")
        if stype not in ("csv", "tdoc"):
            print(f"❌ 未知 storage.type: {stype} (仅支持 csv/tdoc)")
            sys.exit(1)
        cfg["storage"].setdefault("options", {})
    elif "doc" in cfg:
        print("⚠️ 配置使用旧版 doc 段，已自动映射为 tdoc 存储；建议迁移为 storage 段")
        options = dict(cfg["doc"])
        if cfg.get("tdocs_dir"):
            options["tdocs_dir"] = cfg["tdocs_dir"]
        cfg["storage"] = {"type": "tdoc", "options": options}
    else:
        print(f"❌ 配置缺少 storage 段: {path}")
        sys.exit(1)

    cfg["_config_dir"] = os.path.dirname(os.path.abspath(path))
    return cfg


def field_enabled(cfg, field_id):
    """config["fields"] 的 enabled 开关；未配置 fields 段或未列出该字段时默认启用"""
    for f in cfg.get("fields") or []:
        if f.get("field_id") == field_id:
            return f.get("enabled", True)
    return True
