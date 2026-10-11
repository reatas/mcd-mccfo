# 麦当劳中国 MCP 工具速查表

> 来源：官方仓库 [M-China/mcd-mcp-server](https://github.com/M-China/mcd-mcp-server)（工具列表整理自官方 README，截至 2026-10）。
> 接入地址：`https://mcp.mcd.cn` ｜ 协议：Streamable HTTP ｜ 鉴权：`Authorization: Bearer <MCP_TOKEN>`
> 限流：**600 次/分钟**，超限返回 `429`；Token 无效返回 `401`。

## 一、全部 35 个工具（按业务域分组）

> 实测：`tools/list` 于 2026-10 返回 **35** 个工具（官方 README 表格列了 33 个，
> 另有 `query-survey-coupon`、`query-promotions` 两个未列入表格但确实存在）。

### 🥗 营养
| Tool | 说明 |
|---|---|
| `list-nutrition-foods` | 餐品营养信息列表：能量、蛋白质、脂肪、碳水、钠、钙。用于热量/营养分析、指定热量套餐搭配 |

### 🚚 外送地址与门店
| Tool | 说明 |
|---|---|
| `delivery-query-addresses` | 查询用户已创建的配送地址列表 |
| `delivery-create-address` | 新增配送地址（用户无地址时） |
| `delivery-query-stores` | 外送场景下查询收货地址附近可配送门店 |
| `query-nearby-stores` | 查询指定地址附近的麦当劳餐厅 |
| `query-meal-assistance` | 企业团餐场景下查询门店支持的助餐服务 |

### 🎫 优惠券
| Tool | 说明 |
|---|---|
| `query-store-coupons` | 查询**当前门店下可用**的优惠券列表（券可用性校验的关键） |
| `available-coupons` | 查询当前可**领取**的麦麦省券列表 |
| `auto-bind-coupons` | **一键领取**麦麦省所有当前可用的券（写操作，需用户同意） |
| `query-my-coupons` | 查询用户账户下**已有**的全部优惠券（支持 `page` / `pageSize`，最大 200） |
| `query-promotions` | 营销活动查询 |
| `query-survey-coupon` | 问卷券查询 |

### 🍔 点餐（菜单 / 价格 / 订单）
| Tool | 说明 |
|---|---|
| `query-meals` | 查询当前门店**可售卖**的餐品菜单（分类、编码、标签） |
| `query-meal-detail` | 按餐品编码查询详情与套餐组成，查看可替换选项 |
| `calculate-price` | 按选购清单（可含券）计算金额、配送费、优惠、应付总价 |
| `create-order` | 创建订单（写操作，返回订单详情与支付链接） |
| `cancel-order` | 取消点餐订单（写操作） |
| `query-order` | 查询单个订单的状态、内容、配送信息 |
| `order-list` | 查询近期到店/外送**历史订单**（不含商城订单） |

### 📅 活动
| Tool | 说明 |
|---|---|
| `campaign-calendar` | 当月营销活动日历：进行中 / 往期 / 未来 |
| `query-party-city` | 主题活动可参与城市列表 |
| `query-party-store` | 指定城市下主题活动可参与门店 |
| `query-partystore-date` | 指定门店主题活动可预约日期 |
| `query-partystore-session` | 指定门店+日期下可预约场次 |
| `party-order-create` | 创建主题活动订单（写操作） |

### 💰 积分与麦麦商城
| Tool | 说明 |
|---|---|
| `query-my-account` | 积分账户：可用、累计、冻结、即将过期积分 |
| `mall-points-products` | 麦麦商城商品列表（积分兑换 / 现金购买） |
| `mall-product-detail` | 商城商品详情：图片、所需积分、有效期、说明 |
| `mall-create-order` | 积分兑换商品下单（写操作，扣积分/库存） |
| `mall-order-list` | 麦麦商城近一年订单列表 |
| `mall-order-detail` | 麦麦商城订单详情：支付积分、支付金额、状态 |
| `query-lottery-info` | 积分抽奖活动信息：状态、奖品、消耗规则 |
| `draw-lottery` | 执行一次抽奖（**消耗积分/次数**，写操作） |
| `query-my-prizes` | 查询抽奖所获全部奖品记录（分页） |

### ⏰ 系统
| Tool | 说明 |
|---|---|
| `now-time-info` | 返回当前完整时间信息（**每月/每日分析的第一步，务必先调**） |

## 二、写操作清单（McCFO 必须先获得用户明确确认）

| Tool | 副作用 |
|---|---|
| `create-order` | 生成订单，产生消费 |
| `cancel-order` | 取消订单 |
| `party-order-create` | 生成主题活动订单 |
| `mall-create-order` | 扣除积分/库存 |
| `draw-lottery` | 消耗积分或抽奖次数 |
| `auto-bind-coupons` | 批量领取券（改变账户状态） |
| `delivery-create-address` | 新增地址（改变账户数据） |

## 三、真实返回字段速查（2026-10 实测抓包整理）

> 这一节是 `scripts/mccfo_fetch.py` 归一化逻辑的依据，写归一化代码前先看这里。

### `order-list` → `data.list[]`

| 字段 | 说明 |
|---|---|
| `orderId` / `createTime` / `storeCode` / `storeName` / `orderStatus` | 订单号、下单时间、门店、状态 |
| `orderType` | `1`=到店（含自取/得来速），`2`=外送 |
| `beType` | `1`=到店自提，`5`=得来速，`2`=麦乐送到家，`6`=企业团餐 |
| `orderProductList[]` | `productCode` / `productName` / `quantity` / `comboItemList[]` |
| `realTotalAmount` | **实付** |

⚠️ **列表里没有原价、没有优惠额**。要算折扣率必须逐单再调 `query-order`。

### `query-order` → `data`

`totalAmount`（原价）｜`realTotalAmount`（实付）｜`totalDiscountAmount`（优惠）｜
`couponList[]`｜`deliveryPrice` / `realDeliveryPrice`｜`takeWay`（外带/堂食）｜`orderProductList[].price`

### `query-my-coupons` → `data.coupons[]`

| 字段 | 说明 |
|---|---|
| `title` | 券名 —— **面额只能从这里解析**（见踩坑 11） |
| `discountInfo.discountValue` + `discountInfo.discountDesc` | 数值 + 语义（实测 `discountDesc` =「用券价格」） |
| `tradeStartDate` / `tradeEndDate` / `tradeStartDateTime` / `tradeEndDateTime` | 有效期 |
| `weekdays[]` / `tradeTimePeriods[]` | 可用星期、可用时段 |
| `instructions.labels[].text` | 「未到使用时间」「到店」等状态标签 |
| `enable` / `unavailableType` | 是否可核销 |
| `orderTypes[]` / `beTypes[]` / `tags[].label` | 渠道限制 |

### `query-my-account` → `data`

`availablePoint`（可用）｜`accumulativePoint`（累计）｜`usedPoint`（已用）｜
`expiredPoint`（已过期）｜`frozenPoint`（冻结）｜
`currentMouthExpirePoint` / `nextMouthExpirePoint`（本月/下月将过期）

⚠️ **全是字符串，且可能是小数**（实测 `"22.8"`），必须做容错转换。

### `mall-points-products` → `data[]`

`spuName` / `spuId` / `point`（所需积分）/ `price` / `catName` / `upTime` / `downTime` / `status`

⚠️ `price` 常年为 `"0"`；真实现金价藏在 `spuName`（`"21.9元巨无霸可乐组合"`）
或 `mall-product-detail` 的 `skuList[].extTradePrice` 里。

### `campaign-calendar` → `data`

`currentTime` ｜ `dailyList[{date, dateText, events[{activityCode, activityStage, price, priceSuffix, articleDto{title, content, highlights, buttonText}}]}]` ｜ `subscribedEvents`

### `query-nearby-stores` → `data[]`

`storeCode` / `storeName` / `address` / `reservation` / `reservationTimeOptions[]`
入参：`beType`（必填，1=到店自提 5=得来速）+ `searchType`（必填，1=收藏 2=按位置）+ 可选 `city` / `keyword`

## 四、实测踩坑要点（McCFO 开发中总结，16 条）

1. **金额是「元」字符串，积分不是元。** `query-meals`、`calculate-price`、`query-order` 返回的金额已是「元」，直接展示，不要做分↔元换算。所有 `points` 类字段是积分，**不做任何汇率换算**。
2. **券分两层，必须都查。** `query-my-coupons` 是账户卡包（静态），`query-store-coupons` 是"这张券在这家店现在能不能用"（动态）。只查前者会给出错误的可用性结论——这是最容易翻车的地方。
3. **`now-time-info` 要先调。** 所有涉及"本月""剩余天数""临期"的判断都依赖它，不要用模型自身的时间直觉。
4. **`calculate-price` 是唯一可信的价格来源。** 不要自己拿菜单价格乘减券相加，价格、配送费、券叠加规则都可能变。
5. **`order-list` 不含商城订单。** 要看积分商城的消费，必须另外调 `mall-order-list`。
6. **限流 600/分钟。** 组合试算时控制候选数量（建议 ≤8 个）；批量逐单查询要先过滤。
7. **外送 vs 到店是两套门店接口。** 外送用 `delivery-query-stores`，到店/通用用 `query-nearby-stores`。
8. **套餐可以换品，但不能换单品。** `query-meal-detail` 能看套餐可替换项；官方说明套餐内容暂不支持完全自定义（以最新版本为准）。
9. **支付无法在 MCP 内完成。** `create-order` 只返回支付链接，最终支付需用户本人在麦当劳 App/小程序完成。
10. **空结果 ≠ 出错。** 券列表为空、订单为空是正常状态，如实汇报即可，不要补编数据。
11. **⚠️ `query-my-coupons` 的 `discountValue` 不是优惠金额。** 实测 `discountDesc` =「用券价格」：一张「火腿扒堡早餐两件套」的 `discountValue` 是 `9.9`，那是**券后价**，不是省下的钱。照搬成面额会让 12 张券的账面价值虚高到 ¥168。正确做法：券名形如「满X减Y」时取「减Y」为精确面额；「买一送一」「X元套餐」「X元两件套」只能把 `discountValue` 当**估算**账面价值并标（估）。
12. **⚠️ `order-list` 没有原价和优惠。** 只有 `realTotalAmount`。折扣率必须靠逐单 `query-order` 的 `totalAmount` / `totalDiscountAmount` 兜底，否则算不出「省了多少」。
13. **⚠️ `mall-points-products` 的 `price` 是 `"0"`。** 真实现金价在 `spuName`（正则 `(\d+(?:\.\d+)?)元`）或 `mall-product-detail` 的 `skuList[].extTradePrice`。两条路都取不到就别硬算 VPP。
14. **⚠️ `query-store-coupons` 返回 `[]` 不等于"不可用"。** 券未进入可用时间窗时（`instructions.labels` = 「未到使用时间」）同样返回空。必须区分「校验通过 / 校验跳过 / 确认不可用」三种状态再说结论。
15. **⚠️ `mall-product-detail` 对已下架商品返回 `success:false` + `code 610403`。** 先用 `downTime` 过滤掉过期商品，能省掉大量无效调用；失败要当"取不到"跳过，不要中断流程。
16. **⚠️ 数字字段全是字符串，积分还可能是小数。** `availablePoint: "22.8"`、`realTotalAmount: "1"`。任何 `float(v)` 都要包一层容错，积分用 `:g` 格式化输出，别用 `:,`（会显示成 `22`）。
17. **⚠️ 实付可能为 `0`（0 元兑的券）。** 真实数据里出现过「圆筒买一送一」原价 54.5、实付 0。做「原价 − 实付」兜底优惠额时，条件别写成 `pay > 0`，否则这一单会被算成 0 优惠，导致 实付 + 优惠 ≠ 原价、折扣率与实付率相加不到 100%。口径必须自洽，跑完用 `实付 + 优惠 == 原价` 自检一遍。

## 五、错误码

| code | 原因 | 处理 |
|---|---|---|
| 401 | Token 无效、过期或未提供 | 检查 `Authorization` 头与 Token 配置 |
| 429 | 触发限流（>600 次/分钟） | 降低频率，拉长调用间隔后重试 |
| 610403 | 麦麦商城商品已下架 | 跳过该商品（常见于 `mall-product-detail`） |

> 业务错误不走 HTTP 状态码，而是 **HTTP 200 + 响应体 `success:false` + `code`**。
> 客户端必须同时检查两者，只看 HTTP 状态会漏掉所有业务失败。
