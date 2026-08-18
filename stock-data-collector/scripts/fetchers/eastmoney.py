# -*- coding: utf-8 -*-
"""东方财富 fetcher（实时 + 历史K线，合一个文件）。

eastmoney_rt — push2.eastmoney.com 实时快照，按 f 字段号取值，仅当日。
  WAF 封禁（data 为 null）时内置回退 push2delay（延时行情）。
  params: {"field": "f116", "scale": number?, "secid_from_config": "..."?}

eastmoney_kline — push2his.eastmoney.com 历史K线，klines 每行逗号分隔。
  params: {"kline_col": int, "klt": 101?, "fqt": 1?, "date": "..."?, "secid_from_config": "..."?}
  列含义：0日期/1开/2收/3高/4低/5量/6成交额/7振幅%/8涨跌幅%/9涨跌额/10换手率%

secid 缺省时按市场规则推导（沪市 1.code、深市 0.code），或用 secid_from_config
从 config 指定路径读取（由执行器在调用前解析为 params["secid"]）。
"""

from lib import common
from .base import Fetcher, FetchError, to_float, derive_secid, parse_json_body

_FIELDS1 = "f1,f2,f3,f4,f5,f6"
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"


def _secid(code, params):
    return params.get("secid") or derive_secid(code)


class EastmoneyRtFetcher(Fetcher):
    name = "eastmoney_rt"
    supports_history = False  # 仅当日实时快照

    def _request(self, host, secid, field, insecure):
        url = f"https://{host}/api/qt/stock/get?secid={secid}&fields=f57,f58,{field}"
        raw = common.fetch(url, insecure=insecure, timeout=10)
        return parse_json_body(raw, "东财实时")

    def fetch(self, code, params, config, date=None, insecure=False):
        field = params.get("field")
        if not field:
            raise FetchError("eastmoney_rt params 缺少 field（f字段号）")
        secid = _secid(code, params)
        override = params.get("url_override")
        if override:
            url = override.replace("{secid}", secid).replace("{code}", code)
            try:
                raw = common.fetch(url, insecure=insecure, timeout=10)
            except Exception as e:
                raise FetchError(f"东财实时请求失败: {e}")
            body = parse_json_body(raw, "东财实时")
        else:
            try:
                body = self._request("push2.eastmoney.com", secid, field, insecure)
            except Exception as e:
                raise FetchError(f"东财实时请求失败: {e}")
            if not body.get("data"):
                # WAF 封禁时 data 为 null，内置回退延时行情
                try:
                    body = self._request("push2delay.eastmoney.com", secid, field, insecure)
                except Exception as e:
                    raise FetchError(f"东财实时(push2)被封且回退push2delay也失败: {e}")
        data = body.get("data")
        if not data:
            raise FetchError("东财实时返回空数据（push2与push2delay均无data，疑似WAF封禁）")
        return to_float(data.get(field), params, f"东财实时({field})")


class EastmoneyKlineFetcher(Fetcher):
    name = "eastmoney_kline"
    supports_history = True

    def fetch(self, code, params, config, date=None, insecure=False):
        col = params.get("kline_col")
        if col is None:
            raise FetchError("eastmoney_kline params 缺少 kline_col")
        secid = _secid(code, params)
        klt = params.get("klt", 101)
        fqt = params.get("fqt", 1)
        want = date or params.get("date") or "latest"
        if want == "latest":
            beg, end = "0", "20500101"
        else:
            beg = end = want.replace("-", "")
        url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
               f"&fields1={_FIELDS1}&fields2={_FIELDS2}&klt={klt}&fqt={fqt}&beg={beg}&end={end}")
        override = params.get("url_override")
        if override:
            url = override.replace("{secid}", secid).replace("{code}", code)
        try:
            raw = common.fetch(url, insecure=insecure, timeout=15)
        except Exception as e:
            raise FetchError(f"东财K线请求失败: {e}")
        body = parse_json_body(raw, "东财K线")
        data = body.get("data")
        if not data or not data.get("klines"):
            raise FetchError(f"东财K线无数据(secid={secid}, {want})")
        rows = [line.split(",") for line in data["klines"]]
        row = None
        if want == "latest":
            row = rows[-1]
        else:
            for r in rows:
                if r[0] == want:
                    row = r
                    break
        if row is None:
            raise FetchError(f"东财K线无 {want} 的交易行")
        if col >= len(row):
            raise FetchError(f"东财K线行列数不足: 需要列{col}, 实际{len(row)}列")
        if col == 0:
            return row[0]  # 日期列返回字符串
        return to_float(row[col], params, "东财K线")
