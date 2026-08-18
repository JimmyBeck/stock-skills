# -*- coding: utf-8 -*-
"""
存储适配层抽象基类。
collector 核心逻辑（fetch + 计算）只面向本接口编程，不关心底层存储。
新增存储后端：继承 StorageAdapter 实现全部抽象方法，并在 __init__.py 注册。
"""

from abc import ABC, abstractmethod


class StorageError(Exception):
    """存储读写失败。写入失败必须抛出本异常，绝不允许静默成功。"""


class StorageAdapter(ABC):
    """
    存储适配器接口。
    sheet 为逻辑表名: "quote"(行情, 16列) / "macd"(MACD日周月, 21列)。
    行列均 0-indexed；read_sheet 返回的行列表含表头行（第0行）。
    """

    @abstractmethod
    def read_sheet(self, sheet, max_col=21):
        """读取整个表，返回二维列表（含表头），已裁掉尾部全空行"""

    @abstractmethod
    def read_dates(self, sheet):
        """读取已存储的日期列表（第0列，不含表头），用于幂等性检查"""

    @abstractmethod
    def get_next_row(self, sheet):
        """获取下一个空行位置（0-indexed，含表头偏移）"""

    @abstractmethod
    def write_record(self, sheet, row):
        """追加写入一行（一个交易日的数据），失败抛 StorageError"""

    @abstractmethod
    def update_cell(self, sheet, row, col, value):
        """更新单个单元格（如 --fix 回写、补填板块参照），失败抛 StorageError"""

    # ---- 通用辅助（子类无需重写） ----
    def date_exists(self, sheet, display_date):
        """目标显示日期是否已存在于表中"""
        return display_date in self.read_dates(sheet)
