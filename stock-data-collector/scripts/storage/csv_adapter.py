# -*- coding: utf-8 -*-
"""
本地CSV adapter（默认 adapter）：每股一个目录，quote.csv + macd.csv。
纯标准库，零外部依赖，全流程（含 dry-run）不需要网络外的任何环境。
表头：config 含 layout 段（schema 2.0.0）时按 layout 列序 + 字段中文名动态生成；
无 layout 的旧配置回退到行情16列 / MACD21列固定表头。
"""

import csv
import os

from .base import StorageAdapter, StorageError

QUOTE_HEADER = ["日期", "收盘价", "涨跌额", "涨跌%", "板块涨跌%", "总市值亿", "流通市值亿",
                "交易额亿", "换手率%", "量比", "振幅%", "市盈率",
                "交易记录1", "交易记录2", "交易记录3", "交易记录4"]
MACD_HEADER = ["日期", "收盘价", "交易额亿",
               "日DIFF", "日DEA", "日MACD", "日零轴", "日金叉死叉", "日动能",
               "周DIFF", "周DEA", "周MACD", "周零轴", "周金叉死叉", "周动能",
               "月DIFF", "月DEA", "月MACD", "月零轴", "月金叉死叉", "月动能"]

_SHEET_LAYOUT_KEYS = {"quote": "quote_sheet", "macd": "macd_sheet"}


def _header_from_layout(cfg, sheet):
    """按 config.layout 生成表头：单列字段用字段中文名（未接入字段用 field_id 原样），
    多列输出字段用 '字段名_输出列名'（如 日线MACD_DIFF）。"""
    elements = (cfg.get("layout") or {}).get(_SHEET_LAYOUT_KEYS[sheet])
    if not elements:
        return None
    names = {f.get("field_id"): f.get("name") for f in cfg.get("fields") or []}
    labels = []
    for el in elements:
        if isinstance(el, str):
            labels.append(names.get(el) or el)
        elif isinstance(el, dict):
            fid, output = el.get("field"), el.get("output")
            labels.append(f"{names.get(fid) or fid}_{output}")
        else:
            labels.append(str(el))
    return labels


class CsvAdapter(StorageAdapter):
    def __init__(self, cfg):
        opts = cfg["storage"].get("options", {})
        data_dir = opts.get("data_dir") or os.path.join("data", cfg["stock"]["code"])
        if not os.path.isabs(data_dir):
            # 相对路径以配置文件所在目录为基准
            data_dir = os.path.join(cfg.get("_config_dir", "."), data_dir)
        self.data_dir = data_dir
        try:
            os.makedirs(self.data_dir, exist_ok=True)
        except OSError as e:
            raise StorageError(f"CSV数据目录创建失败: {self.data_dir}: {e}")
        self.paths = {"quote": os.path.join(self.data_dir, "quote.csv"),
                      "macd": os.path.join(self.data_dir, "macd.csv")}
        self.headers = {"quote": _header_from_layout(cfg, "quote") or QUOTE_HEADER,
                        "macd": _header_from_layout(cfg, "macd") or MACD_HEADER}

    def _ensure_file(self, sheet):
        """文件不存在时创建并写入表头"""
        path = self.paths[sheet]
        if not os.path.isfile(path):
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(self.headers[sheet])

    def read_sheet(self, sheet, max_col=64):
        self._ensure_file(sheet)
        with open(self.paths[sheet], "r", newline="", encoding="utf-8-sig") as f:
            rows = [row[:max_col] for row in csv.reader(f)]
        while rows and all(c == "" for c in rows[-1]):
            rows.pop()
        return rows

    def read_dates(self, sheet):
        rows = self.read_sheet(sheet)
        return [r[0] for r in rows[1:] if r and r[0]]

    def get_next_row(self, sheet):
        return len(self.read_sheet(sheet))

    def write_record(self, sheet, row):
        self._ensure_file(sheet)
        try:
            with open(self.paths[sheet], "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(["" if v is None else v for v in row])
        except OSError as e:
            raise StorageError(f"CSV写入失败: {self.paths[sheet]}: {e}")

    def update_cell(self, sheet, row, col, value):
        rows = self.read_sheet(sheet)
        if row >= len(rows):
            raise StorageError(f"行{row}超出范围(共{len(rows)}行): {self.paths[sheet]}")
        while len(rows[row]) <= col:
            rows[row].append("")
        rows[row][col] = str(value)
        try:
            with open(self.paths[sheet], "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerows(rows)
        except OSError as e:
            raise StorageError(f"CSV写入失败: {self.paths[sheet]}: {e}")
