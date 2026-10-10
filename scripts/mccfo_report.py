#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
McCFO 财报渲染引擎  (麦麦财务官 · 渲染层)
====================================================

本脚本是 McCFO 技能的可运行源码部分：它把麦当劳 MCP 返回的
「订单 / 优惠券 / 积分」三类原始数据，按 references/finance-model.md
定义的财务口径，渲染成一份结构化的个人消费财报（Markdown 或 HTML）。

职责边界：
  · 本脚本 **不** 直接调用麦当劳 MCP。取数由 Agent 通过 MCP 完成，
    取到的 JSON 落到本地后再交给本脚本渲染。这样做的好处是：
    渲染逻辑可离线复现、可单元测试、也不需要在代码里保管 Token。
  · 本脚本不做网络请求、不读取任何凭据。

用法：
  python mccfo_report.py \
      --orders  sample_data/orders.sample.json \
      --coupons sample_data/coupons.sample.json \
      --points  sample_data/points.sample.json \
      --format  both \
      --out     ./out

输出：
  out/mccfo-report.md    结构化财报（Markdown）
  out/mccfo-report.html  同内容的可视化报表（自包含单文件，无外部依赖）

依赖：仅标准库（Python 3.8+）。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime

# --------------------------------------------------------------------------
# 常量与口径（与 references/finance-model.md 严格对应）
# --------------------------------------------------------------------------

CATEGORY_ORDER = ["汉堡", "小食", "饮料", "甜品", "套餐", "其他"]

# (名称, 起始分钟, 结束分钟)；夜宵跨天
MEAL_SLOTS = [
    ("早餐", 5 * 60, 10 * 60 + 29),
    ("午餐", 10 * 60 + 30, 13 * 60 + 59),
    ("下午茶", 14 * 60, 16 * 60 + 59),
    ("晚餐", 17 * 60, 20 * 60 + 59),
    ("夜宵", 21 * 60, 4 * 60 + 59),
]

EXPIRY_LEVELS = [
    (0, "⛔ 已过期"),
    (3, "🔴 临期"),
    (7, "🟡 关注"),
    (10 ** 9, "🟢 充裕"),
]


def slot_of(dt: datetime) -> str:
    """把时间点归入 早餐/午餐/下午茶/晚餐/夜宵。"""
    m = dt.hour * 60 + dt.minute
    for name, a, b in MEAL_SLOTS:
        if a <= b:
            if a <= m <= b:
                return name
        else:  # 跨天（夜宵）
            if m >= a or m <= b:
                return name
    return "其他"


def expiry_level(days_left: int) -> str:
    for limit, label in EXPIRY_LEVELS:
        if days_left <= limit:
            return label
    return "🟢 充裕"


def money(x: float) -> str:
    return f"¥{x:,.2f}"


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


# --------------------------------------------------------------------------
# 输入加载
# --------------------------------------------------------------------------

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 模块 B：券资产模型
# --------------------------------------------------------------------------

def analyze_coupons(data: dict, today: date) -> dict:
    rows = []
    for c in data.get("coupons", []):
        d = float(c.get("discount", 0) or 0)
        t = float(c.get("threshold", 0) or 0)
        try:
            exp = datetime.strptime(str(c.get("expireDate", ""))[:10], "%Y-%m-%d").date()
            days_left = (exp - today).days
        except Exception:
            exp, days_left = None, 999

        yield_ = 1.0 if t <= 0 else min(d / max(t, d), 1.0)
        urgency = 1.0 / max(days_left, 1) if days_left > 0 else 0.0
        fit = float(c.get("fitBoost", 1.0) or 1.0)
        store_ok = bool(c.get("storeAvailable", True))

        # 期望收益 = 面额 × 门槛效率 × 紧迫系数 × 需求匹配 × 门店可用
        score = d * yield_ * (1 + urgency) * fit * (1 if store_ok else 0)

        rows.append({
            "name": c.get("name", "(未命名券)"),
            "discount": d,
            "threshold": t,
            "expire": exp.isoformat() if exp else "—",
            "days_left": days_left,
            "level": expiry_level(days_left),
            "yield": yield_,
            "urgency": urgency,
            "fit": fit,
            "store_ok": store_ok,
            "score": score,
            "expired": days_left <= 0,
        })

    active = [r for r in rows if not r["expired"]]
    usable = [r for r in active if r["store_ok"]]
    total_d = sum(r["discount"] for r in active)
    usable_d = sum(r["discount"] for r in usable)
    weighted_yield = safe_div(sum(r["discount"] * r["yield"] for r in active), total_d)

    ranked = sorted(usable, key=lambda r: (-r["score"], -r["discount"]))
    alert = sorted([r for r in active if 0 < r["days_left"] <= 3],
                   key=lambda r: (-r["score"], r["days_left"]))

    return {
        "rows": rows,
        "ranked": ranked,
        "alert": alert,
        "count_active": len(active),
        "count_expired": len(rows) - len(active),
        "total_discount": total_d,
        "usable_discount": usable_d,
        "weighted_yield": weighted_yield,
    }


