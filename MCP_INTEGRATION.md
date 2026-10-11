# MCP 接入说明

> 本文说明 McCFO 实际使用的麦当劳 MCP Server、具体 Tool、调用流程，以及这些调用如何转化为业务价值。

---

## 一、使用的 MCP Server

| 项目 | 值 |
|---|---|
| Server 名称 | 麦当劳中国 MCP Server |
| 官方仓库 | https://github.com/M-China/mcd-mcp-server |
| 开放平台 | https://open.mcd.cn/mcp |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | **Streamable HTTP** |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| Token 获取 | open.mcd.cn/mcp → 手机号登录 → 控制台 → 激活 |
| 限流 | 每 Token **600 次/分钟**，超限返回 `429` |
| 部署形态 | 远程托管（用户零安装，配 URL + Token 即可用） |
| 脱敏配置 | 见仓库根目录 [`mcp-config.example.json`](./mcp-config.example.json) |

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

> 本仓库不包含任何真实 Token。示例配置仅使用环境变量占位符（对应 `CONTEST_DECLARATION.md` 的信息安全声明）。

---

## 二、使用的 Tool 清单

麦当劳 MCP 实际开放 **35** 个 Tool（2026-10 用 `tools/list` 实测；官方 README 表格列了 33 个，另有 `query-promotions`、`query-survey-coupon`）。
McCFO 使用了其中 **19 个**，按"只读 / 写"分类如下。

> 用 `python scripts/mcd_mcp.py list-tools` 可以自己复现这个清单。

### 2.1 核心只读 Tool（16 个）

| # | Tool | McCFO 用它做什么 | 对应模式 |
|---:|---|---|---|
| 1 | `now-time-info` | 确定"当前年月日"，所有期间/剩余天数计算的基准 | 全部 |
| 2 | `order-list` | 拉取近期到店/外送历史订单，消费财报的**数据源** | 财报 |
| 3 | `query-order` | 补单个订单的明细（品类、金额构成），算品类结构 | 财报 |
| 4 | `query-meals` | 取门店可售菜单，SKU 编码校验 | 下单 |
| 5 | `query-meal-detail` | 取套餐组成/可替换项，用于品类归类与方案组装 | 下单 |
| 6 | `calculate-price` | **唯一可信的价格来源**，券后实付价试算 | 全部 |
| 7 | `query-nearby-stores` | 定位用户常去/附近门店 | 券配置 / 下单 |
| 8 | `query-my-coupons` | 取账户**全部**优惠券（卡包静态视图） | 券配置 |
| 9 | `query-store-coupons` | **门店级券可用性校验**（动态视图）—— 关键步骤 | 券配置 |
| 10 | `available-coupons` | 取当前可**领取**的券列表 | 券配置 |
| 11 | `query-my-account` | 取积分余额、累计、冻结、即将过期积分 | 积分 |
| 12 | `mall-points-products` | 取麦麦商城可兑换商品列表 | 积分 |
| 13 | `mall-product-detail` | 取商品详情（所需积分、有效期、说明） | 积分 |
| 14 | `campaign-calendar` | 取当月活动日历（进行中/未来/往期） | 择时 |
| 15 | `list-nutrition-foods` | 取餐品营养数据（用户关注热量时附加展示） | 附加 |
| 16 | `mall-order-list` | 取积分商城订单（与 `order-list` 口径不同，需分开看） | 财报 |

### 2.2 写操作 Tool（3 个，全部需用户显式确认）

| # | Tool | 副作用 | McCFO 的处理 |
|---:|---|---|---|
| 17 | `create-order` | 生成订单、产生消费 | 展示明细 → 用户确认 → 才调用 |
| 18 | `auto-bind-coupons` | 批量领取券、改变账户状态 | 先展示"可领 N 张、合计可省 ¥X"，用户同意才领 |
| 19 | `mall-create-order` | 扣除积分/库存 | 先展示兑换方案与 ROI，用户确认才兑换 |

> 此外，`query-party-*` / `party-order-create`（主题活动）、`draw-lottery`（抽奖）、`cancel-order`（取消订单）、`delivery-*`（地址）等 Tool 未纳入核心链路——McCFO 的定位是"个人消费与权益分析"，主题活动与抽奖属于独立场景，避免功能发散。

---

## 二·五、代码层的真实调用链路

McCFO 不只靠 Agent 在对话里逐个调工具。`scripts/mccfo_fetch.py` 把上面 19 个 Tool 里的
**只读全链路**固化成了可复现的代码（`scripts/mcd_mcp.py` 提供零依赖的 MCP 传输层）：

