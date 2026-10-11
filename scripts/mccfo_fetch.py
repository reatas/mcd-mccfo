#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
McCFO 实时取数器  (麦麦财务官 · 取数层)
====================================================

**本脚本会真实调用麦当劳中国官方 MCP**（https://mcp.mcd.cn），
把你账户里的「订单 / 券 / 积分 / 门店 / 活动」拉下来，
归一化成 `mccfo_report.py` 能直接吃的 JSON，并可选直接渲染成财报。

这一层是整个项目「真数据」的落点：仓库里 scripts/sample_data/ 只是脱敏样例，
跑本脚本才会产生属于你自己的真实财报。

用法：
    export MCD_MCP_TOKEN='你的Token'        # 或在项目根目录建 .env
    python mccfo_fetch.py --out out --render

常用参数：
    --month 2026-10        统计月份（默认取 MCP 返回的当前月）
    --store-code 3500155   指定做「门店级券校验」的门店（默认取你最近一单的门店）
    --max-orders 20        最多拉多少单明细（query-order 是逐单调用）
    --max-products 12      最多取多少个积分商品的详情
    --render               取完直接调用 mccfo_report.py 渲染 md + html
    --quiet                不打印过程

产物：
    out/raw/*.json               MCP 原始返回（便于自查口径）
    out/normalized/orders.json   归一化订单
    out/normalized/coupons.json  归一化券
    out/normalized/points.json   归一化积分
    out/normalized/meta.json     本次取数元信息（时间、门店、工具调用清单、降级说明）
    out/mccfo-report.md/.html    （--render 时）

依赖：仅标准库（Python 3.8+）。Token 只读环境变量/--token，绝不落盘。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_mcp import McdMcpClient, McdAuthError, McdMcpError  # noqa: E402

# --------------------------------------------------------------------------
# 真实踩坑记录（2026-10 实测，逐条对应下面的代码）
#   坑1  order-list 只给 realTotalAmount，没有原价/优惠；
#        必须逐单 query-order 才有 totalAmount / totalDiscountAmount / couponList。
#   坑2  query-my-coupons 的 discountInfo.discountValue **不是优惠金额**，
#        它的语义写在 discountDesc 里（实测是「用券价格」）。
#        照搬成"面额"会把 9.9 元早餐券当成省 9.9 元——严重虚高。
#        所以面额一律从券名解析「满X减Y」，解析不到才退回 discountValue 并标 (估)。
#   坑3  mall-points-products 的 price 字段常年为 "0"，
#        真实现金价藏在 spuName（"21.9元巨无霸可乐组合"）或
#        mall-product-detail 的 skuList[].extTradePrice 里。
#   坑4  query-store-coupons 在券未进入可用时间窗时返回空数组，
#        这不等于"这些券这家店不能用"，必须区分「校验跳过」与「确认不可用」。
#   坑5  mall-product-detail 对已下架商品返回 success:false / code 610403，
#        要当作"取不到"跳过，而不是当错误中断。
#   坑6  积分/金额字段全是字符串（"22.8"），且积分可能是小数，必须做容错转换。
# --------------------------------------------------------------------------

CATEGORY_RULES = [
    # (品类, 关键词) —— 按顺序命中，越靠前优先级越高
    ("套餐", ["套餐", "件套", "随心配", "超值", "组合", "全家桶", "超值午餐", "超值晚餐", "1+1"]),
    ("甜品", ["麦旋风", "圣代", "甜筒", "新地", "冰淇淋", "派", "蛋挞", "松饼", "蛋糕", "雪糕"]),
    ("饮料", ["可乐", "咖啡", "拿铁", "奶茶", "雪碧", "果汁", "牛奶", "豆浆", "茶",
              "汽水", "美汁源", "酷", "柠檬", "燕麦", "美式", "卡布", "摩卡"]),
    ("小食", ["薯条", "薯格", "脆薯饼", "麦乐鸡", "鸡块", "鸡翅", "鸡排", "鸡米花",
              "盐酥鸡", "洋葱圈", "小食", "鸡腿", "肉骨鸡", "那么大"]),
    ("汉堡", ["堡", "汉堡", "卷", "饼"]),
]

COUPON_PATTERNS = [
    # (kind, 正则) —— 返回 (面额, 门槛, 是否估算)
    ("满减", re.compile(r"满\s*(\d+(?:\.\d+)?)\s*元?\s*减\s*(\d+(?:\.\d+)?)")),
    ("满减", re.compile(r"满\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)")),
    ("立减", re.compile(r"立减\s*(\d+(?:\.\d+)?)")),
    ("减",   re.compile(r"减\s*(\d+(?:\.\d+)?)\s*元")),
]


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def fnum(v: Any, default: float = 0.0) -> float:
    """把 MCP 的字符串数字安全地转 float（坑6）。"""
    if v is None or v == "":
        return default
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def inum(v: Any, default: int = 0) -> int:
    return int(fnum(v, default))


def classify(name: str) -> str:
    """按商品名关键词归类到 汉堡/小食/饮料/甜品/套餐/其他。"""
    for cat, keys in CATEGORY_RULES:
        for k in keys:
            if k in name:
                return cat
    return "其他"


def parse_coupon_value(title: str, fallback: float):
    """
    从券名解析 (面额, 门槛, kind, 是否估算)。

    坑2 的核心：只有「满X减Y」这类券的面额是可信的精确值；
    「买一送一」「X元两件套」的 discountValue 是**券后价**不是省下的钱，
    只能当作估算的账面价值，并在报告里标 (估)。
    """
    t = title or ""
    for kind, pat in COUPON_PATTERNS:
        m = pat.search(t)
        if m:
            g = m.groups()
            if len(g) == 2:
                return fnum(g[1]), fnum(g[0]), kind, False
            return fnum(g[0]), 0.0, kind, False
    if "买一送一" in t or "买1送1" in t:
        return fallback, 0.0, "买赠", True
    if "免费" in t:
        return fallback, 0.0, "免费", True
    if re.search(r"\d+(?:\.\d+)?\s*元", t):
        return fallback, 0.0, "特价", True
    if "套餐" in t or "件套" in t:
        return fallback, 0.0, "套餐", True
    return (fallback or 0.0), 0.0, "权益", True


def parse_cash_from_name(name: str) -> Optional[float]:
    """从商品名里抠现金参考价（坑3）："21.9元巨无霸可乐组合" → 21.9"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", name or "")
    return float(m.group(1)) if m else None


def channel_of(o: dict) -> str:
    """订单渠道：到店取餐 / 麦乐送 / 得来速。"""
    ot, bt = str(o.get("orderType", "")), str(o.get("beType", ""))
    if ot == "2":
        return "麦乐送"
    if bt == "5":
        return "得来速"
    return "到店取餐"


# --------------------------------------------------------------------------
# 取数 + 归一化
# --------------------------------------------------------------------------

class McCFOFetcher:
    def __init__(self, cli: McdMcpClient, out: str, verbose: bool = True,
                 max_orders: int = 20, max_products: int = 12):
        self.cli = cli
        self.out = out
        self.verbose = verbose
        self.max_orders = max_orders
        self.max_products = max_products
        self.raw_dir = os.path.join(out, "raw")
        self.norm_dir = os.path.join(out, "normalized")
        os.makedirs(self.raw_dir, exist_ok=True)
        os.makedirs(self.norm_dir, exist_ok=True)
        self.meta: Dict[str, Any] = {
            "fetchedAt": datetime.now().isoformat(timespec="seconds"),
            "source": "麦当劳中国官方 MCP (https://mcp.mcd.cn)",
            "toolsCalled": [],
            "degradations": [],
        }

    def say(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def _save_raw(self, name: str, data: Any) -> None:
        try:
            with open(os.path.join(self.raw_dir, name + ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _call(self, tool: str, args: Optional[dict] = None, raw_name: Optional[str] = None) -> Any:
        """调用工具并记录到 meta；返回 data 部分（失败返回 None 并记降级）。"""
        self.meta["toolsCalled"].append({"tool": tool, "args": args or {}})
        try:
            res = self.cli.call(tool, args)
        except McdAuthError:
            raise
        except Exception as e:  # noqa: BLE001
            self._degrade(f"{tool} 调用失败：{e}")
            self.say(f"  ⚠️ {tool} 失败（已跳过）：{e}")
            return None
        if raw_name:
            self._save_raw(raw_name, res)
        if isinstance(res, dict):
            if res.get("success") is False:
                self._degrade(f"{tool} 返回失败：{res.get('message')}")
                return None
            return res.get("data")
        return res

    def _degrade(self, msg: str) -> None:
        """降级说明去重计数：8 次「商品已下架」只应出现一行，而不是 8 行。"""
        for d in self.meta["degradations"]:
            if d["msg"] == msg:
                d["count"] += 1
                return
        self.meta["degradations"].append({"msg": msg, "count": 1})

    # -- 主流程 -------------------------------------------------------------
    def run(self, month: Optional[str] = None, store_code: Optional[str] = None) -> Dict[str, Any]:
        self.say("① 取服务器当前时间 …")
        now = self._call("now-time-info", raw_name="now_time") or {}
        today_s = str(now.get("date") or datetime.now().date().isoformat())
        today = datetime.strptime(today_s, "%Y-%m-%d").date()
        self.meta["serverDate"] = today_s
        self.meta["serverTime"] = now.get("formatted") or now.get("datetime")
        self.say(f"   服务器日期：{today_s}")

        month = month or today.strftime("%Y-%m")
        self.meta["month"] = month

        orders = self.fetch_orders(today)
        store_code = store_code or self.pick_store(orders)
        coupons = self.fetch_coupons(today, store_code)
        points = self.fetch_points(today)

        self.meta["storeCode"] = store_code
        self.meta["storeCheckStatus"] = coupons.pop("_storeCheckStatus", "skipped")
        self.meta["storeCheckNote"] = coupons.pop("_storeCheckNote", "")

        # 降级说明：去重后压平成字符串（"×N" 表示重复次数）
        self.meta["degradations"] = [
            d["msg"] + (f"（×{d['count']}）" if d["count"] > 1 else "")
            for d in self.meta["degradations"]
        ]

        # 落盘
        self._write("orders.json", orders)
        self._write("coupons.json", coupons)
        self._write("points.json", points)
        self._write("meta.json", self.meta)
        return {"orders": orders, "coupons": coupons, "points": points, "meta": self.meta}

    def _write(self, name: str, data: Any) -> None:
        p = os.path.join(self.norm_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # -- 订单 ---------------------------------------------------------------
    def fetch_orders(self, today) -> dict:
        self.say("② 取历史订单 order-list …")
        lst = self._call("order-list", raw_name="order_list") or {}
        raw_orders = (lst or {}).get("list", []) if isinstance(lst, dict) else (lst or [])
        raw_orders = [o for o in raw_orders if isinstance(o, dict)]
        self.say(f"   拿到 {len(raw_orders)} 单")

        # 坑1：逐单补明细才有原价/优惠/就餐方式
        norm: List[dict] = []
        for i, o in enumerate(raw_orders[: self.max_orders]):
            oid = o.get("orderId")
            detail = {}
            if oid:
                d = self._call("query-order", {"orderId": oid})
                detail = (d or {}) if isinstance(d, dict) else {}
                if detail:
                    self._save_raw(f"order_{oid}", detail)

            create = str(o.get("createTime") or detail.get("createTime") or "")[:16]
            products = detail.get("orderProductList") or o.get("orderProductList") or []

            items = []
            for p in products:
                pname = p.get("productName") or p.get("name") or "未知商品"
                items.append({
                    "name": pname,
                    "category": classify(pname),          # MCP 不给品类，按名启发式归类
                    "qty": inum(p.get("quantity"), 1),
                    "price": fnum(p.get("price")),
                    "components": [c.get("itemName") or c.get("name")
                                   for c in (p.get("comboItemList") or [])] or None,
                })

            list_amt = fnum(detail.get("totalAmount")) or sum(
                fnum(p.get("price")) * inum(p.get("quantity"), 1) for p in products)
            pay = fnum(detail.get("realTotalAmount") or o.get("realTotalAmount"))
            disc = fnum(detail.get("totalDiscountAmount"))
            # 兜底：官方没给优惠额时用「原价 − 实付」。
            # 注意不能加 pay > 0 的条件 —— 真实数据里有「0 元兑的券」（原价 54.5 实付 0），
            # 漏掉它会导致 实付 + 优惠 ≠ 原价，报告内部口径不自洽。
            if not disc and list_amt > pay:
                disc = round(list_amt - pay, 2)

            norm.append({
                "orderId": oid or f"(no-id-{i})",
                "date": create,
                "store": o.get("storeName") or detail.get("storeName") or "未知门店",
                "storeCode": o.get("storeCode") or "",
                "channel": channel_of(o),
                "takeWay": detail.get("takeWay") or "",
                "status": o.get("orderStatus") or detail.get("orderStatus") or "",
                "items": items,
                "listAmount": round(list_amt, 2),
                "discount": round(disc, 2),
                "payAmount": round(pay, 2),
                "deliveryFee": fnum(detail.get("realDeliveryPrice") or detail.get("deliveryPrice")),
                "coupons": [c.get("couponName") or c.get("name") or "券"
                            for c in (detail.get("couponList") or [])],
            })

        norm.sort(key=lambda x: x["date"], reverse=True)

        # 期间：默认当月；环比：用同一批数据里的上一个月
        cur = [o for o in norm if o["date"].startswith(self.meta["month"])]
        all_in_month = cur or norm
        period_start = min((o["date"][:10] for o in all_in_month), default="—")
        period_end = max((o["date"][:10] for o in all_in_month), default="—")

        prev_month = self._prev_month(self.meta["month"])
        prev_total = sum(o["payAmount"] for o in norm if o["date"].startswith(prev_month)) or None

        self.meta["orderCountFetched"] = len(norm)
        self.meta["orderCountInMonth"] = len(cur)
        if not cur:
            self._degrade(
                f"order-list 返回的近期订单里没有 {self.meta['month']} 的订单，"
                f"报告期间已回退为「已拉取到的 {len(norm)} 单」（{period_start} ~ {period_end}）")
        if prev_total is None:
            self._degrade(
                "近期订单中没有上一个月的记录，环比数据不可用（MCP 的 order-list 只返回近期订单）")

        return {
            "_note": "真实数据：order-list + query-order 归一化结果",
            "period": f"{period_start} ~ {period_end}",
            "prevPeriodTotal": prev_total,
            "orders": all_in_month,
        }

    @staticmethod
    def _prev_month(ym: str) -> str:
        y, m = int(ym[:4]), int(ym[5:7])
        return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"

    @staticmethod
    def pick_store(orders: dict) -> Optional[str]:
        """默认取最近一单的门店做门店级券校验。"""
        for o in orders.get("orders", []):
            if o.get("storeCode"):
                return str(o["storeCode"])
        return None

    # -- 券 -----------------------------------------------------------------
    def fetch_coupons(self, today, store_code: Optional[str]) -> dict:
        self.say("③ 取我的券 query-my-coupons …")
        data = self._call("query-my-coupons", {"page": "1", "pageSize": "200"},
                          raw_name="my_coupons") or {}
        raw = (data or {}).get("coupons", []) if isinstance(data, dict) else (data or [])
        self.say(f"   拿到 {len(raw)} 张券")

        # 门店级可用性校验（坑4）
        store_ok_ids = set()
        status_note = ""
        if store_code:
            self.say(f"④ 门店级券校验 query-store-coupons（门店 {store_code}）…")
            sc = self._call("query-store-coupons",
                            {"orderType": 1, "beType": 1, "storeCode": store_code},
                            raw_name="store_coupons")
            sc_list = (sc or {}).get("coupons", []) if isinstance(sc, dict) else (sc or [])
            for c in sc_list:
                for key in ("id", "code", "couponId", "promotionId"):
                    if c.get(key):
                        store_ok_ids.add(str(c[key]))
            if sc_list:
                status_note = f"已用 query-store-coupons 在门店 {store_code} 校验，{len(sc_list)} 张确认可用"
                check = "ok"
            else:
                # 坑4：空数组 ≠ 不可用（券可能还没进可用时间窗）
                status_note = (f"query-store-coupons 在门店 {store_code} 返回空列表"
                               f"（常见于券未进入可用时间窗），未做门店级校验，可用性以券面状态为准")
                check = "empty"
                self._degrade(status_note)
        else:
            status_note = "未能确定门店（无历史订单且未指定 --store-code），跳过门店级校验"
            check = "skipped"
            self._degrade(status_note)

        coupons = []
        for c in raw:
            title = c.get("title") or c.get("couponName") or "(未命名券)"
            di = c.get("discountInfo") or {}
            fallback = fnum(di.get("discountValue"))
            # 坑2：discountValue 的语义是 discountDesc（实测「用券价格」），只能当兜底
            discount, threshold, kind, est = parse_coupon_value(title, fallback)

            end = str(c.get("tradeEndDate") or "")[:10]
            labels = [l.get("text") for l in ((c.get("instructions") or {}).get("labels") or [])]
            available = bool(store_ok_ids) is False or (
                any(str(c.get(k)) in store_ok_ids for k in ("id", "code", "promotionId")))

            coupons.append({
                "name": title,
                "code": c.get("code") or "",
                "kind": kind,
                "discount": discount,
                "threshold": threshold,
                "discountEstimated": est,
                "discountDesc": di.get("discountDesc") or "",
                "expireDate": end,
                "startDate": str(c.get("tradeStartDate") or "")[:10],
                "weekdays": c.get("weekdays") or [],
                "labels": labels,
                "usableNow": bool(c.get("enable") == 1),
                "storeAvailable": available if check == "ok" else True,
                "fitBoost": 1.0,
            })

        return {
            "_note": "真实数据：query-my-coupons 归一化结果（面额优先按券名解析，见 discountEstimated）",
            "coupons": coupons,
            "_storeCheckStatus": check,
            "_storeCheckNote": status_note,
        }

    # -- 积分 ---------------------------------------------------------------
    def fetch_points(self, today) -> dict:
        self.say("⑤ 取积分账户 query-my-account …")
        acc = self._call("query-my-account", raw_name="my_account") or {}
        available = fnum(acc.get("availablePoint"))
        expiring = max(fnum(acc.get("currentMouthExpirePoint")),
                       fnum(acc.get("nextMouthExpirePoint")))
        self.say(f"   可用积分 {available:g}（本月将过期 {expiring:g}）")

        self.say("⑥ 取积分商城 mall-points-products …")
        prods = self._call("mall-points-products", raw_name="mall_products") or []
        # 坑5：已下架商品再去查详情必然 610403，先用 downTime 过滤掉，省掉无效调用
        def on_sale(p: dict) -> bool:
            down = str(p.get("downTime") or "")[:10]
            if not down:
                return True
            try:
                return datetime.strptime(down, "%Y-%m-%d").date() >= today
            except ValueError:
                return True
        cand = [p for p in prods
                if isinstance(p, dict) and fnum(p.get("point")) > 0 and on_sale(p)]
        cand.sort(key=lambda p: fnum(p.get("point")))
        self.say(f"   在售可兑换 {len(cand)} 件（共 {len(prods)} 件商品）")

        out = []
        for p in cand[: self.max_products]:
            pts = fnum(p.get("point"))
            cash, est = None, True
            # 坑3：优先从 mall-product-detail 的 skuList[].extTradePrice 取真实价
            det = self._call("mall-product-detail", {"spuId": p.get("spuId")})
            if isinstance(det, dict):
                self._save_raw(f"product_{p.get('spuId')}", det)
                for sku in (det.get("skuList") or []):
                    v = fnum(sku.get("extTradePrice"))
                    if v > 0:
                        cash, est = v, False
                        break
                    if fnum(sku.get("points")) > 0:
                        pts = fnum(sku.get("points"))
            if cash is None:
                cash = parse_cash_from_name(p.get("spuName", "")) or fnum(p.get("price"))
            if not cash:
                continue
            out.append({
                "name": p.get("spuName"),
                "spuId": p.get("spuId"),
                "category": p.get("catName") or "",
                "points": int(pts),
                "cashPrice": round(cash, 2),
                "valueEstimated": est,
                "affordable": pts <= available,
                "needMatch": 1.0,
            })

        out.sort(key=lambda x: -(x["cashPrice"] / x["points"] if x["points"] else 0))
        if not out:
            self._degrade("积分商城里没有取到带现金参考价的可兑换商品")

        return {
            "_note": "真实数据：query-my-account + mall-points-products 归一化结果",
            "available": available,
            "expiring": expiring,
            "accumulative": fnum(acc.get("accumulativePoint")),
            "used": fnum(acc.get("usedPoint")),
            "expired": fnum(acc.get("expiredPoint")),
            "frozen": fnum(acc.get("frozenPoint")),
            "products": out,
        }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="McCFO 实时取数器（真实调用麦当劳 MCP）")
    ap.add_argument("--out", default="./out", help="输出目录")
    ap.add_argument("--token", default=None, help="MCP Token；默认读环境变量 MCD_MCP_TOKEN")
    ap.add_argument("--month", default=None, help="统计月份 YYYY-MM，默认当前月")
    ap.add_argument("--store-code", default=None, help="门店编码，用于门店级券校验")
    ap.add_argument("--max-orders", type=int, default=20)
    ap.add_argument("--max-products", type=int, default=12)
    ap.add_argument("--render", action="store_true", help="取完直接渲染财报")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    try:
        cli = McdMcpClient(a.token, verbose=not a.quiet) if a.token \
            else McdMcpClient.from_env(verbose=not a.quiet)
    except McdAuthError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        f = McCFOFetcher(cli, a.out, verbose=not a.quiet,
                         max_orders=a.max_orders, max_products=a.max_products)
        res = f.run(month=a.month, store_code=a.store_code)
    except McdAuthError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2
    except McdMcpError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    m = res["meta"]
    print()
    print("✅ 取数完成 →", os.path.abspath(a.out))
    print(f"   期间 {res['orders']['period']} ｜ {len(res['orders']['orders'])} 单 ｜ "
          f"{len(res['coupons']['coupons'])} 张券 ｜ 可用积分 {res['points']['available']:g}")
    print(f"   调用 MCP 工具 {len(m['toolsCalled'])} 次 ｜ 降级说明 {len(m['degradations'])} 条")
    for d in m["degradations"]:
        print("   ⚠️", d)

    print("\n摘要：", json.dumps({
        "orders": len(res["orders"]["orders"]),
        "coupons": len(res["coupons"]["coupons"]),
        "storeCheck": m.get("storeCheckStatus"),
        "store": m.get("storeCode"),
        "availablePoints": res["points"]["available"],
        "exchangeable": len(res["points"]["products"]),
    }, ensure_ascii=False))

    if a.render:
        import subprocess
        cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "mccfo_report.py"),
               "--orders", os.path.join(a.out, "normalized", "orders.json"),
               "--coupons", os.path.join(a.out, "normalized", "coupons.json"),
               "--points", os.path.join(a.out, "normalized", "points.json"),
               "--meta", os.path.join(a.out, "normalized", "meta.json"),
               "--out", a.out, "--format", "both"]
        r = subprocess.run(cmd)
        return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
