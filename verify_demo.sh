#!/usr/bin/env bash
# verify_demo.sh — demo 验收脚本：每轮迭代的固定验收载体
#
# 模拟新用户开箱路径：只给一个股票代码 → onboard（种子回退）→ dry-run →
# 真实写入（CSV）→ 历史补录 → 完整性核查 → --fix 路径。
# 全部通过则 exit 0，任何一步失败即 exit 1。
#
# 用法:
#   ./verify_demo.sh [--code sh600900] [--insecure] [--workdir /tmp/stock-demo-verify]
#
# 注意：会真实访问行情接口；--insecure 仅用于企业代理等 SSL 拦截环境。

set -euo pipefail

CODE="sh600900"
INSECURE=""
WORKDIR="/tmp/stock-demo-verify"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --code) CODE="$2"; shift 2 ;;
    --insecure) INSECURE="--insecure"; shift ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
DC="$SKILL_DIR/stock-data-collector/scripts"
FR="$SKILL_DIR/stock-field-registry/scripts"

step() { echo; echo "==[ $1 ]=="; }

step "0. 编译检查"
find "$SKILL_DIR" -name "*.py" -print0 | xargs -0 python3 -m py_compile
echo "py_compile OK"

step "1. 环境准备（${WORKDIR}）"
rm -rf "$WORKDIR"; mkdir -p "$WORKDIR"; cd "$WORKDIR"

step "2. onboard（不带 --registry，验证种子回退）"
python3 "$DC/onboard_stock.py" --code "$CODE" $INSECURE | grep -E "内置|种子|包含字段|配置已保存"
[[ -f "${CODE}_config.json" ]] || { echo "❌ config 未生成"; exit 1; }
# 演示保持自包含：数据目录改回本地 ./data（onboard 默认 ~/stock-data， demo 不污染用户主目录）
python3 - "${CODE}" <<'EOF'
import json, sys
p = f"{sys.argv[1]}_config.json"
c = json.load(open(p))
c["storage"]["options"]["data_dir"] = f"./data/{sys.argv[1]}"
json.dump(c, open(p, "w"), ensure_ascii=False, indent=2)
EOF

step "3. dry-run（只算不写）"
python3 "$DC/update_daily.py" "${CODE}_config.json" --dry-run $INSECURE | tail -15

step "4. 真实写入（最近交易日）"
# 用 --days 1 而不是"今天"：凌晨/非交易时段运行时"今天"可能尚未开市
python3 "$DC/update_daily.py" "${CODE}_config.json" --days 1 $INSECURE | grep -E "写入 ✓|收盘:|已存在"

step "5. 历史补录（--date 前一交易日，验证取历史值）"
# 取存储中最新日期，再从K线找它的前一交易日补录（盘中价格漂移不影响历史日）
PREV=$(python3 - "$DC" "${CODE}" "$INSECURE" <<'EOF'
import csv, sys
sys.path.insert(0, sys.argv[1])
from lib import common
last = list(csv.reader(open(f'data/{sys.argv[2]}/quote.csv')))[-1][0]
kline, _ = common.fetch_kline(sys.argv[2], count=10, insecure=bool(sys.argv[3]))
dates = [r[0] for r in kline]
print(dates[dates.index(last) - 1] if last in dates else dates[-2])
EOF
)
python3 "$DC/update_daily.py" "${CODE}_config.json" --date "$PREV" $INSECURE | grep -E "写入 ✓|收盘:"
# 幂等性：同一天再跑应跳过
python3 "$DC/update_daily.py" "${CODE}_config.json" --date "$PREV" $INSECURE | grep -E "已存在"

step "6. 完整性核查"
python3 "$DC/check_integrity.py" "${CODE}_config.json" $INSECURE | tail -10

step "7. --fix 路径（篡改历史行→检出→修复→复查）"
python3 - "$CODE" "$PREV" <<'EOF'
import csv, sys
p = f"data/{sys.argv[1]}/quote.csv"
rows = list(csv.reader(open(p)))
header = rows[0]
col = header.index("涨跌额") if "涨跌额" in header else 3
for r in rows[1:]:
    if r[0] == sys.argv[2]:
        orig = r[col]; r[col] = "9.99"
        print(f"已篡改 {r[0]} 的 {header[col]}: {orig} → 9.99")
csv.writer(open(p, "w", newline="")).writerows(rows)
EOF
python3 "$DC/check_integrity.py" "${CODE}_config.json" --fix $INSECURE | tail -8
python3 "$DC/check_integrity.py" "${CODE}_config.json" $INSECURE | tail -8

step "8. 字段说明表渲染"
python3 "$FR/render_registry.py" "$SKILL_DIR/stock-field-registry/assets/field_library.json" | head -6

echo
echo "=================================================="
echo "✅ demo 验收通过（${CODE}）"
echo "=================================================="
