# 实测记录 · 渲染链路

> 本文件记录 McCFO **渲染链路的真实运行结果**，命令可直接复现。
> 关于与麦当劳 MCP 服务端到端联调的说明，见文末「联调状态」。

## 一、环境

| 项目 | 值 |
|---|---|
| 操作系统 | Windows |
| Python | 3.13.12 |
| 第三方依赖 | **无**（仅标准库） |
| 执行日期 | 2026-10-10 |
| 输入数据 | `scripts/sample_data/*.json`（脱敏合成） |

## 二、执行的命令

```bash
python scripts/mccfo_report.py \
  --orders  scripts/sample_data/orders.sample.json \
  --coupons scripts/sample_data/coupons.sample.json \
  --points  scripts/sample_data/points.sample.json \
  --out     examples --format both --as-of 2026-10-10
```

## 三、实际输出

```
McCFO 财报已生成：
  · examples/mccfo-report.md
  · examples/mccfo-report.html

摘要：8 单，实付 ¥198.70，省 ¥72.80（26.8%）；券 7 张（临期 2），积分 1,280 分。
```

产物已随仓库附上：

- `examples/report-sample.md`（4.6 KB）
- `examples/report-sample.html`（10.2 KB，自包含单文件，无外部资源）

## 四、口径自检（复算验证）

脚本输出与手算一致，说明三个模型都按 `references/finance-model.md` 正确实现：

| 校验项 | 手算 | 脚本输出 | 结果 |
|---|---|---|---|
| 原价合计 | 22+44+27+53.5+30+34+31+30 = 271.50 | ¥271.50 | ✅ |
| 实付合计 | 18+29.7+21+40+25+22+23+20 = 198.70 | ¥198.70 | ✅ |
| 守恒校验 | 271.50 − 72.80 = 198.70 | 一致 | ✅ |
| 客单价 | 198.70 ÷ 8 = 24.8375 | ¥24.84 | ✅ |
| 折扣率 | 72.80 ÷ 271.50 = 26.81% | 26.8% | ✅ |
| 临期券数（N≤3） | 满30减10(N=2)、9.9冰美式(N=1) → 2 张 | 🔴 2 张 | ✅ |
| 积分过期压力 | 400 ÷ 1280 = 31.25% | 31.2%（触发高压力提示） | ✅ |
| 券优先级 Top1 | 9.1×1.0×(1+1/1)×1.0 = 18.20 | 18.200 | ✅ |

## 五、运行中发现并修复的问题

真实跑一遍的价值就在这里——以下三个问题都是跑起来才暴露的：

1. **券评分模型会让小额券"霸榜"**。初版 `Score = Yield × 紧迫 × 匹配` 里，一张 4.1 元的无门槛券（Yield=1.0）会压过 15 元的满减券。改为 `Score = 面额 × Yield × 紧迫 × 匹配` 后，既保留门槛效率的区分度，又让绝对价值参与排序。已同步更新 `references/finance-model.md` 1.4 节并附上手算示例。
2. **"临期券"与"首选用券"两条建议会互相打架**。当最高优先级券本身就是临期券时，报告会同时输出"用掉临期券 A"和"首选用券 B"两条矛盾建议。抽成统一的 `build_actions()` 后，改为按分数选出唯一 Top1，其余临期券作为补充提醒。
3. **无门槛券的建议文案会输出"凑到 ¥0.00 可省 ¥9.10"**。已按 `threshold == 0` 分支改为"无门槛，可直接省 ¥9.10"。

## 六、联调状态（如实说明）

- ✅ **已实测**：本地渲染链路（输入 → 财务口径 → Markdown/HTML 报表）完整跑通，含上表全部口径自检。
- ⏳ **待联调**：与 `https://mcp.mcd.cn` 的端到端联调需要真实的麦当劳 MCP Token。本仓库**不包含任何真实 Token**（遵守 `CONTEST_DECLARATION.md` 的信息安全声明），因此该步骤留给使用者按下面的清单自行完成。

**端到端联调清单**（配好 MCP 后，可直接把这段话发给你使用的 Agent）：

```
请按以下顺序调用麦当劳 MCP，并把结果整理成 McCFO 财报：
1. now-time-info                 → 确定当前年月日
2. order-list                    → 拉取最近的到店/外送历史订单
3. query-my-coupons              → 取账户全部优惠券
4. query-nearby-stores           → 定位我的常去门店
5. query-store-coupons           → 校验这些券在该门店是否可用
6. query-my-account              → 取积分余额与即将过期积分
7. mall-points-products          → 取可兑换商品列表
8. campaign-calendar             → 取当月活动日历
把 2/3/6 的结果按 scripts/sample_data/ 的 JSON 结构归一化，
交给 scripts/mccfo_report.py 渲染成财报。
注意：任何 create-order / mall-create-order / auto-bind-coupons / draw-lottery
都必须先给我看明细，等我确认后再执行。
```

联调完成后，可把 `order-list` / `query-my-coupons` / `query-my-account` 的返回按
`scripts/sample_data/` 的结构落盘，用同一条命令生成真实财报，替换 `examples/report-sample.*`。
