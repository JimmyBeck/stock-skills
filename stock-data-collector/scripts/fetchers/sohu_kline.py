# -*- coding: utf-8 -*-
"""搜狐历史日K fetcher（q.stock.sohu.com/hisHq）。东财历史K线被 WAF 封禁时的历史备胎源。

接口: https://q.stock.sohu.com/hisHq?code=cn_{6位代码}&start={YYYYMMDD}&end={YYYYMMDD}
返回 JSON 数组（每只股票一个对象），对象内 hq 为K线行数组，**日期倒序（最新在前）**。
实测（2026-08-17, sh600900 / sz000001）确认列含义：
  0日期 1开 2收 3涨跌额 4涨跌幅%(带%后缀) 5最低 6最高 7成交量(手) 8成交金额(万) 9换手率%(带%后缀)
  索引10为 2024 年后新增列，含义未确认（形如 "552.00"），生产字段勿用。
实测无单次天数上限（一次请求取回 2003 年至今 5066 行）。

params: {"kline_col": int, "scale": number?, "date": "latest"|"YYYY-MM-DD"?}
  单位换算：成交金额原始单位为万元，scale 0.0001 → 亿元；成交量为手。
  涨跌幅/换手率原始值带 % 后缀，fetcher 取值时先剥掉再转 float。
"""

from datetime import date as _date, timedelta

from lib import common
from .base import Fetcher, FetchError, to_float, parse_json_body

_LATEST_WINDOW_DAYS = 45  # latest 模式的回溯窗口，覆盖长假


def _sohu_code(code):
    """sh600900/sz000001 → cn_600900/cn_000001（搜狐 A股统一 cn_ 前缀，不分沪深）"""
    if code.startswith(("sh", "sz")):
        return "cn_" + code[2:]
    raise FetchError(f"搜狐 hisHq 仅支持 A股 sh/sz 代码，收到: {code}")


class SohuKlineFetcher(Fetcher):
    name = "sohu_kline"
    supports_history = True

    def fetch(self, code, params, config, date=None, insecure=False):
        col = params.get("kline_col")
        if col is None:
            raise FetchError("sohu_kline params 缺少 kline_col")
        want = date or params.get("date") or "latest"
        if want == "latest":
            # 倒序返回，取足够宽的窗口后第一行即最新交易日
            end = _date.today()
            start = end - timedelta(days=_LATEST_WINDOW_DAYS)
        else:
            try:
                start = end = _date.fromisoformat(want)
            except ValueError:
                raise FetchError(f"sohu_kline date 需为 latest 或 YYYY-MM-DD，收到: {want}")
        sohu_code = _sohu_code(code)
        url = (f"https://q.stock.sohu.com/hisHq?code={sohu_code}"
               f"&start={start.strftime('%Y%m%d')}&end={end.strftime('%Y%m%d')}")
        override = params.get("url_override")
        if override:
            url = override.replace("{code}", sohu_code)
        try:
            raw = common.fetch(url, insecure=insecure, timeout=15)
        except Exception as e:
            raise FetchError(f"搜狐K线请求失败: {e}")
        body = parse_json_body(raw, "搜狐K线")
        if not isinstance(body, list) or not body:
            # 指定日期为非交易日等无数据情形，接口返回 {} 而非数组
            raise FetchError(f"搜狐K线无数据({sohu_code}, {want})，返回: {raw[:60]}")
        node = body[0]
        if node.get("status") != 0:
            raise FetchError(f"搜狐K线 status={node.get('status')}（{sohu_code} 无数据或代码有误）")
        rows = node.get("hq") or []
        if not rows:
            raise FetchError(f"搜狐K线无数据({sohu_code}, {want})")
        row = None
        if want == "latest":
            row = rows[0]  # 倒序，首行即最新
        else:
            for r in rows:
                if r[0] == want:
                    row = r
                    break
        if row is None:
            raise FetchError(f"搜狐K线无 {want} 的交易行")
        if col >= len(row):
            raise FetchError(f"搜狐K线行列数不足: 需要列{col}, 实际{len(row)}列")
        if col == 0:
            return row[0]  # 日期列返回字符串
        val = row[col]
        if isinstance(val, str):
            val = val.rstrip("%")  # 涨跌幅/换手率列带 % 后缀
        return to_float(val, params, "搜狐K线")