```
mccfo_fetch.py
  ├── now-time-info              锁定"今天"，作为所有期间/剩余天数的基准
  ├── order-list                 拿近期订单（只有实付额）
  ├── query-order        × N     逐单补 原价 / 优惠 / 券 / 就餐方式
  ├── query-my-coupons           账户全部券（静态卡包视图）
  ├── query-store-coupons        门店级可用性校验（动态视图）
  ├── query-my-account           积分余额 / 累计 / 将过期
  ├── mall-points-products       商城可兑换商品
  └── mall-product-detail × N    补每个候选的真实现金价（skuList[].extTradePrice）
        │
        ▼
  out/normalized/{orders,coupons,points,meta}.json
        │
        ▼  mccfo_report.py
  out/mccfo-report.md  +  out/mccfo-report.html
```

**实测一次完整取数 = 23 次 MCP 调用**（10 单 + 7 个在售兑换品），远低于 600 次/分钟的限流。

代码层对 MCP 的忠实度做了三件关键处理，都是踩过坑才加的：

| 真实情况 | 代码里的处理 |
|---|---|
| `order-list` 只有 `realTotalAmount`，没有原价/优惠 | 逐单调 `query-order` 补 `totalAmount` / `totalDiscountAmount`；都拿不到时才用「原价 − 实付」兜底 |
| `query-my-coupons` 的 `discountValue` 是**券后价**不是优惠额 | 面额优先从券名解析「满X减Y」；解析不到才用 `discountValue` 并标 `discountEstimated=true`，报告显示 **（估）** |
| `mall-points-products` 的 `price` 常年为 `"0"` | 现金价优先取 `mall-product-detail` 的 `skuList[].extTradePrice`，退回从 `spuName` 正则解析（`21.9元…`），并按 `downTime` 过滤掉已下架商品以免 610403 |

## 三、调用流程

### 3.1 模式 A · 月度消费财报

```
用户："我这个月在麦当劳花了多少"
        │
        ▼
[1] now-time-info ─────────► 确定当前日期，锁定统计期间（如 2026-10-01 ~ 2026-10-10）
        │
        ▼
[2] order-list ────────────► 拉取期间内历史订单（到店 + 外送）
        │
        ├──► [3] query-order（按需逐单）──► 补品类明细、金额构成
        │
        └──► [4] mall-order-list（可选）──► 若用户要含积分商城消费
        │
        ▼
[5] 归一化为 orders JSON ──► 交给 scripts/mccfo_report.py
        │
        ▼
输出：总支出 / 客单价 / 折扣率 / 品类结构 / 时段分布 / 环比 / 榜单 / 三条洞察
```

### 3.2 模式 B · 券资产配置（**两步校验是本模式的关键**）

```
用户："我的券怎么用最划算"
        │
        ▼
[1] now-time-info ─────────► 算每张券的剩余天数
        │
        ▼
[2] query-my-coupons ──────► 账户全部券（静态：面额/门槛/有效期/适用范围）
        │
        ▼
[3] query-nearby-stores ───► 定位门店
        │
        ▼
[4] query-store-coupons ───► ★ 门店级可用性校验（动态：这张券在这家店能不能用）
        │
        ▼
[5] 可选：available-coupons ► 还能领什么；用户同意后 auto-bind-coupons 一键领
        │
        ▼
[6] 按 Score = 面额 × Yield × (1+紧迫) × 匹配 × 门店可用 排序
        │
        ▼
输出：券资产配置表 + 临期预警 + "先用哪张、为什么"
```

> **为什么必须两步校验**：`query-my-coupons` 只能回答"账上有没有"，只有 `query-store-coupons` 能回答"这家店现在能不能用"。只看前者会给出错误的可用性结论——这是实际开发中最容易翻车的地方，也是 McCFO 区别于"券列表罗列"的核心设计。

### 3.3 模式 C · 积分资产 ROI

```
用户："积分兑换什么最值"
        │
        ▼
[1] query-my-account ──────► 可用积分 / 即将过期积分 → 算过期压力
        │
        ▼
[2] mall-points-products ──► 可兑换商品列表
        │
        ├──► [3] mall-product-detail（逐个候选）──► 补所需积分、现金参考价
        │
        └──► [4] query-lottery-info（可选）──────► 抽奖期望价值参考
        │
        ▼
[5] 算 VPP = 现金参考价 ÷ 消耗积分 → 按 ROI 排序
        │
        ▼
输出：兑换 ROI 排行榜 + "优先用掉将过期的 X 分"
```

### 3.4 模式 D · 活动择时

```
用户："这周有什么活动值得去"
        │
        ▼
[1] now-time-info ─────────► 定位"今天"在周历中的位置
        │
        ▼
[2] campaign-calendar ─────► 当月活动：进行中 / 未来 / 往期
        │
        └──► [3] query-party-*（可选）──► 主题活动（派对/品鉴会）城市→门店→日期→场次
        │
        ▼
输出：周历式择时建议（哪天是"低点"、适合买什么）
```

---

