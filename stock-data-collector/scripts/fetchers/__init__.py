# -*- coding: utf-8 -*-
"""Fetcher 插件注册表。执行器按 source_entry.fetcher 名字取插件实例。
新增信源族 = 加一个插件文件并在此注册（显式代码动作，符合边界契约）。"""

from .base import Fetcher, FetchError
from .tencent_qt import TencentQtFetcher
from .tencent_kline import TencentKlineFetcher
from .sina_rt import SinaRtFetcher
from .eastmoney import EastmoneyRtFetcher, EastmoneyKlineFetcher
from .sohu_kline import SohuKlineFetcher

_FETCHERS = {
    f.name: f for f in (
        TencentQtFetcher(),
        TencentKlineFetcher(),
        SinaRtFetcher(),
        EastmoneyRtFetcher(),
        EastmoneyKlineFetcher(),
        SohuKlineFetcher(),
    )
}


def get_fetcher(name):
    f = _FETCHERS.get(name)
    if f is None:
        raise FetchError(f"未知 fetcher: {name}（已注册: {', '.join(sorted(_FETCHERS))}）")
    return f


def fetcher_names():
    return sorted(_FETCHERS)
