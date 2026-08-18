# -*- coding: utf-8 -*-
"""公式函数注册表入口。function 必须是受控名单中的名字，原样使用。"""

from .functions import FUNCTIONS


def get_function(name):
    item = FUNCTIONS.get(name)
    if item is None:
        raise ValueError(
            f"computed.function={name} 不在函数注册表名单内"
            f"（已注册: {', '.join(sorted(FUNCTIONS))}），新公式需先注册函数")
    return item[0]


def function_outputs(name):
    """该函数的输出列声明；None = 单值字段"""
    item = FUNCTIONS.get(name)
    return item[1] if item else None


def function_names():
    return sorted(FUNCTIONS)
