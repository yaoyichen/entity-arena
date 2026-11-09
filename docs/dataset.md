# 实测数据说明：Febrl3 与训练/评测切分

> 本文回答：实测主轨用的**真实数据是什么**、**训练/评测怎么切分**、演示页的案例村和切分是什么关系。
> 所有数字均为本仓库可复算的实测值（复现命令见 §7），与 `data/open_benchmark.json`
> 的 provenance 逐项对得上。

## 1. 数据本体：Febrl3 是什么

**Febrl**（Freely Extensible Biomedical Record Linkage）是实体归能领域的经典公开基准，
本仓库用 `recordlinkage` 官方发行版自带的 **dataset 3**（`data_open/febrl/febrl3.csv`，
随仓库提交）。它是程序合成的澳大利亚人口档案——**不是真实个人信息**，但注入了真实
ER 场景的四类噪声：**OCR 错、音译错、字段缺失、字段冲突**。

| 规模 | 数值 |
|---|---:|
| 记录总数 | 5 000（原始 `rec-<实体>-org` 2 000 条 + 副本 `rec-<实体>-dup-<k>` 3 000 条） |
| 实体总数（ground truth） | 2 000 |
| 多记录实体 | 1 165（即 835 个实体只有 1 条记录） |
| 每实体记录数分布 | 1 条×835，2 条×368，3 条×256，4 条×212，5 条×161，6 条×168 |

字段与缺失率（缺失本身是 ER 的常态，序列化时不补占位符）：

| 字段 | 含义 | 缺失率 |
|---|---|---:|
| `rec_id` | 记录号，`-org`/`-dup-k` 后缀即实体归属，**实体号就是 ground truth** | 0% |
| `given_name` / `surname` | 姓名 | 3.1% / 1.6% |
| `street_number` / `address_1` / `address_2` | 街道地址 | 4.9% / 3.1% / **13.9%** |
| `suburb` / `postcode` / `state` | 地区 | 1.7% / 0% / 1.7% |
| `date_of_birth` | 生日 | 3.1% |
| `soc_sec_id` | 社保号 | 0% |

一条记录变成模型输入的方式（`src/entity_arena/febrl.py: serialize`）：

```
rec-302-dup-0 → "name: jonas wilson | DOB: 1988-07-13 | address: 12 sturt street
                 | locality: darlinghurst 2010 nsw | SSN: 5305167"
```

## 2. 切分：实体级 80/20（seed 42）

切分单位是**实体，不是记录**——同一实体的原始记录和所有副本必须落在同一侧，
否则"训练时见过 rec-302-org、测试考 rec-302-dup-0"就是答案泄漏。

算法（`febrl.py: split_entities`，单一事实来源，训练包与案例村共用）：

```python
ents = sorted(所有实体号)          # 2000 个
random.Random(42).shuffle(ents)
test  = ents[:round(2000 × 0.2)]   # 前 400 个实体 → test
train = ents[400:]                 # 其余 1600 个实体 → train
```

| | train | test |
|---|---|---:|
| 实体 | 1 600 | 400 |
| 记录 | 3 992 | 1 008 |
| 同实体正对（qrels，score=1） | 10 478 | 2 598 |
| 有效查询（有 ≥1 正例的记录） | 3 315 | **850** |

> **口径说明**：test 的 1 008 条记录里只有 850 条能当查询——其余 158 条所属实体在
> test 内只有它自己一条记录，没有正例，它们只作为被检索语料里的**干扰项**存在。
> 平台评测报告里的 "query 数 850"（`data_open/eval_reports/`）就是这个口径。

## 3. 训练包格式（BEIR 风格上传包）

`scripts/make_open_dataset.py` 把 CSV 转成 inf-retriever 平台的上传格式
（`.tmp/febrl_er/`，不入库）：

| 文件 | 内容 |
|---|---|
| `queries.jsonl` / `corpus.jsonl` | **全部 5 000 条记录**同池——查询与文档是同一批文本（自检索：top-1 常是自身，对基线/微调是同口径的均匀代价，绝对值按"有效 k-1"解读） |
| `qrels/train.tsv` / `test.tsv` | §2 的正对，train 只供训练、test 只供评测 |
| `neg/neg.jsonl` | 训练查询的硬负例：**同姓、或同 (suburb,postcode)、或同生日的异实体记录**，每查询 ≤5 条 |

