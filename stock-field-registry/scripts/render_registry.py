#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_registry.py — 把 registry.json 渲染成人读的《字段说明表.md》

registry.json 是字段定义的唯一事实源（机器契约），本脚本产出的人读表格只是视图。
**永不手工维护输出的 md**——改了 registry 重新渲染即可。

用法:
    python3 render_registry.py <registry.json>
    python3 render_registry.py <registry.json> -o 字段说明表.md
"""
import argparse
import json
import sys
from datetime import date

STATUS_LABEL = {
    "production": "✅ production",
    "verified": "✅ verified",
    "draft": "🔶 draft",
    "deprecated": "⛔ deprecated",
}


def fmt_sources(field):
    """信源列：主备链简写（名称+fetcher），计算型字段标 计算:function"""
    computed = field.get("computed")
    if computed:
        return f"计算:{computed.get('function', '?')}"
    parts = []
    for s in field.get("sources", []):
        fetcher = s.get("fetcher")
        name = s.get("name", "?")
        parts.append(f"{name}({fetcher})" if fetcher else name)
    return " → ".join(parts) if parts else "-"


def fmt_evidence(field):
    n = len(field.get("sample_values") or [])
    cross = "是" if field.get("cross_validated") else "否"
    return f"样本{n}天/交叉:{cross}"


def render(registry):
    fields = registry.get("fields", [])
    lines = []
    lines.append("# 字段说明表")
    lines.append("")
    lines.append(f"> 由 render_registry.py 从 registry 渲染（schema {registry.get('schema_version', '?')}，"
                 f"registry_version {registry.get('registry_version', '?')}），"
                 f"渲染日期 {date.today().isoformat()}。请勿手工编辑，改 registry 后重新渲染。")
    lines.append("")
    ready = sum(1 for f in fields if f.get("ready_for_production"))
    lines.append(f"共 {len(fields)} 个字段，其中 ready_for_production: {ready} 个。")
    lines.append("")
    lines.append("| 字段 | 名称 | 口径/定义 | 单位 | 类型 | 信源（主→备） | 更新节奏 | 可得时间 | 历史回填 | 状态 | ready | 证据 |")
    lines.append("|------|------|-----------|------|------|---------------|----------|----------|----------|------|-------|------|")
    for f in fields:
        backfill = {True: "可", False: "否"}.get(f.get("backfill_available"), "-")
        rfp = "✅" if f.get("ready_for_production") else "❌"
        status = STATUS_LABEL.get(f.get("status", ""), f.get("status", "-"))
        row = [
            f.get("field_id", "?"),
            f.get("name", "?"),
            (f.get("definition") or "-").replace("|", "\\|"),
            f.get("unit") or "-",
            f.get("type", "?"),
            fmt_sources(f),
            f.get("update_frequency") or "-",
            f.get("available_after") or "-",
            backfill,
            status,
            rfp,
            fmt_evidence(f),
        ]
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    # 非 ready 字段的缺口说明
    gaps = [(f.get("field_id"), (f.get("notes") or "").strip())
            for f in fields if not f.get("ready_for_production")]
    if gaps:
        lines.append("")
        lines.append("## 未 ready 字段缺口")
        lines.append("")
        for fid, note in gaps:
            lines.append(f"- **{fid}**：{note or '（无说明）'}")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="把 registry.json 渲染成人读的字段说明表（markdown）")
    parser.add_argument("registry", help="registry 文件路径 (*.json)")
    parser.add_argument("-o", "--output", help="输出 md 路径，缺省打印到 stdout")
    args = parser.parse_args()

    with open(args.registry, "r", encoding="utf-8") as fp:
        registry = json.load(fp)
    md = render(registry)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fp:
            fp.write(md)
        print(f"✅ 字段说明表已生成: {args.output}（{len(registry.get('fields', []))} 个字段）")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