## 四、业务价值

McCFO 用 19 个 Tool 组合出的价值，不是"多调几个接口"，而是**把分散的原始数据翻译成决策**：

| 业务价值 | 依赖的 Tool | 具体产出 |
|---|---|---|
| **消费透明化** | `order-list` + `query-order` | 用户第一次说得清自己的钱花在哪：品类结构、时段习惯、客单价、环比趋势 |
| **省钱量化** | `calculate-price` 口径 + 订单金额 | 不只说"省了多少"，而是给出**折扣率**与**实付率**，让"这个月到底省得好不好"有可比口径 |
| **券决策自动化** | `query-my-coupons` + `query-store-coupons` | 从"十几张券挑一张"变成"按分数排序的优先级列表"，并规避"券在但店用不了"的翻车 |
| **积分资产保值** | `query-my-account` + `mall-points-products` + `mall-product-detail` | 把"积分能换啥"变成"**每积分值多少元**"的 ROI 排行，直接回答"该换哪个" |
| **权益不浪费** | 积分过期字段 + 券到期日 | 主动预警：还有 N 天到期的券、即将作废的积分，避免沉默损失 |
| **择时省钱** | `campaign-calendar` | 把"什么时候去吃更便宜"从运气变成日程 |
| **零 Token 泄露** | 渲染与取数分离 | MCP Token 只在 Agent 侧使用，代码仓库零凭据 |

**一句话总结业务价值**：麦当劳 App 给用户的是**数据**，McCFO 给用户的是一份**能直接照着做的行动清单**。

---

## 五、错误处理与降级策略

| 场景 | 表现 | McCFO 的处理 |
|---|---|---|
| Token 无效/过期 | `401` | 明确提示"Token 失效，请重新在 open.mcd.cn/mcp 申请"，不重试、不猜测 |
| 触发限流 | `429`（>600 次/分钟） | 降低调用频率、拉长间隔；组合试算候选控制在 8 个以内 |
| 工具返回空 | 空数组 | 如实汇报"当前无可用券/无历史订单"，**不用估算值填空** |
| 单个 Tool 失败 | 其他工具正常 | **跳过该模块继续**，最后统一说明"哪几项没取到、为什么" |
| 门店定位失败 | 无法确定门店 | 降级为"账户全量券视图"，并明确标注"未做门店级校验，可用性待确认" |

---

## 六、安全边界

1. **写操作零自动执行**：`create-order` / `auto-bind-coupons` / `mall-create-order` 三个写操作，必须先展示明细、等用户明确确认（"确认"/"下单"/"可以"）后才调用。
2. **金额单位不混用**：MCP 返回的金额字段已是「元」，直接用；积分字段**不做任何汇率换算**。
3. **价格必现算**：下单前的实付价一律以 `calculate-price` 的实时返回为准，不沿用旧数据。
4. **仓库零凭据**：本仓库所有配置均为环境变量占位符，不含真实 Token、手机号或个人订单数据。

---

## 七、数据归一化

`scripts/mccfo_report.py` 的输入结构与 MCP 返回字段的对应关系（由 `scripts/mccfo_fetch.py` 产出）：

| 脚本字段 | 来源 | 说明 |
|---|---|---|
| `orders[].orderId` | `order-list` / `query-order` | 订单号 |
| `orders[].date` | `query-order` | 下单时间，用于时段归类 |
| `orders[].store` | `query-order` | 门店，用于常去店聚合 |
| `orders[].channel` | `query-order` | 到店/麦乐送/得来速 |
| `orders[].items[].category` | `query-meal-detail` | 品类，归入 汉堡/小食/饮料/甜品/套餐 |
| `orders[].listAmount / discount / payAmount` | `query-order` | 券前 / 优惠 / 实付 |
| `coupons[].discount / threshold` | `query-my-coupons` | 面额 / 门槛 |
| `coupons[].expireDate` | `query-my-coupons` | 到期日 |
| `coupons[].storeAvailable` | **`query-store-coupons`** | 门店级可用性（关键校验位） |
| `points.available / expiring` | `query-my-account` | 可用 / 即将过期积分（字段是字符串，且可能是小数） |
| `coupons[].discountEstimated` | 券名解析失败标记 | `true` 表示面额是估算值（券后价），报告标 **（估）** |
| `coupons[].status` | `instructions.labels[].text` | 「未到使用时间」「到店」等券面状态 |
| `meta.toolsCalled / degradations` | 取数过程 | 本次调用了哪些工具、哪些项被降级跳过（写进报告第八节） |
| `points.products[].points / cashPrice` | `mall-points-products` + `mall-product-detail` | 消耗积分 / 现金参考价 |

> 门店列表（`query-nearby-stores`）与活动日历（`campaign-calendar`）由 Agent 直接呈现给用户，不进入渲染引擎。