# --------------------------------------------------------------------------
# 模块 C：积分资产模型
# --------------------------------------------------------------------------

def analyze_points(data: dict) -> dict:
    p = int(data.get("available", 0) or 0)
    p_exp = int(data.get("expiring", 0) or 0)

    prods = []
    for item in data.get("products", []):
        cost = float(item.get("points", 0) or 0)
        value = float(item.get("cashPrice", 0) or 0)
        need = float(item.get("needMatch", 1.0) or 1.0)
        vpp = safe_div(value, cost)
        prods.append({
            "name": item.get("name", "(未命名)"),
            "points": int(cost),
            "value": value,
            "value_estimated": bool(item.get("valueEstimated", False)),
            "need": need,
            "vpp": vpp,
            "priority": vpp * need,
        })

    prods.sort(key=lambda x: -x["priority"])
    return {
        "available": p,
        "expiring": p_exp,
        "pressure": safe_div(p_exp, p, 0.0),
        "products": prods,
    }


# --------------------------------------------------------------------------
# 模块 A：消费分析模型
# --------------------------------------------------------------------------

def analyze_orders(data: dict) -> dict:
    orders = data.get("orders", [])
    period = data.get("period", "—")
    prev_total = data.get("prevPeriodTotal") or data.get("prevMonthTotal")

    total_list = total_discount = total_pay = 0.0
    cat_amount = defaultdict(float)
    cat_count = defaultdict(int)
    slot_amount = defaultdict(float)
    slot_count = defaultdict(int)
    chan_amount = defaultdict(float)
    chan_count = defaultdict(int)
    store_amount = defaultdict(float)
    store_count = defaultdict(int)
    item_count = defaultdict(int)
    item_spend = defaultdict(float)

    best_order = None

    for o in orders:
        lst = float(o.get("listAmount", 0) or 0)
        dis = float(o.get("discount", 0) or 0)
        pay = float(o.get("payAmount", 0) or 0)

        total_list += lst
        total_discount += dis
        total_pay += pay

        dt = None
        try:
            dt = datetime.strptime(str(o.get("date", ""))[:16], "%Y-%m-%d %H:%M")
        except Exception:
            pass

        channel = o.get("channel", "未知")
        chan_amount[channel] += pay
        chan_count[channel] += 1

        store = o.get("store", "未知门店")
        store_amount[store] += pay
        store_count[store] += 1

        if dt:
            s = slot_of(dt)
            slot_amount[s] += pay
            slot_count[s] += 1

        for it in o.get("items", []):
            cat = it.get("category", "其他")
            if cat not in CATEGORY_ORDER:
                cat = "其他"
            qty = int(it.get("qty", 1) or 1)
            price = float(it.get("price", 0) or 0) * qty
            cat_amount[cat] += price
            cat_count[cat] += qty
            item_count[it.get("name", "?")] += qty
            item_spend[it.get("name", "?")] += price

        rate = safe_div(dis, lst)
        if lst > 0 and (best_order is None or rate > best_order["rate"]):
            best_order = {"id": o.get("orderId", "—"), "date": o.get("date", "—"),
                          "list": lst, "save": dis, "pay": pay, "rate": rate}

    n = len(orders)
    cat_total = sum(cat_amount.values()) or 1.0

    top_items = sorted(item_count.items(), key=lambda kv: -kv[1])[:5]
    heavy_items = sorted(item_spend.items(), key=lambda kv: -kv[1])[:5]

    mom = None
    if prev_total:
        mom = safe_div(total_pay - float(prev_total), float(prev_total))

    return {
        "period": period,
        "order_count": n,
        "total_list": total_list,
        "total_discount": total_discount,
        "total_pay": total_pay,
        "aov": safe_div(total_pay, n),
        "discount_rate": safe_div(total_discount, total_list),
        "pay_rate": safe_div(total_pay, total_list),
        "category": [(c, cat_amount.get(c, 0.0), cat_count.get(c, 0),
                      safe_div(cat_amount.get(c, 0.0), cat_total)) for c in CATEGORY_ORDER
                     if cat_count.get(c, 0) > 0],
        "slots": sorted(((k, v, slot_count[k]) for k, v in slot_amount.items()),
                        key=lambda kv: -kv[1]),
        "channels": sorted(((k, v, chan_count[k]) for k, v in chan_amount.items()),
                           key=lambda kv: -kv[1]),
        "stores": sorted(((k, v, store_count[k]) for k, v in store_amount.items()),
                         key=lambda kv: -kv[1]),
        "top_items": top_items,
        "heavy_items": heavy_items,
        "best_order": best_order,
        "mom": mom,
    }


