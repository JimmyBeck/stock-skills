# -*- coding: utf-8 -*-
"""
腾讯文档 adapter：通过腾讯文档 sheet-mcp CLI 读写在线表格。
CLI 目录由 config storage.options.tdocs_dir 配置，不再绑定特定 agent 环境。
"""

import json
import os
import subprocess

from .base import StorageAdapter, StorageError

PAGE_SIZE = 500  # get_cell_data 单次读取行数上限，超出需分页循环


class TdocAdapter(StorageAdapter):
    def __init__(self, cfg):
        opts = cfg["storage"].get("options", {})
        missing = [k for k in ("file_id", "sheet_quote", "sheet_macd") if not opts.get(k)]
        if missing:
            raise StorageError(
                f"腾讯文档存储缺少配置项: {', '.join(missing)}"
                "（在 storage.options 或旧版 doc 段中填入）")
        self.file_id = opts["file_id"]
        self.sheets = {"quote": opts["sheet_quote"], "macd": opts["sheet_macd"]}
        self.tdocs_dir = os.path.expanduser(opts.get("tdocs_dir", ""))
        if not self.tdocs_dir or not os.path.isdir(self.tdocs_dir):
            raise StorageError(
                f"腾讯文档CLI目录不存在: {self.tdocs_dir or '(未配置)'}，"
                "请在 storage.options.tdocs_dir 中配置")

    def _call(self, method, args_dict):
        """调用腾讯文档sheet-mcp CLI；检查返回码与输出，失败抛 StorageError"""
        args_json = json.dumps(args_dict, ensure_ascii=False)
        try:
            result = subprocess.run(
                ["python3", "tencentdocs.py", "tdoc_call", "sheet-mcp", method, args_json],
                cwd=self.tdocs_dir,
                capture_output=True,
                text=True,
                timeout=30
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise StorageError(f"腾讯文档CLI调用失败({method}): {e}")
        if result.returncode != 0:
            raise StorageError(
                f"腾讯文档CLI返回非零({method}, code={result.returncode}): "
                f"{result.stderr.strip()[:200]}")
        if not result.stdout.strip():
            raise StorageError(f"腾讯文档CLI无输出({method})")
        return result.stdout

    @staticmethod
    def _parse_response(output):
        """解析腾讯文档CLI的响应,提取structuredContent"""
        try:
            data = json.loads(output)
            sc = data.get("result", {}).get("structuredContent", {})
            if not sc:
                content = data.get("result", {}).get("content", [])
                if content:
                    return json.loads(content[0]["text"])
            return sc
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"解析腾讯文档响应失败: {e}")

    def _get_csv(self, sheet_id, start_row, end_row, end_col):
        sc = self._parse_response(self._call("get_cell_data", {
            "file_id": self.file_id,
            "sheet_id": sheet_id,
            "start_row": start_row,
            "start_col": 0,
            "end_row": end_row,
            "end_col": end_col,
            "return_csv": True
        }))
        return sc.get("csv_data", "")

    def _read_rows(self, sheet, end_col):
        """分页读取：每页 PAGE_SIZE 行，start_row 循环递进，直到一页全空"""
        sheet_id = self.sheets[sheet]
        rows = []
        start = 0
        while True:
            csv_data = self._get_csv(sheet_id, start, start + PAGE_SIZE, end_col)
            page = []
            if csv_data:
                for line in csv_data.strip().split("\n"):
                    # 简单CSV解析（腾讯文档返回的CSV通常不含嵌入逗号）
                    page.append([c.strip().strip('"') for c in line.split(",")])
            if not page or all(all(c == "" for c in r) for r in page):
                break
            rows.extend(page)
            start += PAGE_SIZE
        # 裁掉尾部全空行
        while rows and all(c == "" for c in rows[-1]):
            rows.pop()
        return rows

    def read_sheet(self, sheet, max_col=21):
        return self._read_rows(sheet, max_col)

    def read_dates(self, sheet):
        rows = self._read_rows(sheet, 0)
        # 第0行为表头，剔除
        return [r[0] for r in rows[1:] if r and r[0]]

    def get_next_row(self, sheet):
        rows = self._read_rows(sheet, 0)
        last_non_empty = 0
        for i, r in enumerate(rows):
            if r and r[0]:
                last_non_empty = i
        return last_non_empty + 1

    def write_record(self, sheet, row):
        next_row = self.get_next_row(sheet)
        csv_data = ",".join("" if v is None else str(v) for v in row) + "\n"
        self._call("set_range_value_by_csv", {
            "file_id": self.file_id,
            "sheet_id": self.sheets[sheet],
            "start_row": next_row,
            "start_col": 0,
            "csv_data": csv_data
        })

    def update_cell(self, sheet, row, col, value):
        self._call("set_cell_value", {
            "file_id": self.file_id,
            "sheet_id": self.sheets[sheet],
            "row": row,
            "col": col,
            "value": str(value)
        })
