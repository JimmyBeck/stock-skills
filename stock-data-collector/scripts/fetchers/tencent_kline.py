# -*- coding: utf-8 -*-
"""腾讯前复权日K fetcher（web.ifzq.gtimg.cn）。返回JSON，含日K数组与内嵌qt实时数组。
params 二选一：
  {"kline_col": int, "date": "YYYY-MM-DD"?}  取K线历史行的列（0日期/1开/2收/3高/4低/5量/9成交额）
  {"qt_index": int, "segment": int?, "scale": number?}  取内嵌qt实时数组，仅当日有效
列含义以 registry_schema.md 为准。"""

from .base import Fetcher, FetchError, apply_index_segment_scale, to_float, kline_cache


class TencentKlineFetcher(Fetcher):
    name = "tencent_kline"
    supports_history = True  # kline_col 模式支持历史；qt_index 模式见 supports_date

    def supports_date(self, params, ctx):
        if "qt_index" in params:
            return ctx.allow_realtime  # 内嵌qt是实时快照，仅当日有效
        return ctx.target in ctx.dates

    def fetch(self, code, params, config, date=None, insecure=False):
        kline, qt_arr = kline_cache(config, code, insecure=insecure)

        if "qt_index" in params:
            # 内嵌qt数组，索引语义与 tencent_qt 完全一致，仅当日有效
            if qt_arr is None:
                raise FetchError("K线响应无内嵌qt数组")
            p = dict(params)
            p["index"] = params["qt_index"]
            return apply_index_segment_scale(qt_arr, p, "腾讯K线内嵌qt")

        if "kline_col" in params:
            want = date or params.get("date") or "latest"
            row = None
            if want == "latest":
                row = kline[-1]
            else:
                for r in kline:
                    if r[0] == want:
                        row = r
                        break
            if row is None:
                raise FetchError(f"腾讯K线无 {want} 的交易行")
            col = params["kline_col"]
            if col >= len(row):
                raise FetchError(f"腾讯K线行列数不足: 需要列{col}, 实际{len(row)}列")
            val = row[col]
            if col == 0:
                return val  # 日期列返回字符串
            return to_float(val, params, "腾讯K线")

        raise FetchError("tencent_kline params 需含 kline_col 或 qt_index 之一")