硬负例实测：3 992 个训练查询共配出 **10 998** 条负例，其中 **9 345** 条挖到真硬负例
（正是"高仿不同人"——微调要推开的就是这条边界，机理见 `docs/embedding-er.md`），
**1 653** 个查询无可挖对象、兜底为种子随机异实体记录（保证
`use_hard_negatives=true` 可用，且兜底随机也走同一 seed，字节级确定）。

## 4. 训练与评测在平台上怎么跑

流程（MCP `inf-retriever`，操作指引见 skill `inf-retriever-finetune`）：
上传训练包 → 对三个基座各建一个微调任务（`use_hard_negatives=true`）→
平台在**固定 test split（§2 的 400 实体 / 850 查询）**上用 `pytrec_eval` 算
NDCG@10 → 报告入库 `data_open/eval_reports/`（作溯源）→
`update_open_benchmark.py` 解析成 `data/open_benchmark.json` 供演示页指标板。

| 基座 | 微调产物 | NDCG@10 前→后 | win/tie/loss |
|---|---|---|---|
| `embeddinggemma-2` | `febrl3-er-gemma` | 0.980 → **1.000** | 80 / 770 / 0 |
| `inf-retriever-v1-1.5b-pro` | `febrl3-er-inf15` | 0.986 → 0.998 | 63 / 780 / 7 |
| `nemotron-3.5-embed-1b-bf16` | `febrl3-er-nemotron` | 0.999 → 1.000 | 6 / 844 / 0 |

这是**检索式**口径（NDCG）；演示页网络视图的**成对式**口径（cos ≥ θ）另由
`build_open_cases.py` 在案例村真值上扫 θ 最大化 F1 得到，两种口径的对应关系
见 `docs/embedding-er.md` §2。

## 5. 案例村：演示数据与切分的关系

演示页实测主轨的数据不是从 5 000 条里随手挑的，而是 `scripts/pick_open_village.py`
按**结构性规则**选的"案例村"（`data_open/village.json`）：

- **只在 test 实体里选**（228 条记录 / 78 实体，已验证 ⊂ §2 的 400 个 test 实体）；
- **选村对模型分数盲**：规则是"影子对密度"（异实体 + 同姓/同地区/同生日的高仿对，
  共 1 223 对影子对），预算 230，在看到任何模型分数**之前**定稿——规则全文进
  provenance，避免"挑案例之嫌"；
- 村内全量 **C(228,2) = 25 878 对**由已部署的 `febrl3-er-gemma`
  （`/v1/embeddings`，768 维）算出真实余弦分数，入库
  `data_open/village_scores.json`；
- `build_open_cases.py` 用村 + 分数重建 `data/open_cases.json`
  （网络三态 / Arena F1–F6 / 指标板 / Explain 卡 / 试玩台矩阵）。

## 6. 防泄漏三道锁

1. **实体级切分**：同实体所有记录同侧（§2），train 与 test 无实体交集；
2. **案例村 ⊂ test**：演示用记录全部来自 test 实体，模型训练时从未见过；
3. **盲选规则**：选村在看到任何分数之前定稿并写进 provenance。

## 7. 复现命令与文件地图

```bash
# 切分统计（含 train/test 实体数、正对数、硬负例数）
python3 scripts/make_open_dataset.py --csv data_open/febrl/febrl3.csv --out .tmp/febrl_er

# 案例村（确定性重选，应与 data_open/village.json 逐字节一致）
python3 scripts/pick_open_village.py

# 评测报告 → 指标板数据
python3 scripts/update_open_benchmark.py && python3 scripts/build_demo.py --open
```

| 文件 | 角色 |
|---|---|
| `data_open/febrl/febrl3.csv` | 唯一上游：Febrl3 原始数据（入库） |
| `src/entity_arena/febrl.py` | 解析/序列化/切分的单一事实来源（seed 42、实体级 80/20） |
| `.tmp/febrl_er/`（不入库） | BEIR 风格训练上传包（queries/corpus/qrels/neg） |
| `data_open/eval_reports/` | 平台评测报告（入库溯源），6 份 = 3 基座 × 前后 |
| `data/open_benchmark.json` | 报告解析结果 → 指标板第二板块 |
| `data_open/village.json` | 案例村记录 + 影子对 + 选村 provenance（test 侧） |
| `data_open/village_scores.json` | 案例村全量 25 878 对真实余弦分数（`febrl3-er-gemma`） |
| `data/open_cases.json` | 实测主轨全量页面数据（由村+分数重建） |
