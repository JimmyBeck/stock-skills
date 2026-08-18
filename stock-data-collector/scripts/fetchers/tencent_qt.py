# -*- coding: utf-8 -*-
"""腾讯实时行情 fetcher（qt.gtimg.cn，GBK，~分隔数组）。仅当日实时，仅A股字段映射可靠。
params: {"index": int, "segment": int?, "scale": number?}
执行顺序：取 index → (可选)segment 分段 → float → 乘 scale。"""

from lib import common
from .base import Fetcher, FetchError, apply_index_segment_scale


class TencentQtFetcher(Fetcher):
    name = "tencent_qt"
    supports_history = False  # 仅当日实时快照

    def fetch(self, code, params, config, date=None, insecure=False):
        url = params.get("url_override") or f"http://qt.gtimg.cn/q={code}"
        url = url.replace("{code}", code)
        try:
            raw = common.fetch(url, insecure=insecure, timeout=10, encoding="gbk")
        except Exception as e:
            raise FetchError(f"腾讯实时请求失败: {e}")
        try:
            data_str = raw.split('"')[1]
            arr = data_str.split("~")
        except IndexError:
            raise FetchError("腾讯实时返回格式异常（无引号数据段）")
        return apply_index_segment_scale(arr, params, "腾讯实时")
