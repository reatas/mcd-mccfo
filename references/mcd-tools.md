# 麦当劳中国 MCP 工具速查表

> 来源：官方仓库 [M-China/mcd-mcp-server](https://github.com/M-China/mcd-mcp-server)（工具列表整理自官方 README，截至 2026-10）。
> 接入地址：`https://mcp.mcd.cn` ｜ 协议：Streamable HTTP ｜ 鉴权：`Authorization: Bearer <MCP_TOKEN>`
> 限流：**600 次/分钟**，超限返回 `429`；Token 无效返回 `401`。

## 一、全部 33 个工具（按业务域分组）

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
| `query-my-coupons` | 查询用户账户下**已有**的全部优惠券 |

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

## 三、实测踩坑要点（McCFO 开发中总结）

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

## 四、错误码

| code | 原因 | 处理 |
|---|---|---|
| 401 | Token 无效、过期或未提供 | 检查 `Authorization` 头与 Token 配置 |
| 429 | 触发限流（>600 次/分钟） | 降低频率，拉长调用间隔后重试 |
