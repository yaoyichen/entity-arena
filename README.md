# entity-arena · 实体归并演示台

给客户会议（Jeff，2026-11-10）的 Entity Resolution 能力演示：**合成数据 + 双模型
（baseline vs 微调）前后对比 + 单文件离线演示页**。对应 Jeff 邮件里 "The System
shall…" 的全部用例场景。

> 数据全部合成（seed 钉死、可复现），无任何真实个人信息；模型分数是**代理评分**
> （口径见下方[真实性边界](#真实性边界诚实声明)与 `docs/adr/0002`）。

## 60 秒跑起来

```bash
python3 scripts/make_mock_data.py            # ① 合成数据：19 实体 / 47 记录 / 5 数据源
python3 scripts/update_open_benchmark.py     # ② 开源基准轨：从已入库平台报告重建实测数字
python3 scripts/build_open_cases.py --scores # ③ 网络实测轨：从已入库真实分数重建 Febrl3 案例村
python3 scripts/build_demo.py --open         # ④ 构建 build/demo.html 并用浏览器打开
```

- 零第三方依赖（Python ≥3.11 纯标准库）；产物**单文件、零外链、断网可开**，可直接发给客户。
- 测试（69 项）：`PYTHONPATH=src python3 -m unittest discover -s tests`

## 四个视图（给客户讲的动线）

| 视图 | 讲什么 | 关键交互 |
|---|---|---|
| **Arena 对决台** | 6 个剧本查询，左右双栏 baseline vs 微调 | 切查询；判定线 θ=0.55，误连标红、真身标青、候选虚线 |
| **Network 关系网** | 双轨道：**合成剧本**（按来源孤岛 → 实体 halo 形变）与 **Febrl3 实测**（真实微调模型向量：影子纠缠簇 → 断言组件；红光环 = 混入异实体） | 「执行归并」动画；轨道切换；点节点看档案（实测轨含真实分数 top-3 与 θ 判定）；影子人/双胞胎肉眼可分 |
| **Explain 为什么** | 4 张案例卡逐字段拆解：✓ 全同 / ≈ 近似 / ✗ 冲突 / — 缺失 | 切案例；两个模型的判决 vs 地面真值 |
| **Scoreboard 记分牌** | 三大指标 + 分场景柱状图 + 指标口径脚注；下接**开源基准轨**（三个真实基模微调前后实测 × Febrl3） | — |

深链（截图/分享直达状态）：`?view=arena|network|explain|scoreboard`、
`?track=open`（网络视图直达 Febrl3 实测轨）、`?focus=R101`（自动开档案）、
`?resolve=1`（直达归并后）、`?lang=zh|en`。

## 指标（seed=42 实测，测试锁定）

| 指标 | baseline | 微调后 | 回答的问题 |
|---|---|---|---|
| recall@5 | 0.910 | **1.000** | top-5 里能不能找回同实体记录 |
| link_recall | 0.308 | **0.769** | 敢不敢断言（top-5 内分数 ≥ θ） |
| link_precision | 0.750 | **1.000** | 断言的链路有多少是对的 |
| false_link_rate | 0.600 | **0.000** | 相似实体查询中误连影子人的比例 |

分场景（link_recall，baseline → 微调）：属性冲突 0.15→0.69 · 属性缺失 0.00→1.00 ·
音译变体 0.42→0.83 · 相似实体 0.44→0.78（误连 0.60→0.00）。
**口径**：每条记录作查询、检索其余取 top-5；hit=同实体；断言=分数 ≥0.55。

## Jeff 用例 → 演示落点

| 邮件用例（The System shall…） | 场景标签 | 落点 |
|---|---|---|
| 识别名字变体/音译 | transliteration | Q2/P1（王秀英 ↔ Wong Sau Ying，0.34→0.88） |
| 识别重复记录 | 全场景 | Arena + recall/link 指标 |
| 跨数据源关联 | cross_source（5 源） | Network 归并动画 |
| 信息缺失/冲突下仍匹配 | missing_partial / conflicting | Q1（光秃入住单）/ Q3、P3（2月30日生日） |
| 区分相似实体（不误连） | similar_entity | Q4 两个张伟 / Q5、P4 双胞胎；false_link 0.60→0.00 |
| 关系/网络分析 | relations（亲属/同址/同行/共用号码） | Network 档案侧栏 |
| 支撑下游分析 | 指标口径 | Scoreboard 分场景 |

## 真实性边界（诚实声明）

1. **数据合成**：核心剧本人物（同名异人、双胞胎、音译、曾用名、DOB 冲突）手工设计，
   填充人物 seed 生成；`data/` 不进 git，一条命令确定性重建。
2. **分数是代理评分**：baseline = 字面相似度加权（跨文字近盲是其真实弱点的代理）；
   微调 = 字面信号 + 模拟"学到的"等价（有佐证才触发跨文字推理）。仅 4 对关键案例
   分数由剧本钉死（保证叙事走向），其余全部由公式涌现、指标对全量计算。
3. **故意留一处失手**：Q6 曾用名（Victor Chen ↔ 陈志强）微调也不过线——
   代理评分器的天花板，真微调模型的价值恰在此类案例上。
4. **换真模型**：`make_mock_data.py` 换成真跑分脚本、写同样的
   `results.json`/`metrics.json` 即可，**页面零改动**（数据契约见 `AGENTS.md`）。

## 开源基准轨道（进行中）

除合成剧本数据外，引入公开 ER 基准 **Febrl**（ANU / recordlinkage 发行版）作真实训练评测：

- 源数据 `data_open/febrl/febrl3.csv`（5000 条个人记录 / 2000 实体，随仓库提交可复现）；
- `scripts/make_open_dataset.py` 生成 inf-retriever 训练平台上传包（BEIR 风格：
  queries/corpus/qrels + 硬负例），实体级 80/20 划分防泄漏；
  硬负例 = 同姓 / 同 suburb+postcode / 同生日的**异实体**记录（正是"相似实体不该连"）；
- 训练与评测走 inf-retriever 平台（MCP `inf-retriever` + skill
  `.claude/skills/inf-retriever-finetune`），基模 × {inf-1.5, nvidia, gemma}
  训练前后对比，结果回填演示页；
- 口径注意：查询与文档同池，top-1 自命中对所有模型是同口径均匀代价，
  前后对比公平，绝对值按"有效 k−1"解读。

### 网络视图的实测轨道（Febrl3 案例村，已接入真实模型向量）

合成剧本轨之外，Network 视图另有**真实模型行为**轨道（数据缺席时轨道条隐藏、
页面只出合成轨）：

- `scripts/pick_open_village.py` 从 Febrl3 测试实体按结构性规则选"案例村"
  （102 条记录 / 影子对规则与训练硬负例同源；**选村对模型分数盲**，防挑案例）；
- 微调后模型经平台 pipeline 部署成 embeddings 服务，`fetch_village_embeddings.py`
  取全量真实向量，余弦成对分数入库 `data_open/village_scores.json`（溯源）；
- `build_open_cases.py` 做 θ 扫描（案例村上 link-F1 最大化）→ 断言链 →
  连通分量为"模型声称的实体"，对着 Febrl3 真值算误连/漏连/组件纯度；
- 页面上：before = 影子关系纠缠簇（同姓/同址/同生日的异实体看起来像一伙），
  after = 微调模型的断言组件光环（**红光环 = 混入异实体的混合组件**），
  点节点看真实分数 top-3 与 θ 判定；
- 诚实口径：基线模型平台无法部署（`deploy_model` 仅支持已训练模型），网络轨
  只展示微调模型的真实行为；训练前后对比见记分牌（真实评测数字）。
- **当前状态（2026-11-08 深夜已接入）**：平台 serving 当日曾整体故障
  （新/旧产物部署一律 ~4min FAILED，诊断实验留档），后经手动创建服务
  `embedding-febrl3-er-gemma` 恢复，真实向量已取样入库
  （`data_open/village_scores.json`）。**实测结果：微调 gemma 在案例村上
  147 真值对全部正确断言，零误连零漏连，33 组件全纯**——before 态的
  影子纠缠被完美拆解。注意：平台网关需 TLS1.2（脚本已内置强制与重试）。

## 下一步（通往真演示）

1. ~~用 retrieval-forge 造 ER 训练对~~ → 已引入 Febrl 开源基准（见上）；
2. ~~inf-retriever 平台微调三个基模~~ → 已完成，训练前后同集评测；
3. ~~真实指标回填演示页~~ → 记分牌实测板块 + 网络视图 Febrl3 实测轨道均已接入；
4. 拿到客户真实数据后按同一契约接入。

## 设计决策

- `docs/talk-track.md` **客户讲述文稿**：问题背景 → 思考 → 实现 → 分视图逐句口播 → Q&A 备忘
- `docs/live-demo.md` **现场演示剧本**：会前清单 + 8 场景动线（含 live 打分与客户出题环节）+ Q&A 弹药 + 风险预案
- `docs/adr/0001` 网络视图：预计算坐标 + 手写 SVG，不引 sigma/d3（确定性、零外链）
- `docs/adr/0002` 代理评分的诚实性：公式、剧本钉死范围、指标口径、换真模型接入点
