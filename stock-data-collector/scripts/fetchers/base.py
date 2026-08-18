# -*- coding: utf-8 -*-
"""
Fetcher 插件基类与公共工具。
每个信源族一个插件（tencent_qt / tencent_kline / sina_rt / eastmoney_rt / eastmoney_kline），
取数逻辑从原 update_daily.py / lib/common.py 平移，由字段执行器按 config 快照中的
source_entry（fetcher + params）调用。

插件接口契约（registry_schema.md 2.0.0）：
  fetch(code, params, config, date=None, insecure=False) -> 值（float 或 str）
  supports_history: 类属性，声明该信源族是否支持历史回填
params 中可选 "url_override"：测试/应急用途，覆盖插件内置 URL（{code}/{secid} 占位会被替换）。
"""

import json


class FetchError(Exception):
    """取数失败。执行器捕获后按 sources 顺序切换下一个备源。"""


class Fetcher:
    name = ""
    supports_history = False

    def fetch(self, code, params, config, date=None, insecure=False):
        raise NotImplementedError

    def supports_date(self, params, ctx):
        """该插件在 params 模式下能否服务于 ctx 的目标日期。
        默认：实时信源仅在允许实时（当日+A股+非重算）时可用；
        历史信源要求目标日期在K线范围内（具体判断由插件自身取值时完成）。"""
        if self.supports_history:
            return True
        return ctx.allow_realtime


def apply_index_segment_scale(arr, params, source_desc=""):
    """tencent_qt 族通用取值链：取 index → (可选)按 "/" 分段取 segment → float → 乘 scale"""
    idx = params.get("index")
    if idx is None:
        raise FetchError(f"{source_desc} params 缺少 index")
    if not isinstance(arr, list) or idx >= len(arr):
        raise FetchError(f"{source_desc} 返回数组长度不足: 需要索引{idx}, 实际{len(arr) if isinstance(arr, list) else '非数组'}")
    val = arr[idx]
    if "segment" in params:
        parts = val.split("/")
        seg = params["segment"]
        if seg >= len(parts):
            raise FetchError(f"{source_desc} 索引{idx}分段不足: 需要段{seg}, 实际{len(parts)}段")
        val = parts[seg]
    return to_float(val, params, source_desc)


def to_float(val, params, source_desc=""):
    """转 float 并乘可选 scale；空值/非数值抛 FetchError"""
    if val is None or (isinstance(val, str) and val.strip() in ("", "-", "--")):
        raise FetchError(f"{source_desc} 返回空值")
    try:
        num = float(val)
    except (ValueError, TypeError):
        raise FetchError(f"{source_desc} 非数值: {val!r}")
    return num * params.get("scale", 1)


def derive_secid(code):
    """按市场规则从股票代码推导东财 secid：沪市 1.code / 深市、北交 0.code /
    港股 116.code / 美股 105.code（港美股 secid 规则未逐一实测，接入时需验证）"""
    num = code[2:]
    if code.startswith("sh"):
        return f"1.{num}"
    if code.startswith("hk"):
        return f"116.{num}"
    if code.startswith("us"):
        return f"105.{num.split('.')[0]}"
    return f"0.{num}"


def kline_cache(config, code, insecure=False):
    """腾讯K线共享缓存：执行器一次会话只拉一次2000条K线，各 fetcher 复用。
    缓存由执行器在 config['_kline_cache'] 预置；直接调用 fetcher 时这里现拉并缓存。"""
    cache = config.get("_kline_cache")
    if cache and cache.get("code") == code:
        return cache["kline"], cache["qt"]
    from lib import common  # 延迟导入避免循环依赖
    kline, qt_arr = common.fetch_kline(code, insecure=insecure)
    config["_kline_cache"] = {"code": code, "kline": kline, "qt": qt_arr}
    return kline, qt_arr


def parse_json_body(text, source_desc=""):
    try:
        return json.loads(text)
    except ValueError as e:
        raise FetchError(f"{source_desc} 返回非JSON: {e}")
