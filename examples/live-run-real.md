# 真实账户跑通记录 · McCFO × 麦当劳 MCP

> 本文记录 **2026-10-11** 用真实 MCP Token 跑通 `scripts/mccfo_fetch.py` 的全过程：
> 每一步调了什么、真实返回长什么样、踩了哪些坑、代码怎么修的、最终财报口径怎么自检。
>
> 所有数据均来自麦当劳中国官方 MCP（https://mcp.mcd.cn）的真实返回。
>
> **已脱敏**：门店名中的城市/商圈信息替换为「××」，订单号仅保留前 6 位。
> 金额、券面、积分等用于验证口径的数字**保持原样**，否则这份记录就没有自检价值了。

---

## 一、握手：确认 Token 真的能用

```bash
$ curl -X POST https://mcp.mcd.cn \
    -H "Authorization: Bearer $MCD_MCP_TOKEN" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"mccfo","version":"1.1.0"}}}'
```

真实返回：

```json
{"jsonrpc":"2.0","id":1,"result":{
  "capabilities":{"tools":{}},
  "protocolVersion":"2025-06-18",
  "serverInfo":{"name":"mcd-mcp","version":"1.0.0"}}}
```

✅ `mcd-mcp v1.0.0` 握手成功。

紧接着 `tools/list` 返回 **35** 个工具（官方 README 表格里是 33 个，另有
`query-promotions`、`query-survey-coupon` 两个文档未列但确实存在）。

> **坑 A**：`Accept` 必须同时声明 `application/json` 与 `text/event-stream`。
> 只声明一个时部分网关返回的内容类型不对。这一点写进了 `scripts/mcd_mcp.py` 的默认请求头。

---

## 二、逐步取数（真实返回摘录）

### ① `now-time-info` —— 一切期间计算的基准

```json
{"success":true,"code":200,"data":{
  "timestamp":1791730006264,
  "formatted":"2026-10-11 22:46:46",
  "date":"2026-10-11","year":2026,"month":10,"day":11,
  "dayOfWeek":"SUNDAY","timezone":"GMT+08:00"}}
```

### ② `order-list` —— 拿到 10 单，但**只有实付额**

```json
{"success":true,"data":{"list":[{
  "orderId":"103026…（已截断）",
  "orderType":"1","beType":"1","storeCode":"3500155",
  "storeName":"麦当劳××万达餐厅",
  "createTime":"2026-08-26 17:14:35",
  "orderStatus":"订单已完成",
  "orderProductList":[{"productCode":"9900015566",
    "productName":"泰式炭烤风味猪猪堡随心配","quantity":1,
    "comboItemList":[{"name":"泰式炭烤风味猪猪堡"},{"name":"无糖可口可乐大杯"}]}],
  "realTotalAmount":"14.9"}]}}
```

> **坑 B（最重要的一条）**：列表里**没有原价、没有优惠额**，
> 直接拿 `realTotalAmount` 相加根本算不出"折扣率"。

### ③ `query-order` × 10 —— 补齐原价 / 优惠 / 就餐方式

```json
{"success":true,"data":{
  "totalAmount":"14.9","realTotalAmount":"14.9",
  "totalDiscountAmount":"0","couponList":[],
  "takeWay":"外带","deliveryPrice":"0","productPrice":"14.9"}}
```

修法：取数器逐单调 `query-order` 拿 `totalAmount` / `totalDiscountAmount`，
两者都缺时才用「原价 − 实付」兜底。

### ④ `query-my-coupons` —— 12 张券，但**面额字段在骗人**

```json
{"title":"中薯条买一送一",
 "discountInfo":{"discountValue":"13.5","discountDesc":"用券价格"},
 "tradeStartDate":"2026-10-12","tradeEndDate":"2026-10-16",
 "enable":0,"unavailableType":1,
 "instructions":{"labels":[{"text":"未到使用时间","code":2}]}}
```