# --------------------------------------------------------------------------
# 结论：下一步动作
# --------------------------------------------------------------------------

def build_actions(c: dict, p: dict):
    """生成"接下来做什么"，最多 3 条。返回 [(标签, 说明), ...]。"""
    out = []
    ranked = c.get("ranked") or []
    alert = sorted(c.get("alert") or [], key=lambda r: -r["score"])

    if ranked:
        top = ranked[0]
        cond = ("无门槛，可直接省 " + money(top["discount"])) if top["threshold"] <= 0 \
            else (f"凑到 {money(top['threshold'])} 可省 {money(top['discount'])}")
        if 0 < top["days_left"] <= 3:
            out.append(("优先用掉临期高价值券",
                        f"「{top['name']}」{top['days_left']} 天后到期，收益率 "
                        f"{pct(top['yield'])}，{cond}。"))
        else:
            out.append(("首选用这张券",
                        f"「{top['name']}」，收益率 {pct(top['yield'])}，优先级最高，{cond}。"))

    rest = [r for r in alert if not ranked or r["name"] != ranked[0]["name"]]
    if rest:
        r = rest[0]
        out.append(("别漏掉这张临期券",
                    f"「{r['name']}」{r['days_left']} 天后到期，可省 {money(r['discount'])}。"))

    if p["products"]:
        prod = p["products"][0]
        if p["pressure"] >= 0.3:
            out.append(("兑换积分",
                        f"{prod['name']}，{prod['points']:,} 分兑 {money(prod['value'])}"
                        f"（{prod['vpp']:.4f} 元/分），ROI 最优。"))
        else:
            out.append(("积分不着急",
                        f"当前过期压力低，最优兑换仍是 {prod['name']}"
                        f"（{prod['vpp']:.4f} 元/分），可择机兑换。"))

    if not out:
        out.append(("保持节奏", "当前无临期券或高压力积分，按日常节奏来即可。"))
    return out[:3]


# --------------------------------------------------------------------------
# 渲染：Markdown
# --------------------------------------------------------------------------

