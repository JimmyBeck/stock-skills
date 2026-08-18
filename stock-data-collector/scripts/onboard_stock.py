#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票接入脚本 — Skill 2 (stock-data-collector) 专用

用途：验证股票代码 + 能力核查（对照registry）+ 生成 per-stock config 快照

使用方法:
    python3 onboard_stock.py --code sh600900 --registry <registry.json>
    python3 onboard_stock.py --code sh600900 --registry <registry.json> --fields close,change,change_pct,market_cap,turnover_rate,pe_static,macd_daily,sector_change_pct
    python3 onboard_stock.py --code sz000001 --registry <registry.json>  # 交互式选字段
    python3 onboard_stock.py --code sh600900 --registry <registry.json> --insecure  # 关闭SSL校验（不推荐）

依赖: 仅 Python 标准库
"""

import argparse
import copy
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import common
from lib import executor

# 列布局预设模板（assets/layout_preset.json），生成 config 时拷贝进 layout 段
LAYOUT_PRESET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "assets", "layout_preset.json")

# 内置演示字段集（assets/default_registry.json，由 stock-field-registry 的种子
# field_library.json 同步而来，源头以后者为准）。--registry 缺省时回退使用，
# 让用户只给一个股票代码即可试跑
DEFAULT_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "assets", "default_registry.json")


def resolve_registry_path(arg_path):
    """解析 registry 路径：显式参数 > 同安装的 field-registry 种子 > 内置演示字段集"""
    if arg_path:
        if not os.path.isfile(arg_path):
            print(f"❌ registry 文件不存在: {arg_path}")
            sys.exit(1)
        return arg_path, None
    sibling = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "..", "stock-field-registry",
                           "assets", "field_library.json")
    if os.path.isfile(sibling):
        return sibling, f"未指定 --registry，使用同安装的 field-registry 种子: {sibling}"
    if os.path.isfile(DEFAULT_REGISTRY_PATH):
        return DEFAULT_REGISTRY_PATH, "未指定 --registry，使用内置演示字段集（default_registry.json）"
    return None, None


# ==================== 股票验证 ====================
def validate_stock(code, insecure=False, force=False):
    """验证股票代码：有效性、名称、市场、K线可得性"""
    print(f"\n[1/3] 验证股票代码: {code}")
    print("-" * 50)

    try:
        kline, qt_arr = common.fetch_kline(code, count=5, insecure=insecure)
    except Exception as e:
        err = str(e)
        if not insecure and ("SSL" in err or "CERTIFICATE" in err.upper()
                             or "UNEXPECTED_EOF" in err):
            print(f"❌ 网络请求失败（SSL/TLS 错误）: {e}")
            print(f"   这通常是企业代理/防火墙拦截导致，不是股票代码的问题。")
            print(f"   请加 --insecure 重试（关闭SSL证书校验）。")
        else:
            print(f"❌ 代码无效或无数据返回: {e}")
            print(f"   请核对代码格式: 沪市=sh600900, 深市=sz000001, 北交=bj430047, 港股=hk00700, 美股=usAAPL.OQ")
        return None

    # 推断市场
    if code.startswith("sh"):
        market = "a"
        exchange = "上海证券交易所"
    elif code.startswith("sz"):
        market = "a"
        exchange = "深圳证券交易所"
    elif code.startswith("bj"):
        market = "a"
        exchange = "北京证券交易所"
    elif code.startswith("hk"):
        market = "hk"
        exchange = "香港联合交易所"
    elif code.startswith("us"):
        market = "us"
        exchange = "美国"
    else:
        market = "unknown"
        exchange = "未知"

    # 获取股票名称
    name = "未知"
    if qt_arr and len(qt_arr) > 1:
        name = qt_arr[1]

    # 上市日期（从K线最早日期推断，不精确；验证只取了少量K线）
    listing_date_approx = kline[0][0] if kline else "未知"
    last_trade_date = kline[-1][0] if kline else "未知"

    # 是否停牌（简化判断：最近交易日是否在今天附近）
    today = datetime.now().strftime("%Y-%m-%d")
    is_suspended = False
    stale_days = 0
    if last_trade_date:
        # 简单比较：如果最后交易日距今超过7天且非节假日，可能停牌
        try:
            last_dt = datetime.strptime(last_trade_date, "%Y-%m-%d")
            today_dt = datetime.strptime(today, "%Y-%m-%d")
            stale_days = (today_dt - last_dt).days
            if stale_days > 7:
                is_suspended = True
        except ValueError:
            pass

    validation = {
        "code": code,
        "name": name,
        "market": market,
        "exchange": exchange,
        "kline_available": True,
        "kline_count_checked": len(kline),
        "first_date_in_kline": listing_date_approx,
        "last_trade_date": last_trade_date,
        "is_suspended": is_suspended,
        "stale_days": stale_days,
        "validated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    print(f"  ✅ 代码有效")
    print(f"  名称: {name}")
    print(f"  市场: {market.upper()} ({exchange})")
    print(f"  K线可用: {len(kline)} 条 (验证用)")
    print(f"  K线最早日期: {listing_date_approx}")
    print(f"  最后交易日: {last_trade_date}")
    if is_suspended:
        print(f"  ⚠️ 可能停牌（最后交易日距今 {stale_days} 天）")
    if stale_days > 30 and not force:
        # 长期无交易（退市/长期停牌）：生成 config 没有任何意义，拒绝接入。
        # 确需接入历史数据分析可用 --force 强制
        print(f"  ❌ 该股票已超过30天无交易（退市或长期停牌），不建议接入")
        print(f"     如确需接入（仅历史数据分析），请加 --force 重试")
        return None

    return validation


# ==================== 能力核查 ====================
def check_capabilities(validation, registry, requested_fields=None):
    """对照 registry 检查哪些字段可用于该股票"""
    print(f"\n[2/3] 能力核查（对照 registry）")
    print("-" * 50)

    fields = registry.get("fields", [])
    market = validation["market"]

    available = []
    unavailable = []

    for f in fields:
        if f.get("status") != "production" and f.get("status") != "verified":
            continue
        if not f.get("ready_for_production", False):
            continue

        # 如果指定了字段列表，只检查这些
        if requested_fields and f["field_id"] not in requested_fields:
            continue

        # 可执行性校验（schema 2.0.0）：缺 fetcher/params 或 computed 的旧格式条目不可用
        exec_err = executor.validate_field_entry(f)
        if exec_err:
            unavailable.append({
                "field_id": f["field_id"],
                "name": f["name"],
                "reason": exec_err,
            })
            continue

        # 检查 applicability
        app = f.get("applicability", {})
        markets = app.get("market", ["a", "hk", "us"])
        if market not in markets:
            unavailable.append({
                "field_id": f["field_id"],
                "name": f["name"],
                "reason": f"市场不匹配（字段适用: {markets}, 股票市场: {market}）"
            })
            continue

        # 检查是否有 override 需要注意
        overrides = f.get("overrides", [])
        applicable_overrides = [o for o in overrides if o.get("scope") == market or o.get("scope") == validation["code"]]

        available.append({
            "field_id": f["field_id"],
            "name": f["name"],
            "type": f["type"],
            "registry_version": f.get("registry_version", "unknown"),
            "has_override": len(applicable_overrides) > 0,
            "override_notes": "; ".join(o.get("notes", "") for o in applicable_overrides) if applicable_overrides else None,
            "entry": f,  # 完整 field_entry，生成 config 快照时整条拷贝
        })

    print(f"  可用字段 ({len(available)}):")
    for f in available:
        override_tag = f" [⚠️ override: {f['override_notes']}]" if f["has_override"] else ""
        print(f"    - {f['field_id']}: {f['name']} ({f['type']}){override_tag}")

    if unavailable:
        print(f"\n  不可用字段 ({len(unavailable)}):")
        for f in unavailable:
            print(f"    - {f['field_id']}: {f['name']} — {f['reason']}")
        print(f"  💡 不可用的字段如需采集，请用 stock-field-registry skill 调研")

    return available, unavailable


# ==================== 生成 config 快照 ====================
def detect_sector(validation, insecure=False):
    """自动探测个股所属东财行业板块（f127 行业名 → 板块列表匹配 BK 代码）。
    成功返回 {"name", "secid", "col": 4}，失败返回 None（调用方提示手工配置）。"""
    code = validation["code"]
    secid = ("1." if code.startswith("sh") else "0.") + code[2:]
    for host in ("push2", "push2delay"):
        try:
            url = (f"https://{host}.eastmoney.com/api/qt/stock/get"
                   f"?secid={secid}&fields=f57,f58,f127")
            d = json.loads(common.fetch(url, insecure=insecure, timeout=15))
            industry = (d.get("data") or {}).get("f127")
            if not industry:
                return None
            break
        except Exception:
            industry = None
    if not industry:
        return None
    # 行业板块全量列表（fs=m:90+t:2），分页匹配行业名
    for pn in (1, 2, 3):
        try:
            url = (f"https://push2delay.eastmoney.com/api/qt/clist/get"
                   f"?pn={pn}&pz=100&fs=m:90+t:2&fields=f12,f14")
            d = json.loads(common.fetch(url, insecure=insecure, timeout=20))
            diff = ((d.get("data") or {}).get("diff")) or {}
            items = list(diff.values()) if isinstance(diff, dict) else diff
            if not items:
                return None
            for it in items:
                if it.get("f14") == industry:
                    sec = {"name": industry, "secid": f"90.{it['f12']}", "col": 4}
                    print(f"  板块参照: 自动配置 {industry}（90.{it['f12']}）")
                    return sec
        except Exception:
            return None
    print(f"  ⚠️ 板块参照: 行业板块列表中未找到 {industry}，请手工配置 sector 段")
    return None


def generate_config(validation, available_fields, registry, extra_params=None, insecure=False):
    """生成 per-stock config 快照（默认本地CSV存储，开箱即用）"""
    print(f"\n[3/3] 生成配置文件（快照）")
    print("-" * 50)

    config = {
        "stock": {
            "code": validation["code"],
            "name": validation["name"],
            "market": validation["market"],
        },
        "validation": validation,
        "storage": {
            "type": "csv",
            "options": {
                "data_dir": os.path.expanduser(f"~/stock-data/{validation['code']}"),
            },
        },
        # 改用腾讯文档时：storage.type 改 "tdoc"，options 填入以下三项 + tdocs_dir
        "doc": {
            "file_id": "<待创建腾讯文档后填入>",
            "sheet_quote": "<待填入>",
            "sheet_macd": "<待填入>",
        },
        "params": extra_params or {},
        "sector": detect_sector(validation, insecure=insecure) if validation.get("market") == "a" else None,
        "date_format": "iso",
        "registry_snapshot": {
            "registry_version": registry.get("registry_version", "unknown"),
            "snapshot_date": datetime.now().strftime("%Y-%m-%d"),
            "fields": [f["field_id"] for f in available_fields],
        },
        # schema 2.0.0：完整可执行快照（整条 field_entry + enabled/on_fail），
        # 采集行为由快照驱动，update_daily 不依赖 registry 原文件
        "fields": [],
        # 列布局（从预设模板 assets/layout_preset.json 拷贝）
        "layout": None,
    }

    for f in available_fields:
        entry = copy.deepcopy(f["entry"])
        entry["enabled"] = True
        entry["on_fail"] = "skip_and_alert"
        config["fields"].append(entry)

    try:
        with open(LAYOUT_PRESET_PATH, "r", encoding="utf-8") as fp:
            preset = json.load(fp)
        config["layout"] = {"quote_sheet": preset["quote_sheet"],
                            "macd_sheet": preset["macd_sheet"]}
    except (OSError, KeyError, ValueError) as e:
        print(f"  ❌ 读取布局预设模板失败({LAYOUT_PRESET_PATH}): {e}")
        sys.exit(1)

    print(f"  配置已生成（完整可执行快照 + layout）")
    print(f"  registry 版本: {config['registry_snapshot']['registry_version']}")
    print(f"  快照日期: {config['registry_snapshot']['snapshot_date']}")
    print(f"  包含字段: {len(config['fields'])} 个（整条 field_entry 快照）")
    print(f"  行情Sheet列数: {len(config['layout']['quote_sheet'])}, "
          f"MACD Sheet列数: {len(config['layout']['macd_sheet'])}")
    print(f"\n  ⚠️ 待办:")
    print(f"    - 数据存储位置: ~/stock-data/{validation['code']}（CSV，可直接试跑；改 storage.options.data_dir 可换位置）")
    print(f"    - 如需腾讯文档: storage.type 改 tdoc，options 填入 file_id/sheet_quote/sheet_macd/tdocs_dir")
    print(f"    - 填入 params（如 eps、expected_div）")
    if not config["sector"]:
        print(f"    - 板块参照未自动配置，如需请手工填入 sector 段（name/secid/col）")
    print(f"    - 确认后用 update_daily.py --dry-run 试跑")

    return config


# ==================== 主流程 ====================
def main():
    parser = argparse.ArgumentParser(
        description="股票接入：验证代码 + 能力核查 + 生成 per-stock config 快照")
    parser.add_argument("--code", required=True, help="股票代码，如 sh600900/sz000001/hk00700/usAAPL.OQ")
    parser.add_argument("--registry",
                        help="字段 registry 文件路径 (*.json)；缺省时回退到内置演示字段集")
    parser.add_argument("--fields", help="只接入指定字段（逗号分隔 field_id），默认全部可用字段")
    parser.add_argument("--insecure", action="store_true",
                        help="关闭SSL证书校验（不推荐，仅个别企业网络）")
    parser.add_argument("--force", action="store_true",
                        help="强制接入退市/长期停牌股（仅历史数据分析用）")
    args = parser.parse_args()

    requested_fields = args.fields.split(",") if args.fields else None

    registry_path, fallback_msg = resolve_registry_path(args.registry)
    if not registry_path:
        print("❌ 未指定 --registry 且找不到内置演示字段集")
        print("   请用 --registry 显式指定 registry 文件")
        sys.exit(1)
    if fallback_msg:
        print(f"ℹ️  {fallback_msg}")

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    print("=" * 60)
    print(f"股票接入: {args.code}")
    print(f"registry 版本: {registry.get('registry_version', 'unknown')}")
    print("=" * 60)

    # Step 1: 验证
    validation = validate_stock(args.code, insecure=args.insecure, force=args.force)
    if not validation:
        sys.exit(1)

    # Step 2: 能力核查
    available, unavailable = check_capabilities(validation, registry, requested_fields)

    if not available:
        print("\n❌ 没有可用字段，无法接入")
        sys.exit(1)

    # Step 3: 生成 config
    config = generate_config(validation, available, registry, insecure=args.insecure)

    # 保存 config
    output_path = f"{args.code}_config.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"\n✅ 配置已保存: {output_path}")
    print(f"   确认后用 update_daily.py --dry-run 试跑")


if __name__ == "__main__":
    main()