> **坑 C**：`discountValue = 13.5` 的语义写在 `discountDesc` 里 —— **「用券价格」**，
> 不是"省了 13.5 元"。「火腿扒堡早餐两件套」的 `discountValue` 是 `9.9`，
> 那是**券后价**，不是优惠额。12 张券照搬字段当"面额"，账面价值会虚报到 ¥168.40。

修法（`parse_coupon_value`）：

| 券名形态 | 面额来源 | 是否估算 |
|---|---|---|
| 「满X减Y」 | 解析出「减Y」= 面额，「满X」= 门槛 | ❌ 精确 |
| 「买一送一」 | `discountValue` 作账面价值 | ✅ 标（估） |
| 「X元…」「…两件套」「…套餐」 | `discountValue` 作券后价 | ✅ 标（估） |

报告里所有估算券面额后面带 **（估）**，券表下方有一行说明；
加权收益率只在**非估算**的券上计算 —— 否则 12 张全是 100%，这个指标就没意义了。

### ⑤ `query-store-coupons`（门店 `3500155`）—— 返回了空数组

```json
{"success":true,"code":200,"data":[]}
```

> **坑 D**：空数组 **不等于** "这些券这家店不能用"。
> 12 张券的 `instructions.labels` 全是「未到使用时间」（最早的一张 10-12 才生效），
> 券还没进可用时间窗，`query-store-coupons` 自然返回空。

修法：取数器区分三种状态 —— `ok`（校验通过）/ `empty`（校验跳过）/ `skipped`（无门店）。
`empty` 时在报告里明确写「未做门店级校验，可用性以券面状态为准」，
**绝不**把券标成"本店不可用"。

### ⑥ `query-my-account` —— 积分是字符串，还可能是小数

```json
{"availablePoint":"22.8","accumulativePoint":"9587.3",
 "usedPoint":"6403","expiredPoint":"2660.5",
 "currentMouthExpirePoint":"0","frozenPoint":"0"}
```

> **坑 E**：全是字符串，且 `availablePoint = "22.8"` 是小数。
> 用 `int()` 会直接崩，用 `:,` 格式化会显示成 `22`。统一走 `fnum()` 容错 + `:g` 输出。

### ⑦ `mall-points-products` —— `price` 字段常年是 `"0"`

```json
{"spuName":"21.9元巨无霸可乐组合","spuId":17081,"point":"800","price":"0","catName":"到店专用"}
```

> **坑 F**：真实现金价不在 `price` 里，而在 `spuName`（正则 `(\d+(?:\.\d+)?)元`）
> 或 `mall-product-detail` 的 `skuList[].extTradePrice` 里。

```json
// mall-product-detail(spuId=17081)
{"spuName":"21.9元巨无霸可乐组合",
 "skuList":[{"skuId":17787,"extTradePrice":"21.9","points":"800","price":"0"}]}
```

> **坑 G**：`mall-product-detail` 对已下架商品返回
> `{"success":false,"code":610403,"message":"商品已下架"}` —— 而且是 **HTTP 200**。
> 只看 HTTP 状态码会漏掉所有业务失败。
> 修法：先用 `downTime` 过滤掉过期商品（50 件 → 7 件在售），再查详情，省掉大量无效调用。

---

## 三、最终跑通

```bash
$ export MCD_MCP_TOKEN='<你的Token>'
$ python scripts/mccfo_fetch.py --out out --render

① 取服务器当前时间 …        服务器日期：2026-10-11
② 取历史订单 order-list …    拿到 10 单
③ 取我的券 query-my-coupons … 拿到 12 张券
④ 门店级券校验（门店 3500155）…
⑤ 取积分账户 …               可用积分 22.8（本月将过期 0）
⑥ 取积分商城 …               在售可兑换 7 件（共 50 件商品）

✅ 取数完成
   期间 2026-02-03 ~ 2026-08-26 ｜ 10 单 ｜ 12 张券 ｜ 可用积分 22.8
   调用 MCP 工具 23 次 ｜ 降级说明 3 条
```

