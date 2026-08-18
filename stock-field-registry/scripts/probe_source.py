#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探索性信源验证工具 — Skill 1 (stock-field-registry) 专用

用途：在字段调研阶段，试跑各种数据源，取样本值，做多源交叉验证。
     产出 sample_values 和 cross_validated 证据，写入 registry 条目。

注意：本工具只读不写，不触碰生产存储（腾讯文档等）。

使用方法:
    # 验证单个源（--field 可选，显示该字段在该源的取值）
    python3 probe_source.py --source tencent_qt --code sh600900 --field close

    # 多源交叉验证（实时模式，各源取最新值）
    python3 probe_source.py --cross --code sh600900 --field close

    # 历史模式交叉验证（所有源都取该日历史值，取不到该日数据的源跳过并明示）
    python3 probe_source.py --cross --code sh600900 --field close --date 2026-08-14

    # 取样本值（腾讯K线最近N天）
    python3 probe_source.py --sample --code sh600900 --field close --count 5

    # 探测字段索引（不知道字段在数组哪个位置时）
    python3 probe_source.py --dump-qt --code sh600900

退出码: 交叉验证不一致/证据不足时退出码为 1，一致为 0。

依赖: 仅 Python 标准库
"""

import argparse
import json
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta


# ==================== 信源定义 ====================
SOURCES = {
    "tencent_kline": {
        "name": "腾讯日K线",
        "url": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{count},",
        "encoding": "utf-8",
        "markets": ["a", "hk", "us"],
    },
    "tencent_qt": {
        "name": "腾讯实时行情",
        "url": "http://qt.gtimg.cn/q={code}",
        "encoding": "gbk",
        "markets": ["a"],
    },
    "sina": {
        "name": "新浪实时行情",
        "url": "http://hq.sinajs.cn/list={code}",
        "encoding": "gbk",
        # 新浪接口必须带 Referer 头，否则返回 403
        "referer": "https://finance.sina.com.cn",
        "markets": ["a"],
    },
    "eastmoney": {
        "name": "东方财富个股",
        # 实时: push2.eastmoney.com/api/qt/stock/get（被WAF拦截时回退 push2delay 延时行情）
        # 历史K线: push2his.eastmoney.com/api/qt/stock/kline/get
        "url": "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&klt=101&fqt=1",
        "encoding": "utf-8",
        "markets": ["a"],
    },
    "sohu": {
        "name": "搜狐历史行情",
        # 历史K线: q.stock.sohu.com/hisHq（东财历史K线被WAF封禁时的历史备胎源）
        "url": "https://q.stock.sohu.com/hisHq?code=cn_{code6}&start={start}&end={end}",
        "encoding": "utf-8",
        "markets": ["a"],
    },
}

# 腾讯 qt 数组字段索引（~ 分隔后）
QT_MAP = {"open": 5, "close": 3, "prev_close": 4, "high": 33, "low": 34,
          "change": 31, "change_pct": 32, "volume": 6, "amount": 35,
          "turnover_rate": 38, "amplitude": 43, "circ_market_cap": 44,
          "market_cap": 45, "volume_ratio": 49}

# 新浪实时字段名
SINA_MAP = {"open": "open", "prev_close": "prev_close", "close": "close",
            "high": "high", "low": "low", "volume": "volume", "amount": "amount"}

# 腾讯K线行字段索引
KLINE_MAP = {"open": 1, "close": 2, "high": 3, "low": 4, "volume": 5}

# 东财K线行字段索引（klines 行逗号分隔）
EM_KLINE_MAP = {"open": 1, "close": 2, "high": 3, "low": 4, "volume": 5,
                "amount": 6, "amplitude": 7, "change_pct": 8, "change": 9,
                "turnover_rate": 10}

# 搜狐 hisHq K线行字段索引（hq 行 JSON 数组，日期倒序）
# 实测确认：7成交量(手) 8成交金额(万) 9换手率%(带%后缀)；索引10含义未确认，不映射
SOHU_KLINE_MAP = {"open": 1, "close": 2, "change": 3, "change_pct": 4,
                  "low": 5, "high": 6, "volume": 7, "amount": 8,
                  "turnover_rate": 9}

# 交叉验证分字段阈值: (模式, 阈值)，与 references/source_verification_checklist.md 对齐
# abs = 绝对偏差；rel = 相对偏差（最大差 / 最大值），用于市值/成交额/成交量等大数值字段
FIELD_THRESHOLDS = {
    "open": ("abs", 0.01), "close": ("abs", 0.01), "high": ("abs", 0.01),
    "low": ("abs", 0.01), "prev_close": ("abs", 0.01), "change": ("abs", 0.01),
    "change_pct": ("abs", 0.05), "amplitude": ("abs", 0.1),
    "turnover_rate": ("abs", 0.1), "volume_ratio": ("abs", 0.1),
    "volume": ("rel", 0.001), "amount": ("rel", 0.001),
    "market_cap": ("rel", 0.001), "circ_market_cap": ("rel", 0.001),
}
DEFAULT_THRESHOLD = ("rel", 0.001)  # 未配置字段默认按相对偏差 0.1%

_INSECURE = False  # --insecure 时置 True，关闭 SSL 证书校验


def fetch(url, encoding="utf-8", timeout=15, referer=None):
    """通用 fetch，返回原始文本。默认校验 SSL 证书，--insecure 时关闭"""
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    if _INSECURE:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return resp.read().decode(encoding)


# ==================== 腾讯 K线 ====================
def fetch_tencent_kline(code, count=2000):
    """获取日K线，返回 (kline, qt_arr)"""
    url = SOURCES["tencent_kline"]["url"].format(code=code, count=count)
    raw = json.loads(fetch(url))
    node = raw.get("data", {}).get(code)
    if not node:
        print(f"❌ K线接口未返回 {code} 数据")
        print(f"   返回 keys: {list(raw.get('data', {}).keys())}")
        return None, None
    kline = node.get("day") or node.get("qfqday")
    qt_arr = (node.get("qt") or {}).get(code)
    if not isinstance(qt_arr, list):
        qt_arr = None
    return kline, qt_arr


def dump_qt_fields(qt_arr):
    """打印 qt 数组所有非空字段，用于探测字段索引"""
    if not qt_arr:
        print("qt 数组为空")
        return
    print(f"qt 数组长度: {len(qt_arr)}")
    print("=" * 50)
    for i, v in enumerate(qt_arr):
        if v:
            print(f"  qt[{i:2d}] = {v}")


def qt_value(qt_arr, field):
    """从腾讯 qt 数组取字段值；成交额 qt[35] 为 '万/亿/元' 格式，取第3部分（元）"""
    idx = QT_MAP.get(field)
    if idx is None or not qt_arr or idx >= len(qt_arr) or not qt_arr[idx]:
        return None
    v = qt_arr[idx]
    if field == "amount":
        v = v.split("/")[-1]
    return v


# ==================== 腾讯实时 ====================
def fetch_tencent_qt(code):
    """获取腾讯实时行情，返回 ~ 分隔的数组"""
    url = SOURCES["tencent_qt"]["url"].format(code=code)
    raw = fetch(url, encoding="gbk")
    if '"' not in raw:
        raise ValueError(f"腾讯实时返回异常: {raw[:80]}")
    data_str = raw.split('"')[1]
    if not data_str:
        raise ValueError(f"腾讯实时返回空数据（代码可能有误）: {raw[:80]}")
    return data_str.split("~")


# ==================== 新浪实时 ====================
def fetch_sina(code):
    """获取新浪实时行情，返回字段字典（必须带 Referer 头，否则 403）"""
    url = SOURCES["sina"]["url"].format(code=code)
    raw = fetch(url, encoding="gbk", referer=SOURCES["sina"]["referer"])
    if '"' not in raw:
        raise ValueError(f"新浪返回异常: {raw[:80]}")
    parts = raw.split('"')[1].split(",")
    if len(parts) < 10:
        raise ValueError(f"新浪返回字段不足（代码可能有误）: {raw[:80]}")
    return {
        "name": parts[0],
        "open": parts[1],
        "prev_close": parts[2],
        "close": parts[3],
        "high": parts[4],
        "low": parts[5],
        "volume": parts[8],
        "amount": parts[9],
    }


# ==================== 东方财富 ====================
def code_to_secid(code):
    """股票代码转东财 secid：沪市 1.code、深市 0.code"""
    if code.startswith("sh"):
        return "1." + code[2:]
    if code.startswith("sz"):
        return "0." + code[2:]
    raise ValueError(f"暂不支持 {code} 的 secid 转换（目前仅支持 sh/sz 开头的A股代码）")


def fetch_eastmoney_rt(code):
    """东财实时行情，返回字段字典（价格/百分比类字段已 ÷100 还原，市值已换算为亿元）
    push2 被WAF拦截时回退 push2delay 延时行情（接口格式相同，数据延时约15分钟）"""
    secid = code_to_secid(code)
    path = ("/api/qt/stock/get?secid={secid}"
            "&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f116,f117,f168,f170").format(secid=secid)
    d = None
    last_err = None
    for host in ("push2.eastmoney.com", "push2delay.eastmoney.com"):
        try:
            raw = json.loads(fetch("https://" + host + path))
            d = raw.get("data")
            if d:
                break
        except Exception as e:
            last_err = e
    if not d:
        raise ValueError(f"东财实时返回异常: {last_err}")

    def scaled(key):
        """价格/百分比类字段原始值 ×100"""
        v = d.get(key)
        if v in (None, "-"):
            return None
        return v / 100

    def to_yi(key):
        """市值类字段原始单位为元，换算为亿元（与腾讯 qt 口径一致）"""
        v = d.get(key)
        if v in (None, "-"):
            return None
        return v / 1e8

    return {
        "name": d.get("f58"),
        "close": scaled("f43"), "open": scaled("f46"),
        "high": scaled("f44"), "low": scaled("f45"),
        "prev_close": scaled("f60"),
        "volume": d.get("f47"),            # 手
        "amount": d.get("f48"),            # 元
        "turnover_rate": scaled("f168"),   # %
        "change_pct": scaled("f170"),      # %
        "market_cap": to_yi("f116"),       # 亿元
        "circ_market_cap": to_yi("f117"),  # 亿元
    }


def fetch_eastmoney_kline(code, beg=None, end=None, count=5):
    """东财历史日K线（前复权），返回 klines 行列表（逗号分隔已拆分）"""
    secid = code_to_secid(code)
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
           "&fields1=f1,f2,f3,f4,f5,f6"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
           "&klt=101&fqt=1&lmt={count}&beg={beg}&end={end}").format(
               secid=secid, count=count,
               beg=(beg or "0").replace("-", ""), end=(end or "20500101").replace("-", ""))
    raw = json.loads(fetch(url))
    data = raw.get("data")
    if not data or not data.get("klines"):
        return []
    return [k.split(",") for k in data["klines"]]


# ==================== 搜狐历史 ====================
def fetch_sohu_kline(code, beg=None, end=None):
    """搜狐历史日K线（hisHq），返回 hq 行列表（JSON数组行，**日期倒序，最新在前**）。
    行内金额类原始单位：成交量=手、成交金额=万元；涨跌幅/换手率带 % 后缀。"""
    if not code.startswith(("sh", "sz")):
        raise ValueError(f"搜狐 hisHq 仅支持 A股 sh/sz 代码，收到: {code}")
    url = SOURCES["sohu"]["url"].format(
        code6=code[2:],
        start=(beg or "19900101").replace("-", ""),
        end=(end or "20500101").replace("-", ""))
    raw = json.loads(fetch(url))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"搜狐K线返回异常: {str(raw)[:80]}")
    node = raw[0]
    if node.get("status") != 0:
        raise ValueError(f"搜狐K线 status={node.get('status')}（无数据或代码有误）")
    return node.get("hq") or []


def sohu_value(row, field):
    """从搜狐K线行取字段值并归一化：剥 % 后缀；成交金额 万→元（与东财/腾讯口径对齐）"""
    idx = SOHU_KLINE_MAP.get(field)
    if idx is None or idx >= len(row):
        return None
    v = row[idx]
    if isinstance(v, str):
        v = v.rstrip("%")
    if field == "amount":
        try:
            return float(v) * 10000  # 万元 → 元
        except (ValueError, TypeError):
            return None
    return v


# ==================== 交叉验证 ====================
def cross_validate(code, field, date=None):
    """多源交叉验证指定字段。返回 True=一致，False=不一致或证据不足"""
    print(f"交叉验证: {code} 字段={field}" + (f" 日期={date}（历史模式）" if date else "（实时模式）"))
    print("=" * 60)

    values = []  # (源名, 原始值)

    if date:
        # 历史模式：所有源都取该日历史值，取不到该日数据的源跳过并明示
        skipped = [f"{s}（实时源，指定日期时不参与）"
                   for s in ("腾讯K线内嵌qt", "腾讯实时", "新浪", "东财实时")]

        # 源1: 腾讯K线历史
        try:
            kline, _ = fetch_tencent_kline(code)
            row = next((r for r in kline if r[0] == date), None) if kline else None
            idx = KLINE_MAP.get(field)
            if row is None:
                skipped.insert(0, f"腾讯K线历史（无 {date} 数据）")
            elif idx is None or idx >= len(row):
                skipped.insert(0, f"腾讯K线历史（无 {field} 字段，可用: {', '.join(KLINE_MAP)}）")
            else:
                values.append(("腾讯K线历史", row[idx]))
                print(f"  腾讯K线历史 row[{idx}]: {row[idx]}")
        except Exception as e:
            skipped.insert(0, f"腾讯K线历史（{e}）")

        # 源2: 东财K线历史
        try:
            rows = fetch_eastmoney_kline(code, beg=date, end=date)
            idx = EM_KLINE_MAP.get(field)
            if not rows:
                skipped.insert(0, f"东财K线历史（无 {date} 数据）")
            elif idx is None or idx >= len(rows[0]):
                skipped.insert(0, f"东财K线历史（无 {field} 字段）")
            else:
                values.append(("东财K线历史", rows[0][idx]))
                print(f"  东财K线历史 row[{idx}]: {rows[0][idx]}")
        except Exception as e:
            skipped.insert(0, f"东财K线历史（{e}）")

        # 源3: 搜狐K线历史
        try:
            rows = fetch_sohu_kline(code, beg=date, end=date)
            row = next((r for r in rows if r[0] == date), None)
            idx = SOHU_KLINE_MAP.get(field)
            if row is None:
                skipped.insert(0, f"搜狐K线历史（无 {date} 数据）")
            elif idx is None:
                skipped.insert(0, f"搜狐K线历史（无 {field} 字段，可用: {', '.join(SOHU_KLINE_MAP)}）")
            else:
                v = sohu_value(row, field)
                values.append(("搜狐K线历史", v))
                print(f"  搜狐K线历史 row[{idx}]: {row[idx]}（归一化后: {v}）")
        except Exception as e:
            skipped.insert(0, f"搜狐K线历史（{e}）")

        for s in skipped:
            print(f"  跳过: {s}")
    else:
        # 实时模式：各源取最新值
        # 源1: 腾讯K线内嵌qt
        try:
            _, qt_arr = fetch_tencent_kline(code)
            v = qt_value(qt_arr, field)
            if v is not None:
                values.append(("腾讯K线内嵌qt", v))
                print(f"  腾讯K线内嵌qt[{QT_MAP[field]}]: {v}")
            else:
                print(f"  腾讯K线内嵌qt: 无 {field} 字段")
        except Exception as e:
            print(f"  腾讯K线内嵌qt失败: {e}")

        # 源2: 腾讯实时
        try:
            rt = fetch_tencent_qt(code)
            v = qt_value(rt, field)
            if v is not None:
                values.append(("腾讯实时", v))
                print(f"  腾讯实时     qt[{QT_MAP[field]}]: {v}")
            else:
                print(f"  腾讯实时: 无 {field} 字段")
        except Exception as e:
            print(f"  腾讯实时失败: {e}")

        # 源3: 新浪
        try:
            sn = fetch_sina(code)
            key = SINA_MAP.get(field)
            if key:
                values.append(("新浪", sn[key]))
                print(f"  新浪         {key}: {sn[key]}")
            else:
                print(f"  新浪: 无 {field} 字段映射（可用: {', '.join(SINA_MAP)}）")
        except Exception as e:
            print(f"  新浪失败: {e}")

        # 源4: 东财实时
        try:
            em = fetch_eastmoney_rt(code)
            if field in em and field != "name" and em[field] is not None:
                values.append(("东财实时", em[field]))
                print(f"  东财实时     {field}: {em[field]}")
            else:
                print(f"  东财实时: 无 {field} 字段")
        except Exception as e:
            print(f"  东财实时失败: {e}")

    print("-" * 60)
    if len(values) < 2:
        print(f"⚠️ 仅获取到 {len(values)} 个源的值，不满足交叉验证要求（需≥2源）")
        return False

    # 比对
    numeric_vals = []
    for name, v in values:
        try:
            numeric_vals.append((name, float(v)))
        except (ValueError, TypeError):
            print(f"  {name}: 值 {v!r} 非数值，不参与比对")

    if len(numeric_vals) < 2:
        print("⚠️ 数值字段不足2个，无法做数值交叉验证")
        return False

    mode, tol = FIELD_THRESHOLDS.get(field, DEFAULT_THRESHOLD)
    vals = [v for _, v in numeric_vals]
    if mode == "abs":
        diff = max(vals) - min(vals)
        diff_desc = f"{diff:.4f}"
    else:
        base = max(abs(v) for v in vals)
        diff = (max(vals) - min(vals)) / base if base else 0.0
        diff_desc = f"{diff:.6f}（相对）"
    tol_desc = f"{mode}阈值 {tol}"

    if diff < tol:
        print(f"✅ {len(numeric_vals)} 源一致，偏差 {diff_desc} < {tol_desc}")
        return True
    elif diff < tol * 10:
        print(f"⚠️ {len(numeric_vals)} 源基本一致，偏差 = {diff_desc}（{tol_desc}），需人工确认")
        return True
    else:
        print(f"❌ {len(numeric_vals)} 源不一致，最大偏差 = {diff_desc}（{tol_desc}）")
        for name, v in numeric_vals:
            print(f"   {name}: {v}")
        return False


# ==================== 取样本值 ====================
def get_sample_values(code, field, count=5):
    """从腾讯K线取最近 count 天的样本值"""
    kline, _ = fetch_tencent_kline(code, count=200)
    if not kline:
        return []

    idx = KLINE_MAP.get(field)
    if field == "amount":
        # 部分市场K线行第10列（索引9）含成交额，A股通常没有
        idx = 9 if any(len(r) > 9 for r in kline[-count:]) else None
    if idx is None:
        print(f"K线中无 {field} 字段映射（可用: {', '.join(KLINE_MAP)}，amount 视接口返回而定）")
        return []

    samples = []
    for row in kline[-count:]:
        if idx < len(row):
            samples.append({"date": row[0], "value": row[idx]})
    return samples


# ==================== 单源验证 ====================
def probe_single_source(source, code, field=None):
    """验证单个源连通性；指定 field 时显示该字段在该源的取值"""
    src = SOURCES[source]
    print(f"验证源: {src['name']} ({source})")
    print(f"代码: {code}" + (f" 字段: {field}" if field else ""))
    try:
        if source == "tencent_kline":
            kline, qt = fetch_tencent_kline(code)
            if not kline:
                print("❌ K线为空")
                return False
            print(f"✅ K线: {len(kline)} 条, qt={'有' if qt else '无'}")
            print(f"   最新: {kline[-1][0]} 开={kline[-1][1]} 收={kline[-1][2]} "
                  f"高={kline[-1][3]} 低={kline[-1][4]} 量={kline[-1][5]}")
            if field:
                if field in KLINE_MAP:
                    print(f"   K线行 {field} = {kline[-1][KLINE_MAP[field]]}")
                elif qt:
                    v = qt_value(qt, field)
                    print(f"   qt[{QT_MAP.get(field)}] {field} = {v}" if v is not None
                          else f"   无 {field} 字段映射（可用: {', '.join(QT_MAP)}）")
        elif source == "tencent_qt":
            rt = fetch_tencent_qt(code)
            print(f"✅ 实时: {len(rt)} 个字段, 名称={rt[1]}")
            if field:
                v = qt_value(rt, field)
                print(f"   qt[{QT_MAP.get(field)}] {field} = {v}" if v is not None
                      else f"   无 {field} 字段映射（可用: {', '.join(QT_MAP)}）")
        elif source == "sina":
            sn = fetch_sina(code)
            print(f"✅ 新浪: {sn['name']} 收盘={sn['close']}")
            if field:
                key = SINA_MAP.get(field)
                print(f"   {field} = {sn[key]}" if key
                      else f"   新浪无 {field} 字段映射（可用: {', '.join(SINA_MAP)}）")
        elif source == "eastmoney":
            rt = fetch_eastmoney_rt(code)
            print(f"✅ 东财实时: {rt['name']} 收盘={rt['close']} "
                  f"涨跌幅={rt['change_pct']}% 总市值={rt['market_cap']:.2f}亿")
            rows = None
            try:
                rows = fetch_eastmoney_kline(code, count=3)
                if rows:
                    print(f"✅ 东财K线: 最近{len(rows)}条, 最新: {rows[-1][0]} 收={rows[-1][2]} "
                          f"涨跌幅={rows[-1][8]}% 换手率={rows[-1][10]}%")
                else:
                    print("⚠️ 东财K线: 返回为空")
            except Exception as e:
                print(f"⚠️ 东财K线失败（实时已通）: {e}")
            if field:
                if field in rt and field != "name":
                    print(f"   实时 {field} = {rt[field]}")
                elif field in EM_KLINE_MAP and rows:
                    print(f"   K线行 {field} = {rows[-1][EM_KLINE_MAP[field]]}")
                else:
                    print(f"   东财无 {field} 字段映射")
        elif source == "sohu":
            # 取最近约 30 天的日K（接口按 start/end 闭区间返回，倒序）
            end = datetime.now().date()
            rows = fetch_sohu_kline(code, beg=str(end - timedelta(days=30)), end=str(end))
            if not rows:
                print("❌ 搜狐K线为空")
                return False
            print(f"✅ 搜狐K线: {len(rows)} 条（日期倒序，最新在前）")
            latest = rows[0]
            print(f"   最新: {latest[0]} 开={latest[1]} 收={latest[2]} "
                  f"涨跌幅={latest[4]} 量={latest[7]}手 额={latest[8]}万 换手={latest[9]}")
            if field:
                idx = SOHU_KLINE_MAP.get(field)
                if idx is None:
                    print(f"   搜狐无 {field} 字段映射（可用: {', '.join(SOHU_KLINE_MAP)}）")
                else:
                    print(f"   K线行 {field} = {latest[idx]}"
                          f"（归一化后: {sohu_value(latest, field)}）")
        return True
    except Exception as e:
        print(f"❌ 失败: {e}")
        return False


# ==================== 主流程 ====================
def main():
    parser = argparse.ArgumentParser(
        description="探索性信源验证工具 — 取样本值、多源交叉验证（只读不写，不触碰生产存储）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  %(prog)s --source tencent_qt --code sh600900 --field close
  %(prog)s --cross --code sh600900 --field close
  %(prog)s --cross --code sh600900 --field close --date 2026-08-14
  %(prog)s --sample --code sh600900 --field volume --count 5
  %(prog)s --dump-qt --code sh600900

退出码: 交叉验证不一致/证据不足时为 1，否则为 0。""")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", choices=list(SOURCES), help="验证单个源连通性")
    group.add_argument("--cross", action="store_true", help="多源交叉验证（配合 --field，可选 --date）")
    group.add_argument("--sample", action="store_true", help="从腾讯K线取最近N天样本值（配合 --field）")
    group.add_argument("--dump-qt", action="store_true", help="打印 qt 数组所有非空字段（探测字段索引）")
    parser.add_argument("--code", default="sh600900",
                        help="股票代码，默认 sh600900（长江电力，仅作演示示例，实际使用请显式指定）")
    parser.add_argument("--field", help="字段名，如 close/change_pct/turnover_rate/market_cap/volume 等")
    parser.add_argument("--date", help="历史日期 YYYY-MM-DD（--cross 历史模式：所有源取该日历史值）")
    parser.add_argument("--count", type=int, default=5, help="--sample 的天数（默认 5）")
    parser.add_argument("--insecure", action="store_true",
                        help="关闭 SSL 证书校验（仅个别企业网络需要，默认开启校验）")
    args = parser.parse_args()

    global _INSECURE
    _INSECURE = args.insecure

    if args.dump_qt:
        _, qt_arr = fetch_tencent_kline(args.code)
        if qt_arr:
            dump_qt_fields(qt_arr)
        else:
            print("K线响应中无 qt 节点")
        return 0

    if args.cross:
        if not args.field:
            parser.error("--cross 需要 --field")
        ok = cross_validate(args.code, args.field, args.date)
        return 0 if ok else 1

    if args.sample:
        if not args.field:
            parser.error("--sample 需要 --field")
        samples = get_sample_values(args.code, args.field, args.count)
        print(f"样本值 ({args.code} {args.field}):")
        for s in samples:
            print(f"  {s['date']}: {s['value']}")
        if samples:
            print(f"\n可写入 registry sample_values:")
            print(json.dumps(samples, indent=2, ensure_ascii=False))
        return 0 if samples else 1

    if args.source:
        ok = probe_single_source(args.source, args.code, args.field)
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
