# -*- coding: utf-8 -*-
"""
存储适配层工厂：按 config["storage"]["type"] 分发 adapter。
新增 adapter：实现 StorageAdapter 后在 _ADAPTERS 注册即可。
"""

from .base import StorageAdapter, StorageError
from .csv_adapter import CsvAdapter
from .tdoc_adapter import TdocAdapter

_ADAPTERS = {
    "csv": CsvAdapter,
    "tdoc": TdocAdapter,
}


def get_adapter(cfg):
    """按 config["storage"]["type"] 创建 adapter；配置缺失/不可用抛 StorageError"""
    stype = cfg.get("storage", {}).get("type")
    cls = _ADAPTERS.get(stype)
    if cls is None:
        raise StorageError(f"未知存储类型: {stype} (支持: {', '.join(_ADAPTERS)})")
    return cls(cfg)