**23 次调用**，远低于 600 次/分钟的限流。

产物：

```
out/
├── raw/now_time.json, order_list.json, order_<id>.json × 10,
│       my_coupons.json, store_coupons.json, my_account.json,
│       mall_products.json, product_<spuId>.json × 7
├── normalized/orders.json, coupons.json, points.json, meta.json
├── mccfo-report.md
└── mccfo-report.html
```

---

## 四、财报口径自检（手算核对）

| 指标 | 报告值 | 手算核对 |
|---|---:|---|
| 订单数 | 10 | `order-list` 返回 10 条 ✅ |
| 原价合计 | ¥290.00 | 各单 `query-order.totalAmount` 相加 ✅ |
| 总支出 | ¥121.10 | 各单 `realTotalAmount` 相加 ✅ |
| 用券省下 | ¥168.90 | 290.00 − 121.10 = 168.90 ✅（**实付 + 优惠 = 原价，口径自洽**） |
| 折扣率 | 58.2% | 168.90 ÷ 290.00 = 58.24% ✅ |
| 实付率 | 41.8% | 121.10 ÷ 290.00 = 41.76%（58.2% + 41.8% = 100%） ✅ |
| 客单价 | ¥12.11 | 121.10 ÷ 10 ✅ |
| 券资产 | 12 张 / ¥168.40（估） | 12 张券 `discountValue` 之和，全部标记（估） ✅ |
| 加权收益率 | — | 12 张券全部是估算值，无可信门槛 → 显示「—」而不是编一个 100% ✅ |
| 积分 | 22.8 / 最便宜需 50 分 | 差 27.2 分，报告明说"还差多少"，不假装能换 ✅ |

> **这一轮自检抓出了一个真 bug**：第一版兜底逻辑写的是
> `if not disc and list_amt > pay > 0`，结果一单「圆筒买一送一」（原价 54.5、实付 **0**）
> 被算成 0 优惠，导致 实付 + 优惠 ≠ 原价，折扣率与实付率相加不到 100%。
> 改成 `if not disc and list_amt > pay` 后口径自洽。
> —— 真实数据才逼得出来的错误，样例数据永远碰不到。

**券表排序自检**：`Score = 面额 × 门槛效率 × (1 + 紧迫度) × 门店可用`

```
「巧克力味厚松饼猪柳蛋套餐」29.9 × 1.0 × (1 + 1/6) = 34.883   ← 排第 1
「火腿扒堡早餐两件套」      9.9  × 1.0 × (1 + 1/1) = 19.800   ← 排第 3（1 天后到期）
```

大额券 × 适中紧迫度 > 小额券 × 满紧迫度，符合模型设计：**优先把高面额券用掉**，
同时临期券单独在预警区列出，不会被漏掉。

---

## 五、真实数据带来的三个"必须诚实"的改动

1. **标题不再硬叫"月度财报"**。该账户最后一单是 08-26，10 月没有任何订单，
   `order-list` 只返回近期订单。报告标题自动切换为「消费财报（跨月区间）」，
   并在降级说明里写清楚"回退为已拉取到的 10 单"。
2. **算不出的指标显示「—」**。加权收益率、估算券的收益率都不编数字。
3. **积分不够就直说**。可用 22.8 分、最便宜要 50 分 —— 报告给出"还差 27.2 分，先攒分"，
   而不是生成一份看起来能换的排行榜。

---

## 六、复现方式

```bash
git clone https://github.com/reatas/mcd-mccfo.git
cd mcd-mccfo
cp .env.example .env          # 填入你在 open.mcd.cn/mcp 申请的 Token
python scripts/mccfo_fetch.py --out out --render
open out/mccfo-report.html
```

零第三方依赖，Python 3.8+ 即可。
