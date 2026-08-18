# -*- coding: utf-8 -*-
"""新浪实时行情 fetcher（hq.sinajs.cn，GBK，逗号分隔数组）。仅当日实时。
必须带 Referer: https://finance.sina.com.cn 请求头，否则403（fetcher内置，params不用管）。
params: {"index": int}"""

from lib import common
from .base import Fetcher, FetchError, apply_index_segment_scale

REFERER = "https://finance.sina.com.cn"


class SinaRtFetcher(Fetcher):
    name = "sina_rt"
    supports_history = False  # 仅当日实时快照

    def fetch(self, code, params, config, date=None, insecure=False):
        url = params.get("url_override") or f"http://hq.sinajs.cn/list={code}"
        url = url.replace("{code}", code)
        try:
            raw = common.fetch(url, insecure=insecure, timeout=10, encoding="gbk",
                               headers={"Referer": REFERER})
        except Exception as e:
            raise FetchError(f"新浪实时请求失败: {e}")
        try:
            data_str = raw.split('"')[1]
            arr = data_str.split(",")
        except IndexError:
            raise FetchError("新浪实时返回格式异常（无引号数据段）")
        if len(arr) < 2 or not arr[0].strip():
            raise FetchError("新浪实时返回空数据（代码无效或被限流）")
        return apply_index_segment_scale(arr, params, "新浪实时")
