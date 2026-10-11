#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
McCFO 财报渲染引擎  (麦麦财务官 · 渲染层)
====================================================

本脚本是 McCFO 技能的可运行源码部分：它把麦当劳 MCP 返回的
「订单 / 优惠券 / 积分」三类原始数据，按 references/finance-model.md
定义的财务口径，渲染成一份结构化的个人消费财报（Markdown 或 HTML）。

数据来源（两条路，同一套渲染逻辑）：
  · 真实数据：先跑 scripts/mccfo_fetch.py（真实调用麦当劳 MCP），
    它会产出 out/normalized/{orders,coupons,points,meta}.json，再喂给本脚本。
  · 样例数据：直接用 scripts/sample_data/*.json，用于离线复现与自检。
    --meta 传入 meta.json 时，报告头部会显示数据来源、门店校验结论与降级说明；
    不带 --meta 时自动标注为「脱敏样例数据」。

职责边界：
  · 本脚本 **不** 直接调用麦当劳 MCP、不读取任何凭据，
    渲染逻辑因此可离线复现、可单元测试。

用法：
  # 真实数据（配合 mccfo_fetch.py --render 自动调用）
  python mccfo_report.py \
      --orders  out/normalized/orders.json \
      --coupons out/normalized/coupons.json \
      --points  out/normalized/points.json \
      --meta    out/normalized/meta.json \
      --format  both --out ./out

  # 样例数据
  python mccfo_report.py \
      --orders  sample_data/orders.sample.json \
      --coupons sample_data/coupons.sample.json \
      --points  sample_data/points.sample.json \
      --format  both --out ./out

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
from typing import Optional

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


def report_title(period: str) -> str:
    """期间跨月时别硬叫"月度财报"——MCP 的 order-list 只返回近期订单，
    用户可能两个月没消费，这时标题必须说实话。"""
    try:
        a, b = period.split("~")
        ym_a, ym_b = a.strip()[:7], b.strip()[:7]
    except Exception:
        return "McCFO 消费财报"
    return "McCFO 月度消费财报" if ym_a == ym_b else "McCFO 消费财报（跨月区间）"


def pct_opt(x: Optional[float]) -> str:
    """指标算不出来时显示「—」，而不是编一个 0.0% / 100.0% 出来。"""
    return "—" if x is None else pct(x)


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

        # 券面状态：MCP 给的是「未到使用时间 / 到店 / 外送」这类标签；
        # 一张券可以既是"有效"又"暂不可用"，两者必须分开表达。
        status = " · ".join(c.get("labels") or []) or ("可用" if c.get("usableNow", True) else "暂不可用")

        rows.append({
            "name": c.get("name", "(未命名券)"),
            "kind": c.get("kind", ""),
            "discount": d,
            "threshold": t,
            "estimated": bool(c.get("discountEstimated", False)),
            "expire": exp.isoformat() if exp else "—",
            "days_left": days_left,
            "level": expiry_level(days_left),
            "status": status,
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
    # 加权收益率只在「面额可信（非估算）」的券上算才有意义：
    # 估算券的门槛未知，收益率恒为 100%，混进来会把这个指标拉成虚高的常数。
    solid = [r for r in active if not r["estimated"] and r["threshold"] > 0]
    weighted_yield = (safe_div(sum(r["discount"] * r["yield"] for r in solid),
                               sum(r["discount"] for r in solid))
                      if solid else None)

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
    p = float(data.get("available", 0) or 0)
    p_exp = float(data.get("expiring", 0) or 0)

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
            "affordable": bool(item.get("affordable", cost <= p)),
            "need": need,
            "vpp": vpp,
            "priority": vpp * need,
        })

    prods.sort(key=lambda x: -x["priority"])
    cheapest = min((x["points"] for x in prods), default=0)
    return {
        "available": p,
        "expiring": p_exp,
        "pressure": safe_div(p_exp, p, 0.0),
        "products": prods,
        "cheapest": cheapest,
        # 真实账户常见情况：积分太少，一个都换不起 —— 必须明说，不能假装能换
        "shortfall": max(0.0, cheapest - p) if cheapest else 0.0,
        "any_affordable": any(x["affordable"] for x in prods),
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
            # 复购榜用「套餐内的真实单品」更准：随心配1+1 应该拆成 汉堡+可乐 计数，
            # 而不是记成一个叫"随心配1+1"的东西。components 由取数器从
            # comboItemList 提取；样例数据没有这个字段时退回商品名。
            names = it.get("components") or [it.get("name", "?")]
            for nm in names:
                item_count[nm] += qty
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
        if top["estimated"]:
            # 估算券：discountValue 是「用券价格」，不能说成"省 X 元"
            cond = f"券后价 {money(top['discount'])}（面额为估算值，实际省多少以结账为准）"
        elif top["threshold"] <= 0:
            cond = "无门槛，可直接省 " + money(top["discount"])
        else:
            cond = f"凑到 {money(top['threshold'])} 可省 {money(top['discount'])}"
        y = pct_opt(None if top["estimated"] else top["yield"])
        ytxt = f"收益率 {y}，" if y != "—" else ""
        if 0 < top["days_left"] <= 3:
            out.append(("优先用掉临期高价值券",
                        f"「{top['name']}」{top['days_left']} 天后到期，{ytxt}{cond}。"))
        else:
            out.append(("首选用这张券",
                        f"「{top['name']}」，{ytxt}优先级最高，{cond}。"))

    rest = [r for r in alert if not ranked or r["name"] != ranked[0]["name"]]
    if rest:
        r = rest[0]
        verb = f"券后价 {money(r['discount'])}" if r["estimated"] else f"可省 {money(r['discount'])}"
        out.append(("别漏掉这张临期券",
                    f"「{r['name']}」{r['days_left']} 天后到期，{verb}。"))

    if not p["products"]:
        out.append(("积分暂无可兑商品",
                    "当前积分商城没有取到带现金参考价的可兑换商品，本项跳过。"))
    elif not p.get("any_affordable", True):
        out.append(("积分还不够换",
                    f"可用 {p['available']:g} 分，最便宜的「{p['products'][0]['name']}」要 "
                    f"{p['products'][0]['points']:,} 分，还差 {p['shortfall']:g} 分 —— 先攒分，别硬换。"))
    else:
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

def render_markdown(c: dict, p: dict, o: dict, today: date, meta: Optional[dict] = None) -> str:
    L = []
    A = L.append
    meta = meta or {}

    A(f"# 📊 {report_title(o['period'])}")
    A("")
    A(f"> 生成时间：{today.isoformat()} ｜ 统计期间：{o['period']}")
    if meta:
        A(f"> 数据来源：**{meta.get('source', '麦当劳中国官方 MCP')}** ｜ "
          f"服务器时间 {meta.get('serverDate', '—')} ｜ 共调用 MCP 工具 {len(meta.get('toolsCalled', []))} 次")
        if meta.get("storeCode"):
            A(f"> 门店级券校验：门店 `{meta.get('storeCode')}` ｜ {meta.get('storeCheckNote', '')}")
    else:
        A("> 数据来源：**脱敏样例数据**（scripts/sample_data，非真实账户） ｜ 渲染：McCFO 财报引擎")
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
      f"本店可用 {money(c['usable_discount'])} ｜ 加权收益率 {pct_opt(c['weighted_yield'])}")
    A("")
    if c["alert"]:
        A("> 🔴 **临期预警**：" + "；".join(
            f"「{r['name']}」{r['days_left']} 天后到期（省 {money(r['discount'])}）"
            for r in c["alert"]))
        A("")
    A("| 券名 | 类型 | 面额 | 门槛 | 到期 | 剩余 | 收益率 | 优先级 | 状态 |")
    A("|---|---|---:|---:|---|---:|---:|---:|---|")
    for r in c["ranked"][:10]:
        est = "（估）" if r["estimated"] else ""
        A(f"| {r['name']} | {r['kind'] or '—'} | {money(r['discount'])}{est} | "
          f"{money(r['threshold'])} | {r['expire']} | {r['days_left']}天 | "
          f"{pct_opt(None if r['estimated'] else r['yield'])} | "
          f"{r['score']:.3f} | {r['level']} · {r['status']} |")
    unavailable = [r for r in c["rows"] if not r["expired"] and not r["store_ok"]]
    for r in unavailable:
        A(f"| {r['name']} | {r['kind'] or '—'} | {money(r['discount'])} | {money(r['threshold'])} | "
          f"{r['expire']} | {r['days_left']}天 | "
          f"{pct_opt(None if r['estimated'] else r['yield'])} | — | ⚪ 本店不可用 |")
    A("")
    A("> 面额标 **（估）** 的券：麦当劳 MCP 的 `discountInfo.discountValue` 语义是「用券价格」"
      "（如 9.9 元早餐两件套），并非实际省下的钱，只能作为账面价值参考。")
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
    if not p.get("any_affordable", True) and p["products"]:
        A(f"> ⚠️ **当前积分不足以兑换**：可用 {p['available']:g} 分，"
          f"最便宜的需 {p['cheapest']:,} 分，还差 {p['shortfall']:g} 分。"
          f"下表是「攒够之后该怎么换」的排行榜。")
        A("")
    A("| 商品 | 消耗积分 | 参考价值 | 每积分价值 | 优先级 | 够不够 |")
    A("|---|---:|---:|---:|---:|---|")
    for prod in p["products"][:8]:
        est = "（估）" if prod["value_estimated"] else ""
        ok = "✅ 够" if prod["affordable"] else "❌ 差"
        A(f"| {prod['name']} | {prod['points']:,} | {money(prod['value'])}{est} | "
          f"{prod['vpp']:.4f} 元/分 | {prod['priority']:.4f} | {ok} |")
    A("")

    # 结论
    A("## 七、接下来做什么")
    A("")
    for i, (label, text) in enumerate(build_actions(c, p), 1):
        A(f"{i}. **{label}**：{text}")
    A("")

    if meta and meta.get("degradations"):
        A("## 八、本次取数的降级与说明")
        A("")
        for d in meta["degradations"]:
            A(f"- {d}")
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


def render_html(c: dict, p: dict, o: dict, today: date, meta: Optional[dict] = None) -> str:
    meta = meta or {}

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
        f'<td>{_esc(r["name"])}</td><td>{_esc(r["kind"] or "—")}</td>'
        f'<td class="num">{money(r["discount"])}{" (估)" if r["estimated"] else ""}</td>'
        f'<td class="num">{money(r["threshold"])}</td><td>{_esc(r["expire"])}</td>'
        f'<td class="num">{r["days_left"]}天</td>'
        f'<td class="num">{pct_opt(None if r["estimated"] else r["yield"])}</td>'
        f'<td class="num">{r["score"]:.3f}</td>'
        f'<td>{_esc(r["level"])}<div class="sub">{_esc(r["status"])}</div></td></tr>'
        for r in c["ranked"][:10])

    for r in [r for r in c["rows"] if not r["expired"] and not r["store_ok"]]:
        coupon_rows += (
            f'<tr class="muted"><td>{_esc(r["name"])}</td><td>{_esc(r["kind"] or "—")}</td>'
            f'<td class="num">{money(r["discount"])}</td>'
            f'<td class="num">{money(r["threshold"])}</td><td>{_esc(r["expire"])}</td>'
            f'<td class="num">{r["days_left"]}天</td>'
            f'<td class="num">{pct_opt(None if r["estimated"] else r["yield"])}</td>'
            f'<td class="num">—</td><td>⚪ 本店不可用</td></tr>')

    prod_rows = "".join(
        f'<tr class="{"muted" if not x["affordable"] else ""}">'
        f'<td>{_esc(x["name"])}</td><td class="num">{x["points"]:,}</td>'
        f'<td class="num">{money(x["value"])}{"（估）" if x["value_estimated"] else ""}</td>'
        f'<td class="num">{x["vpp"]:.4f} 元/分</td><td class="num">{x["priority"]:.4f}</td>'
        f'<td>{"✅ 够" if x["affordable"] else "❌ 差"}</td></tr>'
        for x in p["products"][:8])

    est_note = ('<p class="note">面额标 <b>(估)</b> 的券：MCP 的 '
                '<code>discountInfo.discountValue</code> 语义是「用券价格」，'
                '并非实际省下的钱，仅作账面价值参考。</p>')

    short_note = ""
    if not p.get("any_affordable", True) and p["products"]:
        short_note = (f'<div class="alertbox"><h4>⚠️ 当前积分不足以兑换</h4>'
                      f'可用 <b>{p["available"]:g}</b> 分，最便宜的需 <b>{p["cheapest"]:,}</b> 分，'
                      f'还差 <b>{p["shortfall"]:g}</b> 分。下表是攒够之后该怎么换的排行榜。</div>')

    degrade_note = ""
    if meta.get("degradations"):
        lis = "".join(f"<li>{_esc(d)}</li>" for d in meta["degradations"])
        degrade_note = f'<section><h2>六、本次取数的降级与说明</h2><ul class="note-list">{lis}</ul></section>'

    source_line = (
        f'数据来源：{_esc(meta.get("source", "麦当劳中国官方 MCP"))} ｜ '
        f'服务器时间 {_esc(meta.get("serverDate", "—"))} ｜ '
        f'MCP 工具调用 {len(meta.get("toolsCalled", []))} 次'
        + (f' ｜ 门店校验 `{_esc(meta.get("storeCode", ""))}`' if meta.get("storeCode") else "")
    ) if meta else "数据来源：脱敏样例数据（scripts/sample_data，非真实账户）"

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
<title>{report_title(o['period'])} · {_esc(o['period'])}</title>
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
  td .sub {{ font-size:11px; color:var(--sub); }}
  p.note {{ font-size:12px; color:var(--sub); margin:10px 0 0; }}
  ul.note-list {{ font-size:13px; color:var(--sub); margin:0; padding-left:20px; }}
  ul.note-list li {{ margin:4px 0; }}
  footer {{ text-align:center; color:var(--sub); font-size:12px; margin-top:24px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="kicker">McCFO · 麦麦财务官</div>
    <h1>📊 {report_title(o['period'])}</h1>
    <div class="meta">统计期间 {_esc(o['period'])} ｜ 生成于 {today.isoformat()}</div>
    <div class="meta" style="margin-top:6px">{source_line}</div>
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
       本店可用 {money(c['usable_discount'])} ｜ 加权收益率 {pct_opt(c['weighted_yield'])}</p>
    {alert_box}
    <table>
      <tr><th>券名</th><th>类型</th><th style="text-align:right">面额</th><th style="text-align:right">门槛</th>
          <th>到期</th><th style="text-align:right">剩余</th><th style="text-align:right">收益率</th>
          <th style="text-align:right">优先级</th><th>状态</th></tr>
      {coupon_rows}
    </table>
    {est_note}
  </section>

  <section>
    <h2>四、积分资产 ROI</h2>
    <p>可用 <b>{p['available']:g}</b> 分 ｜ 即将过期 <b>{p['expiring']:g}</b> 分
       （压力 {pct(p['pressure'])}）</p>
    {short_note}
    <table>
      <tr><th>商品</th><th style="text-align:right">消耗积分</th><th style="text-align:right">参考价值</th>
          <th style="text-align:right">每积分价值</th><th style="text-align:right">优先级</th><th>够不够</th></tr>
      {prod_rows}
    </table>
  </section>

  <section>
    <h2>五、接下来做什么</h2>
    <ol class="actions">{action_items}</ol>
  </section>

  {degrade_note}

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
    ap.add_argument("--meta", default=None,
                    help="取数元信息 JSON（由 mccfo_fetch.py 产出）；传入后报告显示数据来源与降级说明")
    ap.add_argument("--out", default="./out", help="输出目录")
    ap.add_argument("--format", default="both", choices=["md", "html", "both"])
    ap.add_argument("--as-of", default=None, help="以指定日期为准（YYYY-MM-DD），默认今天")
    args = ap.parse_args()

    meta = load_json(args.meta) if args.meta else None

    # 有 meta 时以「取数当天的服务器日期」为基准，保证券的剩余天数与真实口径一致
    if meta and meta.get("serverDate"):
        try:
            today = datetime.strptime(meta["serverDate"][:10], "%Y-%m-%d").date()
        except Exception:
            today = date.today()
    else:
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
            f.write(render_markdown(c, p, o, today, meta))
        written.append(path)

    if args.format in ("html", "both"):
        path = os.path.join(args.out, "mccfo-report.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_html(c, p, o, today, meta))
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
