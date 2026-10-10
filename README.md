# 🍟 麦麦财务官 McCFO

> **别人管钱，我管麦门。** 把麦当劳 App 里散落的「消费账单 + 优惠券 + 积分」，用财务分析的框架重新组织成一份**看得懂、比得了、能决策**的个人财报。

基于 [麦当劳中国官方 MCP 服务](https://github.com/M-China/mcd-mcp-server) 开发的参赛 Skill —— **2026 麦当劳程序员创意开发大赛**（麦当劳 1024 程序员节）。

---

## 😩 它解决什么问题

你在麦当劳 App 里有三笔"资产"，但它们被拆在三个不同的入口里，你几乎从没把它们放在一起看过：

- **订单** → 你只知道"这个月好像点得有点多"，但说不清多在哪
- **优惠券** → 卡包里躺着十几张，你每次都靠感觉挑一张，用完才发现有更划算的
- **积分** → 攒了一千多分，直到有一天发现它过期了

**McCFO 的解法：把这三样东西当成一份资产负债表来经营。**

| 现实里的东西 | McCFO 怎么看 | 解决什么问题 |
|---|---|---|
| 优惠券 | **有价证券 / 期权**（有面额、有门槛、有到期日） | 券组合怎么配、先烧哪张 |
| 积分 | **现金等价物**（会贬值、会过期） | 兑换到哪个商品 ROI 最高 |
| 消费订单 | **现金流 / 支出** | 钱花在哪、省了多少 |
| 营销活动 | **市场行情** | 什么时候"买入"更便宜 |

---

## ✨ 四大能力

| 模式 | 你说 | 它做 |
|---|---|---|
| 📊 **月度消费财报** | "我这个月在麦当劳花了多少" | 拉历史订单 → 算出总支出/客单价/折扣率/品类结构/时段分布/环比 → 输出一份五段式财报 + 三条洞察 |
| 🎫 **券资产配置** | "我的券怎么用最划算" | 拉全部券 → **门店级校验可用性** → 按「面额 × 门槛效率 × 紧迫度 × 需求匹配」算优先级 → 输出券资产配置表 + 临期预警 |
| 💰 **积分资产 ROI** | "积分兑换什么最值" | 拉积分余额与商城 → 算「每积分价值」 → 输出兑换 ROI 排行榜 + 过期压力预警 |
| 📅 **活动择时** | "这周有什么活动值得去" | 拉活动日历 → 识别未来 7 天的"低点" → 输出周历式择时建议 |

外加一个**安全下单**通道：所有花钱操作（下单 / 兑换 / 领券 / 抽奖）都必须先展示明细、经你确认后才执行。

---

## 🚀 快速开始

### 第 1 步 · 申请麦当劳 MCP Token

前往 [open.mcd.cn/mcp](https://open.mcd.cn/mcp) → 手机号登录 → 右上角「控制台」→「激活」→ 复制 Token。

### 第 2 步 · 配置 MCP 连接器

以 [WorkBuddy](https://www.workbuddy.cn/) 为例：左侧边栏【专家·技能·连接器】→【连接器】→ 右上角【自定义连接器】→【配置 MCP】，填入：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

> ⚠️ 把 `YOUR_MCP_TOKEN` 替换成你的真实 Token，保存并启用。
> 完整脱敏配置见仓库根目录 [`mcp-config.example.json`](./mcp-config.example.json)。

其他客户端（Cursor / Cherry Studio / Trae / VSCode / Kiro）配置方式相同，见[官方接入指南](https://github.com/M-China/mcd-mcp-server)。

### 第 3 步 · 安装本 Skill

把 `SKILL.md` 与 `references/` 放进你的技能目录：

- **WorkBuddy**：`~/.workbuddy/skills/mccfo/`
- **Claude Code / 其他 Agent**：放入对应的 skills 目录即可（`SKILL.md` 是通用格式，不绑定特定框架）

### 第 4 步 · 开聊

```
你：帮我看下我这个月在麦当劳花了多少钱
McCFO：📊 McCFO 月度消费财报 · 2026-10-01 ~ 2026-10-10
       8 单 ｜ 总支出 ¥198.70 ｜ 客单价 ¥24.84 ｜ 用券省下 ¥72.80（折扣率 26.8%）
       ...
```

更多对话见 [`examples/demo.md`](./examples/demo.md)。

---

## 🧮 财务模型速览

完整公式与口径见 [`references/finance-model.md`](./references/finance-model.md)。三个核心指标：

**① 券的期望收益**（决定先用哪张券）

```
Score = 面额 D × 门槛效率 Yield × (1 + 紧迫度) × 需求匹配 × 门店可用
其中  Yield = D / max(门槛 T, D)，无门槛券 Yield = 1.0
```

> 只按面额排会忽略门槛（满 50 减 15 不一定比满 30 减 10 好）；只按收益率排会让 3 元无门槛券压过 15 元券。乘上 `D` 后两种错误都被修正——**大额、高效、紧急的券浮到最上面**。

**② 积分的兑换价值**（决定换什么）

```
每积分价值 VPP = 商品现金参考价 ÷ 消耗积分     （单位：元/分，越高越划算）
过期压力 Pressure = 即将过期积分 ÷ 总积分      （≥30% 触发"必须尽快兑换"）
```

**③ 消费效率**

```
客单价 AOV = 总支出 ÷ 订单数
折扣率 = 优惠合计 ÷ 原价合计      实付率 = 1 − 折扣率
```

---

## 🖥️ 可运行的源码：财报渲染引擎

`scripts/mccfo_report.py` 是 McCFO 的可运行部分——把 MCP 取到的订单/券/积分 JSON，渲染成 Markdown 或 HTML 财报。**仅依赖 Python 标准库，零第三方依赖。**

```bash
python scripts/mccfo_report.py \
  --orders  scripts/sample_data/orders.sample.json \
  --coupons scripts/sample_data/coupons.sample.json \
  --points  scripts/sample_data/points.sample.json \
  --out     out --format both --as-of 2026-10-10
```

产物：`out/mccfo-report.md` + `out/mccfo-report.html`（自包含单文件，直接双击即可看）。
仓库里已附上跑出来的样例：[`examples/report-sample.md`](./examples/report-sample.md) ｜ [`examples/report-sample.html`](./examples/report-sample.html)。

> 设计取舍：脚本**不直接调用 MCP**——取数由 Agent 完成，脚本只负责渲染。这样渲染逻辑可离线复现、可自检、也不需要在代码里保管 Token。

---

## 📁 仓库结构

```
mcd-mccfo/
├── SKILL.md                      # 技能主体：四大模式 + 安全边界 + 输出规范
├── README.md                     # 本文件
├── MCP_INTEGRATION.md            # 使用的 MCP Server / Tool / 调用流程 / 业务价值
├── CONTEST_DECLARATION.md        # 参赛声明（官方文件，内容未改动）
├── mcp-config.example.json       # 脱敏 MCP 配置示例（仅环境变量占位符）
├── workbuddy.md                  # WorkBuddy 开发对话记录（WorkBuddy 专项奖励）
├── references/
│   ├── mcd-tools.md              # 麦当劳 MCP 33 个工具速查 + 10 条实测踩坑
│   └── finance-model.md          # 券收益 / 积分 ROI / 消费分析 的公式与口径
├── scripts/
│   ├── mccfo_report.py           # 财报渲染引擎（零依赖，可运行）
│   └── sample_data/              # 脱敏样例数据
│       ├── orders.sample.json
│       ├── coupons.sample.json
│       └── points.sample.json
└── examples/
    ├── demo.md                   # 三段典型对话
    ├── live-run.md               # 渲染链路实测记录（含口径自检）
    ├── report-sample.md          # 样例财报（Markdown）
    └── report-sample.html        # 样例财报（HTML）
```

---

## 🎯 目标用户

- **想省钱又懒得算**的打工人 / 学生党 —— 不想研究券规则，只想知道"先烧哪张"
- **习惯看数据做决定的人** —— 想知道钱花在哪、这个月是不是点多了
- **有积分过期焦虑的人** —— 攒了一堆分，怕哪天悄无声息作废
- **早餐/午餐刚需的上班族** —— 高频、重复、最适合用报表优化
- **任何觉得"麦当劳怎么点最划算"是个值得认真研究的问题的人**

---

## 🤔 为什么不只是"又一个省钱助手"

排行榜上"省钱凑单"类项目已经有十几个。McCFO 的差异在**框架**，不在功能：

1. **它把权益金融化了**。券不是"优惠"，是**有行权价和到期日的期权**；积分不是"分"，是**会贬值的现金**。有了这个框架，"先用哪张券"从拍脑袋变成了一道可计算的题。
2. **它输出的是财报，不是清单**。清单告诉你"有什么"，财报告诉你"意味着什么、接下来做什么"。每份报告都以「接下来做什么」收尾，最多三条，直接可执行。
3. **它把"门店可用性"当成必检步骤**。券列表里躺着但这家店用不了的券，是最常见的翻车点——McCFO 强制用 `query-store-coupons` 做门店级校验。
4. **它有真实可运行的代码**，不只是提示词。渲染引擎可离线复现、口径可手算自检。
5. **它守住安全边界**。所有花钱、花积分、改变账户状态的操作，未经用户明确确认绝不执行。

---

## ⚠️ 声明

- 本项目为**麦当劳程序员节创意开发大赛参赛作品**，由参赛者独立开发，**非麦当劳官方产品**。
- 餐品信息、价格、供应状态及优惠券可用性，均以麦当劳官方渠道的实时返回为准。
- 下单、积分兑换、抽奖、领券等涉及资金/权益的操作，**均需用户显式确认后执行**。
- 本仓库**不包含任何真实 Token 或个人信息**，所有配置仅使用环境变量占位符。
- 项目输出仅供参考，不构成营养、医疗或投资建议。

---

## 🏆 参赛信息

- 赛事：[2026 麦当劳程序员创意开发大赛](https://github.com/M-China/mcd-developer-innovation-challenge)
- MCP 文档：[麦当劳中国 MCP Server](https://github.com/M-China/mcd-mcp-server)
- 开发工具：本项目使用 [WorkBuddy](https://www.workbuddy.cn/) 辅助开发，对话记录见 [`workbuddy.md`](./workbuddy.md)

如果 McCFO 帮你省下了一顿饭钱，欢迎点个 ⭐ **Star** 支持一下！