def render_markdown(c: dict, p: dict, o: dict, today: date) -> str:
    L = []
    A = L.append

    A("# 📊 McCFO 月度消费财报")
    A("")
    A(f"> 生成时间：{today.isoformat()} ｜ 统计期间：{o['period']}")
    A("> 数据来源：麦当劳中国官方 MCP ｜ 渲染：McCFO 财报引擎")
    A("")
    A("---")
    A("")

    # 摘要
    A("## 一、本期摘要")
    A("")
    if o["mom"] is not None:
        arrow = "🔴 上涨" if o["mom"] > 0 else "🟢 下降"
        trend = f"环比上期 **{arrow} {pct(abs(o['mom']))}**"
    else:
        trend = "环比数据不足"
    A(f"本期共 **{o['order_count']}** 单，总支出 **{money(o['total_pay'])}**，"
      f"客单价 **{money(o['aov'])}**，用券省下 **{money(o['total_discount'])}**"
      f"（折扣率 **{pct(o['discount_rate'])}**），{trend}。")
    A("")
    A(f"账上另有 **{c['count_active']}** 张有效券（账面价值 {money(c['total_discount'])}，"
      f"其中 {len(c['alert'])} 张 3 天内到期）和 **{p['available']:,}** 积分"
      f"（{p['expiring']:,} 分即将过期）。")
    A("")

    # 关键指标
    A("## 二、关键指标")
    A("")
    A("| 指标 | 数值 | 口径 |")
    A("|---|---:|---|")
    A(f"| 总支出 | {money(o['total_pay'])} | 实付合计 |")
    A(f"| 原价合计 | {money(o['total_list'])} | 券前金额 |")
    A(f"| 用券/活动省下 | {money(o['total_discount'])} | 优惠合计 |")
    A(f"| 折扣率 | {pct(o['discount_rate'])} | 省下 ÷ 原价 |")
    A(f"| 实付率 | {pct(o['pay_rate'])} | 实付 ÷ 原价 |")
    A(f"| 订单数 | {o['order_count']} | — |")
    A(f"| 客单价 | {money(o['aov'])} | 总支出 ÷ 单数 |")
    A("")

    # 结构分析
    A("## 三、消费结构")
    A("")
    A("### 3.1 品类结构")
    A("")
    A("| 品类 | 金额 | 占比 | 件数 |")
    A("|---|---:|---:|---:|")
    for name, amt, cnt, share in o["category"]:
        A(f"| {name} | {money(amt)} | {pct(share)} | {cnt} |")
    A("")
    A("### 3.2 时段分布")
    A("")
    A("| 时段 | 支出 | 单数 |")
    A("|---|---:|---:|")
    for name, amt, cnt in o["slots"]:
        A(f"| {name} | {money(amt)} | {cnt} |")
    A("")
    A("### 3.3 就餐方式")
    A("")
    A("| 方式 | 支出 | 单数 |")
    A("|---|---:|---:|")
    for name, amt, cnt in o["channels"]:
        A(f"| {name} | {money(amt)} | {cnt} |")

    A("")
    A("### 3.4 常去门店")
    A("")
    A("| 门店 | 支出 | 单数 |")
    A("|---|---:|---:|")
    for name, amt, cnt in o["stores"][:5]:
        A(f"| {name} | {money(amt)} | {cnt} |")
    A("")

    # 榜单
    A("## 四、榜单")
    A("")
    if o["top_items"]:
        A("**复购榜（按出现次数）**：" + "、".join(f"{n}×{c}" for n, c in o["top_items"]))
        A("")
    if o["heavy_items"]:
        A("**重仓榜（按累计花费）**：" + "、".join(f"{n} {money(v)}" for n, v in o["heavy_items"]))
        A("")
    if o["best_order"]:
        b = o["best_order"]
        A(f"**最省一单**：{b['date']} ｜ 原价 {money(b['list'])} → 实付 {money(b['pay'])}，"
          f"省 {money(b['save'])}（{pct(b['rate'])}）")
        A("")

    # 券资产
    A("## 五、券资产配置")
    A("")
    A(f"有效券 {c['count_active']} 张 ｜ 账面价值 {money(c['total_discount'])} ｜ "
      f"本店可用 {money(c['usable_discount'])} ｜ 加权收益率 {pct(c['weighted_yield'])}")
    A("")
    if c["alert"]:
        A("> 🔴 **临期预警**：" + "；".join(
            f"「{r['name']}」{r['days_left']} 天后到期（省 {money(r['discount'])}）"
            for r in c["alert"]))
        A("")
    A("| 券名 | 面额 | 门槛 | 到期 | 剩余 | 收益率 | 优先级 | 状态 |")
    A("|---|---:|---:|---|---:|---:|---:|---|")
    for r in c["ranked"][:10]:
        A(f"| {r['name']} | {money(r['discount'])} | {money(r['threshold'])} | "
          f"{r['expire']} | {r['days_left']}天 | {pct(r['yield'])} | "
          f"{r['score']:.3f} | {r['level']} |")
    unavailable = [r for r in c["rows"] if not r["expired"] and not r["store_ok"]]
    for r in unavailable:
        A(f"| {r['name']} | {money(r['discount'])} | {money(r['threshold'])} | "
          f"{r['expire']} | {r['days_left']}天 | {pct(r['yield'])} | — | ⚪ 本店不可用 |")
    A("")

    # 积分资产
    A("## 六、积分资产 ROI")
    A("")
    A(f"可用积分 **{p['available']:,}** ｜ 即将过期 **{p['expiring']:,}** "
      f"（压力 {pct(p['pressure'])}）")
    A("")
    if p["pressure"] >= 0.3:
        A("> 🔴 **过期压力高**，建议优先兑换下列高 ROI 商品，避免积分作废。")
        A("")
    A("| 商品 | 消耗积分 | 参考价值 | 每积分价值 | 优先级 |")
    A("|---|---:|---:|---:|---:|")
    for prod in p["products"][:8]:
        est = "（估）" if prod["value_estimated"] else ""
        A(f"| {prod['name']} | {prod['points']:,} | {money(prod['value'])}{est} | "
          f"{prod['vpp']:.4f} 元/分 | {prod['priority']:.4f} |")
    A("")

    # 结论
    A("## 七、接下来做什么")
    A("")
    for i, (label, text) in enumerate(build_actions(c, p), 1):
        A(f"{i}. **{label}**：{text}")
    A("")
    A("---")
    A("")
    A("> ⚠️ 本报告由 McCFO 技能生成，为麦当劳程序员节创意开发大赛参赛作品，非麦当劳官方产品。")
    A("> 所有金额单位为「元」，积分不折算为金额；餐品与券的可用性以麦当劳官方渠道实时结果为准。")
    A("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# 渲染：HTML
# --------------------------------------------------------------------------

def _esc(x) -> str:
    return html.escape(str(x))


def render_html(c: dict, p: dict, o: dict, today: date) -> str:
    def bar(share: float) -> str:
        w = max(0.0, min(1.0, share)) * 100
        return f'<div class="bar"><span style="width:{w:.1f}%"></span></div>'

    mom_html = ""
    if o["mom"] is not None:
        cls = "up" if o["mom"] > 0 else "down"
        arrow = "▲" if o["mom"] > 0 else "▼"
        mom_html = f'<span class="{cls}">{arrow} {abs(o["mom"]) * 100:.1f}%</span> 环比上月'

    cat_rows = "".join(
        f'<tr><td>{_esc(n)}</td><td class="num">{money(a)}</td>'
        f'<td class="num">{pct(s)}</td><td>{bar(s)}</td><td class="num">{cnt}</td></tr>'
        for n, a, cnt, s in o["category"])

    slot_rows = "".join(
        f'<tr><td>{_esc(n)}</td><td class="num">{money(a)}</td><td class="num">{cnt}</td></tr>'
        for n, a, cnt in o["slots"])

    chan_rows = "".join(
        f'<tr><td>{_esc(n)}</td><td class="num">{money(a)}</td><td class="num">{cnt}</td></tr>'
        for n, a, cnt in o["channels"])

    coupon_rows = "".join(
        f'<tr class="{ "alert" if 0 < r["days_left"] <= 3 else "" }">'
        f'<td>{_esc(r["name"])}</td><td class="num">{money(r["discount"])}</td>'
        f'<td class="num">{money(r["threshold"])}</td><td>{_esc(r["expire"])}</td>'
        f'<td class="num">{r["days_left"]}天</td><td class="num">{pct(r["yield"])}</td>'
        f'<td class="num">{r["score"]:.3f}</td><td>{_esc(r["level"])}</td></tr>'
        for r in c["ranked"][:10])

    for r in [r for r in c["rows"] if not r["expired"] and not r["store_ok"]]:
        coupon_rows += (
            f'<tr class="muted"><td>{_esc(r["name"])}</td><td class="num">{money(r["discount"])}</td>'
            f'<td class="num">{money(r["threshold"])}</td><td>{_esc(r["expire"])}</td>'
            f'<td class="num">{r["days_left"]}天</td><td class="num">{pct(r["yield"])}</td>'
            f'<td class="num">—</td><td>⚪ 本店不可用</td></tr>')

    prod_rows = "".join(
        f'<tr><td>{_esc(x["name"])}</td><td class="num">{x["points"]:,}</td>'
        f'<td class="num">{money(x["value"])}{"（估）" if x["value_estimated"] else ""}</td>'
        f'<td class="num">{x["vpp"]:.4f} 元/分</td><td class="num">{x["priority"]:.4f}</td></tr>'
        for x in p["products"][:8])

    alert_box = ""
    if c["alert"]:
        items = "".join(f'<li><b>{_esc(r["name"])}</b> — {r["days_left"]} 天后到期，'
                        f'可省 {money(r["discount"])}</li>' for r in c["alert"])
        alert_box = f'<div class="alertbox"><h4>🔴 临期券预警</h4><ul>{items}</ul></div>'

    action_items = "".join(
        f"<li><b>{_esc(label)}</b>：{_esc(text)}</li>" for label, text in build_actions(c, p))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>McCFO 月度消费财报 · {_esc(o['period'])}</title>
<style>
  :root {{
    --bg:#f5f6f8; --card:#ffffff; --ink:#1b1f24; --sub:#6b7280;
    --line:#e5e7eb; --up:#d92b2b; --down:#12864a; --gold:#f2b705; --mcd:#d92b2b;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    line-height:1.6; }}
  .wrap {{ max-width:920px; margin:0 auto; padding:32px 20px 64px; }}
  header {{ text-align:center; padding:28px 24px; background:linear-gradient(135deg,#1b1f24,#2b3138);
    color:#fff; border-radius:16px; }}
  header .kicker {{ letter-spacing:.3em; font-size:12px; color:var(--gold); }}
  header h1 {{ margin:8px 0 4px; font-size:30px; }}
  header .meta {{ color:#c8ccd2; font-size:13px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:20px 0; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
  .card .k {{ font-size:12px; color:var(--sub); }}
  .card .v {{ font-size:22px; font-weight:700; margin-top:4px; }}
  section {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
    padding:20px 22px; margin:16px 0; }}
  h2 {{ font-size:18px; margin:0 0 12px; padding-left:10px; border-left:4px solid var(--mcd); }}
  h4 {{ margin:0 0 8px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th,td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; }}
  th {{ color:var(--sub); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.05em; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  tr.alert {{ background:#fff6f6; }}
  tr.muted {{ color:var(--sub); }}
  .bar {{ background:#eef0f3; border-radius:6px; height:8px; min-width:60px; }}
  .bar span {{ display:block; height:8px; border-radius:6px; background:linear-gradient(90deg,#f2b705,#d92b2b); }}
  .up {{ color:var(--up); font-weight:700; }}
  .down {{ color:var(--down); font-weight:700; }}
  .alertbox {{ background:#fff6f6; border:1px solid #f5c2c2; border-radius:10px; padding:12px 16px; margin:12px 0; }}
  .alertbox ul {{ margin:0; padding-left:20px; }}
  ol.actions li {{ margin:6px 0; }}
  footer {{ text-align:center; color:var(--sub); font-size:12px; margin-top:24px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="kicker">McCFO · 麦麦财务官</div>
    <h1>📊 月度消费财报</h1>
    <div class="meta">统计期间 {_esc(o['period'])} ｜ 生成于 {today.isoformat()} ｜ 数据来源：麦当劳中国官方 MCP</div>
  </header>

  <div class="cards">
    <div class="card"><div class="k">总支出</div><div class="v">{money(o['total_pay'])}</div><div class="k">{mom_html}</div></div>
    <div class="card"><div class="k">订单数</div><div class="v">{o['order_count']}</div><div class="k">客单价 {money(o['aov'])}</div></div>
    <div class="card"><div class="k">用券省下</div><div class="v">{money(o['total_discount'])}</div><div class="k">折扣率 {pct(o['discount_rate'])}</div></div>
    <div class="card"><div class="k">券资产</div><div class="v">{money(c['total_discount'])}</div><div class="k">{c['count_active']} 张有效</div></div>
    <div class="card"><div class="k">积分资产</div><div class="v">{p['available']:,}</div><div class="k">{p['expiring']:,} 分将过期</div></div>
  </div>

  <section>
    <h2>一、消费结构 · 品类</h2>
    <table>
      <tr><th>品类</th><th style="text-align:right">金额</th><th style="text-align:right">占比</th><th>分布</th><th style="text-align:right">件数</th></tr>
      {cat_rows}
    </table>
  </section>

  <section>
    <h2>二、时段分布</h2>
    <table><tr><th>时段</th><th style="text-align:right">支出</th><th style="text-align:right">单数</th></tr>{slot_rows}</table>
    <h4 style="margin-top:16px">就餐方式</h4>
    <table><tr><th>方式</th><th style="text-align:right">支出</th><th style="text-align:right">单数</th></tr>{chan_rows}</table>
  </section>

  <section>
    <h2>三、券资产配置</h2>
    <p class="k">有效券 {c['count_active']} 张 ｜ 账面价值 {money(c['total_discount'])} ｜
       本店可用 {money(c['usable_discount'])} ｜ 加权收益率 {pct(c['weighted_yield'])}</p>
    {alert_box}
    <table>
      <tr><th>券名</th><th style="text-align:right">面额</th><th style="text-align:right">门槛</th>
          <th>到期</th><th style="text-align:right">剩余</th><th style="text-align:right">收益率</th>
          <th style="text-align:right">优先级</th><th>状态</th></tr>
      {coupon_rows}
    </table>
  </section>

  <section>
    <h2>四、积分资产 ROI</h2>
    <p>可用 <b>{p['available']:,}</b> 分 ｜ 即将过期 <b>{p['expiring']:,}</b> 分
       （压力 {pct(p['pressure'])}）</p>
    <table>
      <tr><th>商品</th><th style="text-align:right">消耗积分</th><th style="text-align:right">参考价值</th>
          <th style="text-align:right">每积分价值</th><th style="text-align:right">优先级</th></tr>
      {prod_rows}
    </table>
  </section>

  <section>
    <h2>五、接下来做什么</h2>
    <ol class="actions">{action_items}</ol>
  </section>

  <footer>
    ⚠️ 本报告由 McCFO 技能生成，为麦当劳程序员节创意开发大赛参赛作品，非麦当劳官方产品。<br>
    所有金额单位为「元」，积分不折算为金额；餐品与券的可用性以麦当劳官方渠道实时结果为准。
  </footer>
</div>
</body>
</html>"""


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    # 让中文在 Windows 控制台也能正常输出
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="McCFO 财报渲染引擎")
    ap.add_argument("--orders", required=True, help="订单 JSON")
    ap.add_argument("--coupons", required=True, help="优惠券 JSON")
    ap.add_argument("--points", required=True, help="积分 JSON")
    ap.add_argument("--out", default="./out", help="输出目录")
    ap.add_argument("--format", default="both", choices=["md", "html", "both"])
    ap.add_argument("--as-of", default=None, help="以指定日期为准（YYYY-MM-DD），默认今天")
    args = ap.parse_args()

    today = (datetime.strptime(args.as_of, "%Y-%m-%d").date()
             if args.as_of else date.today())

    orders = load_json(args.orders)
    coupons = load_json(args.coupons)
    points = load_json(args.points)

    c = analyze_coupons(coupons, today)
    p = analyze_points(points)
    o = analyze_orders(orders)

    os.makedirs(args.out, exist_ok=True)
    written = []

    if args.format in ("md", "both"):
        path = os.path.join(args.out, "mccfo-report.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_markdown(c, p, o, today))
        written.append(path)

    if args.format in ("html", "both"):
        path = os.path.join(args.out, "mccfo-report.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_html(c, p, o, today))
        written.append(path)

    print("McCFO 财报已生成：")
    for w in written:
        print("  ·", os.path.abspath(w))
    print(f"\n摘要：{o['order_count']} 单，实付 {money(o['total_pay'])}，"
          f"省 {money(o['total_discount'])}（{pct(o['discount_rate'])}）；"
          f"券 {c['count_active']} 张（临期 {len(c['alert'])}），积分 {p['available']:,} 分。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
