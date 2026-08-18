# -*- coding: utf-8 -*-
"""
字段执行器核心（update_daily / check_integrity 共用）。
采集行为由 config["fields"] 快照（registry 完整 field_entry，schema 2.0.0）驱动：
  - 采集型字段: 按 sources 顺序调用 fetcher 插件，主源失败机械切换备源（保留 on_fail 语义）
  - 计算型字段: 调用 computed 函数注册表（受控名单，禁止 eval）
  - config["layout"] 声明各 sheet 列序，按 layout 组装写入行
拒收规则（不猜测）：
  - 采集型信源缺 fetcher/params、计算型缺 computed、function 不在名单 → 拒收报错
  - config 为旧 4 键格式（仅 enabled 开关）→ 报错提示重新 onboard
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import common
from fetchers import get_fetcher, fetcher_names, FetchError
from computed import get_function, function_outputs, function_names

# 存储逻辑表名 -> config layout 键
SHEET_LAYOUT_KEYS = {"quote": "quote_sheet", "macd": "macd_sheet"}

# 内建衍生输入（非 field_id）：目标日前一交易日收盘（K线倒数第二根），
# 除权日按 config.params.expected_div 调整（见 prev_close()）
BUILTIN_INPUTS = ("prev_close",)


class FieldReject(Exception):
    """字段定义不合法（拒收）或执行期依赖解析失败"""


# ==================== 校验（拒收规则） ====================
def validate_field_entry(entry):
    """校验单条 field_entry 是否符合 schema 2.0.0 可执行要求。返回错误信息或 None。"""
    fid = entry.get("field_id", "<未知>")
    if "sources" not in entry and "computed" not in entry:
        return (f"字段 {fid}: 旧格式（仅 enabled 开关），"
                f"请用 onboard_stock.py 重新接入生成可执行快照")
    if entry.get("type") == "计算":
        comp = entry.get("computed")
        if not comp or not comp.get("function"):
            return f"字段 {fid}: 计算型缺 computed 段，需按 schema 2.0.0 重新调研补充可执行段"
        if comp["function"] not in function_names():
            return (f"字段 {fid}: computed.function={comp['function']} 不在函数注册表名单"
                    f"（{', '.join(function_names())}），新公式需先注册函数")
    sources = entry.get("sources") or []
    for s in sources:
        if s.get("fetcher") is None and s.get("name") == "计算":
            continue  # 计算型伪源，可执行定义在 computed 段
        if not s.get("fetcher") or s.get("params") is None:
            return (f"字段 {fid}: 信源 {s.get('name', '?')} 缺 fetcher/params，"
                    f"该字段条目为旧格式，需按 schema 2.0.0 重新调研补充可执行段")
        if s["fetcher"] not in fetcher_names():
            return f"字段 {fid}: 未知 fetcher {s['fetcher']}（已注册: {', '.join(fetcher_names())}）"
    if entry.get("type") == "采集" and not [s for s in sources if s.get("fetcher")]:
        return f"字段 {fid}: 采集型但无可执行信源（sources 全为空/伪源），需重新调研"
    return None


def validate_config(cfg):
    """校验 config 的 fields 快照与 layout 段。返回错误列表（空=通过）。"""
    errors = []
    fields = cfg.get("fields")
    if not fields:
        errors.append("config 缺少 fields 段或为空：请用 onboard_stock.py 生成配置快照")
        return errors
    for entry in fields:
        err = validate_field_entry(entry)
        if err:
            errors.append(err)
    layout = cfg.get("layout")
    if not layout or "quote_sheet" not in layout:
        errors.append("config 缺少 layout 段（quote_sheet/macd_sheet 列序声明），请重新 onboard")
    return errors


def field_map(cfg):
    return {f["field_id"]: f for f in cfg.get("fields") or []}


# ==================== params 解析（config 引用写法） ====================
def resolve_params(params, cfg):
    """解析 "<名>_from_config": "<json.path>" 形式的键：运行时从 config 取值。
    路径不存在时报错并提示补配置，不猜测默认值。"""
    out = {}
    for k, v in (params or {}).items():
        if k.endswith("_from_config"):
            base_key = k[:-len("_from_config")]
            node = cfg
            try:
                for part in str(v).split("."):
                    node = node[part]
            except (KeyError, TypeError):
                raise FetchError(
                    f"params 引用 config 路径 {v} 不存在，请在 config 中补配置"
                    f"（{base_key} 为 per-stock 参数）")
            if node is None:
                raise FetchError(f"params 引用 config 路径 {v} 为空，请在 config 中补配置")
            out[base_key] = node
        else:
            out[k] = v
    return out


# ==================== 执行上下文 ====================
class ExecContext:
    """一次执行（一个目标交易日）的上下文：K线、实时数组、已算字段值、警告。"""

    def __init__(self, cfg, target, kline, qt_arr=None, insecure=False, realtime=True):
        self.cfg = cfg
        self.target = target          # 目标交易日 YYYY-MM-DD
        self.insecure = insecure
        self.kline = kline
        self.qt_arr = qt_arr
        self.dates = [r[0] for r in kline]
        if target not in self.dates:
            raise FieldReject(f"{target} 不在K线中（非交易日或超出范围），最新K线日期: {self.dates[-1]}")
        self.today_idx = self.dates.index(target)
        self.closes = [float(r[2]) for r in kline]
        self.today = datetime.now().strftime("%Y-%m-%d")
        # 实时qt总是对应"最新交易日"的快照：盘中=当日实时，收盘后/凌晨/周末=最新交易日收盘。
        # 因此条件是"目标日=最新K线日"，而非"目标日=今天"——否则凌晨/周末跑时
        # 最新交易日的实时字段（市值/量比/PE等）会被误判为历史而留空。
        # 更早的历史日期仍一律走K线推导，避免把当前实时值写进历史行。
        self.allow_realtime = (realtime and target == self.dates[-1]
                               and cfg["stock"]["market"] == "a")
        # K线共享缓存，供各 fetcher 复用（避免每个字段重复拉2000条）
        cfg["_kline_cache"] = {"code": cfg["stock"]["code"], "kline": kline, "qt": qt_arr}

        self.values = {}       # field_id -> 单值 或 多列dict 或 ""
        self.meta = {}         # field_id -> {"status": ..., "source": ...}
        self.warnings = []     # 执行期警告（主备切换、除权调整、字段失败等）
        self.collected = None  # identity 用：当前 sources 链采集到的值
        self._busy = set()     # 循环依赖检测
        self._prev_close_cache = None

    # ---- 字段解析（递归 + 记忆化） ----
    def resolve(self, field_id):
        if field_id in self.values:
            return self.values[field_id]
        if field_id in self._busy:
            raise FieldReject(f"字段依赖存在循环: {field_id}")
        entry = field_map(self.cfg).get(field_id)
        if entry is None:
            raise FieldReject(f"字段 {field_id} 未接入（不在 config fields 快照中）")
        self._busy.add(field_id)
        try:
            if not entry.get("enabled", True):
                self.meta[field_id] = {"status": "disabled", "source": ""}
                value = ""
            else:
                value = self._execute_entry(entry)
        finally:
            self._busy.discard(field_id)
        self.values[field_id] = value
        return value

    def resolve_input(self, name):
        """解析 computed params 的输入引用：内建衍生输入 或 其他 field_id"""
        if name in BUILTIN_INPUTS:
            if name == "prev_close":
                return self.prev_close()
        return self.resolve(name)

    # ---- 单字段执行 ----
    def _execute_entry(self, entry):
        fid = entry["field_id"]
        # 市场适用性：不适用的市场留空（如 change/change_pct 仅声明 A 股）
        markets = (entry.get("applicability") or {}).get("market")
        if markets and self.cfg["stock"]["market"] not in markets:
            self.meta[fid] = {"status": "skipped_market", "source": ""}
            return ""

        comp = entry.get("computed") or {}
        func_name = comp.get("function")
        if func_name and func_name != "identity":
            fn = get_function(func_name)  # 名单外函数在 validate 阶段已拒收
            try:
                value = fn(self, comp.get("params") or {})
                self.meta[fid] = {"status": "ok", "source": f"计算:{func_name}"}
                return value
            except Exception as e:
                return self._on_fail(entry, e)

        # 采集路径（含 identity 直采占位）：按 sources 顺序尝试，主源失败机械切换
        value = self._collect(entry)
        if func_name == "identity" and self.meta.get(fid, {}).get("status") == "ok":
            self.collected = value
            value = get_function("identity")(self, comp.get("params") or {})
        return value

    def _collect(self, entry):
        fid = entry["field_id"]
        sources = [s for s in entry.get("sources") or [] if s.get("fetcher")]
        if not sources:
            return self._on_fail(entry, FetchError("无可执行信源"))
        errs = []
        history_blocked = 0
        for i, s in enumerate(sources):
            sname = s.get("name") or s["fetcher"]
            try:
                params = resolve_params(s.get("params"), self.cfg)
                fetcher = get_fetcher(s["fetcher"])
                if not fetcher.supports_date(params, self):
                    history_blocked += 1
                    errs.append(f"{sname}: 该信源不支持目标日期 {self.target}（仅当日实时）")
                    continue
                value = fetcher.fetch(self.cfg["stock"]["code"], params, self.cfg,
                                      date=self.target, insecure=self.insecure)
                if i > 0:
                    self.warnings.append(
                        f"字段 {fid}: 主源失败，已切换备源 {sname}")
                self.meta[fid] = {"status": "ok", "source": sname}
                return value
            except Exception as e:
                errs.append(f"{sname}: {e}")
                if i < len(sources) - 1:
                    self.warnings.append(f"字段 {fid}: 信源 {sname} 失败({e})，尝试下一备源")
        status = "no_history" if history_blocked == len(sources) else "failed"
        self.meta[fid] = {"status": status, "source": ""}
        return self._on_fail(entry, FetchError("；".join(errs)), warn=status != "no_history")

    def _on_fail(self, entry, err, warn=True):
        """on_fail 语义：skip_and_alert = 留空并记录警告"""
        fid = entry["field_id"]
        self.meta.setdefault(fid, {"status": "failed", "source": ""})
        if entry.get("on_fail", "skip_and_alert") == "skip_and_alert" and warn:
            self.warnings.append(f"字段 {fid}({entry.get('name', '')}) 执行失败: {err}，已留空")
        return ""

    # ---- 内建衍生输入：调整后前收 ----
    def prev_close(self):
        """目标日前一交易日收盘（K线倒数第二根）。
        除权除息日检测（当日实时模式）：K线昨收 ≠ 实时接口昨收 时，
        按 config.params.expected_div 调整为 adj_prev_close = 实时昨收 - expected_div。"""
        if self._prev_close_cache is not None:
            return self._prev_close_cache
        if self.today_idx == 0:
            raise FieldReject("目标日为K线首行，无前收盘价，相关计算字段无法执行")
        kline_prev = self.closes[self.today_idx - 1]
        result = kline_prev

        api_prev = None
        if self.allow_realtime:
            qt = self.qt_arr
            if qt is None:
                try:
                    qt = common.fetch_realtime_a(self.cfg["stock"]["code"], insecure=self.insecure)
                except Exception:
                    qt = None
            if qt and len(qt) > 4 and str(qt[4]).strip():
                try:
                    api_prev = float(qt[4])
                except (ValueError, TypeError):
                    api_prev = None

        if api_prev is not None and abs(kline_prev - api_prev) > 0.01:
            self.warnings.append(
                f"检测到昨收不一致: K线={kline_prev}, API={api_prev}，可能是除权除息日")
            expected_div = self.cfg.get("params", {}).get("expected_div")
            if expected_div and abs(abs(kline_prev - api_prev) - expected_div) < 0.05:
                result = api_prev - expected_div
                self.warnings.append(
                    f"已按配置分红值 {expected_div} 自动调整: 调整后前收={round(result, 4)}")
            else:
                self.warnings.append(
                    "⚠️ 未能自动调整（配置无 expected_div 或差值不匹配），"
                    "涨跌额/涨跌%/振幅存疑，写入后需人工复核！")
                result = api_prev
        self._prev_close_cache = result
        return result


# ==================== layout 组装 ====================
def layout_elements(cfg, sheet):
    return (cfg.get("layout") or {}).get(SHEET_LAYOUT_KEYS[sheet], [])


def field_columns(elements):
    """layout 元素 -> [(列号, field_id, output)]，output 为 None 表示单列引用"""
    cols = []
    for i, el in enumerate(elements):
        if isinstance(el, str):
            cols.append((i, el, None))
        elif isinstance(el, dict):
            cols.append((i, el.get("field"), el.get("output")))
    return cols


def execute_layout(ctx):
    """执行两个 sheet layout 引用到的全部字段（未接入的字段列留空）。"""
    not_onboarded = []
    for sheet in ("quote", "macd"):
        for _, fid, _ in field_columns(layout_elements(ctx.cfg, sheet)):
            if not fid or fid in ctx.values:
                continue
            try:
                ctx.resolve(fid)
            except FieldReject:
                not_onboarded.append(fid)
                ctx.values[fid] = ""
                ctx.meta[fid] = {"status": "not_onboarded", "source": ""}
    return sorted(set(not_onboarded))


def assemble_row(ctx, sheet):
    """按 layout 列序组装一行。date 字段值转显示格式；未接入/失败/禁用字段留空。
    数值按字段定义的 precision 固定位数格式化（如 3.5 → "3.50"），无 precision 定义原样。"""
    fmap = field_map(ctx.cfg)
    row = []
    for _, fid, output in field_columns(layout_elements(ctx.cfg, sheet)):
        value = ctx.values.get(fid, "")
        if isinstance(value, dict):
            if output is None:
                ctx.warnings.append(
                    f"layout 列 {fid}: 多列输出字段需用 {{\"field\",\"output\"}} 形式引用，本列留空")
                value = ""
            else:
                value = value.get(output, "")
        elif output is not None:
            ctx.warnings.append(
                f"layout 列 {fid}: 单列字段不支持 output={output} 引用，本列留空")
            value = ""
        if fid == "date" and value:
            value = common.to_display(ctx.cfg, str(value))
        else:
            precision = (fmap.get(fid) or {}).get("precision")
            if precision is not None and isinstance(value, (int, float)):
                value = f"{value:.{int(precision)}f}"
        row.append(value)
    return row


def recompute_field(cfg, field_id, date, kline, insecure=False):
    """check_integrity 用：以K线为原始数据重算一个字段（不用实时信源）。
    返回 (value, ctx)。"""
    ctx = ExecContext(cfg, date, kline, qt_arr=None, insecure=insecure, realtime=False)
    value = ctx.resolve(field_id)
    return value, ctx
