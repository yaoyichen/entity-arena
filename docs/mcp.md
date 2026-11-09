# entity-er MCP Server 使用文档

把「案例村实体归并打分器」交给任意 AI 客户端（Claude Code / Claude Desktop /
任何 MCP 客户端）：注册一次，之后用自然语言提问，AI 会自己调工具给出
**真实模型分数 + θ 判定 + 字段对照 + 真值核验**。

> 配套：`scripts/er_mcp_server.py`（服务本体，纯标准库）；仓库根 `.mcp.json`
> （Claude Code 项目级注册，已随仓库提交）；演示页「接入」视图
> （`build/demo.html?view=connect`，页内可一键复制全部命令）；
> `scripts/live_pair_score.py`（同一能力的命令行版，演示剧本见 `docs/live-demo.md`）。

---

## 30 秒上手

### Claude Code（推荐 · 零手改）

仓库已带项目级注册文件 `.mcp.json`：在本仓库目录启动 Claude Code，
首次使用会提示批准 `entity-er` 服务器，批准即可。等价的手动命令：

```bash
claude mcp add entity-er -- python3 scripts/er_mcp_server.py
```

然后直接对话：

> 村里有哪些叫 morrison 的记录？挑两条打分看看是不是同一个人。

### Claude Desktop / 任意 MCP 客户端（stdio）

客户端配置文件里加一段（路径换成本机绝对路径）：

```json
{
  "mcpServers": {
    "entity-er": {
      "command": "python3",
      "args": ["/absolute/path/to/entity-arena/scripts/er_mcp_server.py"]
    }
  }
}
```

在线模式（可选）在同一块里注入令牌，令牌不进仓库：

```json
      "env": { "EMB_TOKEN": "<平台 Bearer 令牌>" }
```

### 无 MCP 客户端？

同一能力有命令行版：`python3 scripts/live_pair_score.py --pair 476`
（用法见 `docs/live-demo.md` 场景 5/6）；更轻的入口是演示页**试玩台**视图
（`build/demo.html?view=play`）：页面内嵌同一份离线矩阵，任意挑两条村记录
（或名场面一键开打）直接看到真实分数与判定，零安装、断网可玩——
`?pair=ridA,ridB` 可直达指定对。

---

## 两种打分模式

| | 离线模式（默认，零配置） | 在线模式 |
|---|---|---|
| 触发条件 | 两侧参数都是案例村 rid | 任一侧是自由文本（或 rid × 文本混搭） |
| 分数来源 | `data_open/village_scores.json` 预取样真实矩阵（228 条记录两两全量 25,878 对） | 部署服务 `/v1/embeddings` 实时调用 |
| 需要什么 | 无（不联网、不要令牌） | 环境变量 `EMB_TOKEN` |
| 分数可信吗 | 部署模型输出确定，**离线 = 实时**（有测试锁定） | 真实服务输出（GPU 抖动 <0.005 不翻判定） |

试用建议：**从离线模式开始**——先 `er_find_records` 挑 rid，再
`er_score_pair` 打分，全程零配置；要体验「随口出题」再配 `EMB_TOKEN`。

## 环境变量

| 变量 | 必需 | 说明 |
|---|---|---|
| `EMB_TOKEN` | 在线模式必需 | 平台 Bearer 令牌（只在进程内使用，服务绝不回显；不进仓库） |
| `ER_EMB_URL` | 否 | 部署服务地址（默认为当前部署，换服务时覆盖） |
| `ER_EMB_MODEL` | 否 | 模型名（默认 `febrl3-er-gemma`） |
| `ER_VILLAGE` | 否 | 案例村数据路径（默认 `data_open/village.json`） |
| `ER_SCORES` | 否 | 预取样分数矩阵路径（默认 `data_open/village_scores.json`） |
| `ER_THETA` | 否 | 覆盖判定线 θ（默认取 `data/open_cases.json`，避免两处漂移；**别随手改**，见下） |

启动时 stderr 会打一行配置摘要（stdout 保持纯协议流）。

---

## 工具参考

### `er_score_pair(a, b)` — 打分与判定

两侧各接受：案例村 rid（如 `"rec-476-dup-0"`）或自由文本
（村风格行式 `name: … | DOB: … | SSN: …`），可混搭。

输出六段：分数 / θ 与判定 / 双方档案与真值核验 / 字段对照（`←` 标差异）/
打分模式 / 口径声明。rid × rid 的真实输出长这样：

```
cosine similarity = 0.9918
decision line θ    = 0.9208 (village link-F1 optimal)
VERDICT: LINKED — asserted same identity (+0.0710 above θ)

A: rec-1012-dup-0 (true entity 1012)
B: rec-1012-dup-1 (true entity 1012)
Febrl3 truth: SAME identity — assertion CORRECT ✓

fields (← marks a difference):
     name      chelsea morrison                   vs  chelsea morrison
     dob       1962-10-20                         vs  1962-10-20
  ← address   6 hansen circuit public bluding    vs  6 hansencircuit public building
  ← locality  golden bay 2608 nsw                vs  golden bay 2680 nsw
     ssn       6038747                            vs  6038747

score mode: pre-sampled real matrix (offline; deterministic — equals the live service output)
provenance: real fine-tuned-model outputs; θ picked by link-F1 scan on the 228-record
village (selection blind to scores); verdicts are model assertions checked against
Febrl3 truth (public benchmark, no real PII).
```

（这是同一人两份潦草写法：地址/城区全是 typo、门牌与邮编数字对调——
模型照样 0.9918 断言连对，SSN 铁证全同。）

**易错点**：传了实体 id（如 `"476"`）而不是 rid 会得到人话报错，
并列出该实体全部成员 rid，复制即可重试。

### `er_find_records(q?, limit?)` — 案例村检索

按姓名 / 生日 / SSN / 地址 / 子串搜，也可按 rid / 实体 id 精确查。
不给 `q` 时返回跨实体的确定性抽样（先挑 rid 用）。每条结果带
`→ score two of them: er_score_pair(a='<rid>', b='<rid>')` 指引。

### `er_about()` — 口径与状态

读数字之前先看它：模型名、案例村规模、θ 怎么选的、离线/在线两种模式
当前可用性、诚实声明。没有匹配结果、缺令牌等失败都返回
`isError` + 人话原因（协议层不崩），agent 能自己恢复。

---

## 示例对话（注册后直接说）

1. 「`er_about` 看看口径」 → 回卡片：模型 / 村规模 / θ / 两模式状态
2. 「搜 morrison，挑最像的一对打分」 → 检索 → 打分 → LINKED + 真值核验 ✓
3. 「这两个人是同一个吗：`rec-705-dup-1` 和 `rec-936-dup-0`」 → 0.911 REFUSED
   （全村最像的两个陌生人，距 θ −0.010——像 ≠ 是）
4. 「我自己编两条：name: spencer green … / name: spencxr royans …」
   （需 EMB_TOKEN）→ live 分数 + θ 判定，改一个字段看分数掉线

演示页「接入」视图（`?view=connect`）内嵌同款示例，一键复制。

## 口径与诚实声明（引用数字前必读）

- 每个分数都是**微调模型真实输出**：离线 = 预取样确定性矩阵（等于实时输出），
  在线 = 部署服务；没有任何人工分值。
- 判定（LINKED/REFUSED）是**模型断言**，不是保证；输出会自动对照 Febrl3
  真值标 CORRECT ✓ / WRONG ✗（公开基准，无真实 PII）。
- θ=0.9208 是本村 link-F1 最优，**选村对模型分数盲**（规则冻结在 provenance）；
  换数据集请重新扫描 θ，不要照搬。

## 故障排查

| 症状 | 原因与处置 |
|---|---|
| 自由文本打分报 `EMB_TOKEN` | 在线模式未配令牌：设环境变量，或改用 rid 对（离线照常） |
| live 调用超时/握手失败 | 平台网关掐 OpenSSL 3.x 默认 ClientHello；`embed_client.py` 已强制 TLS1.2 + 重试，别改回去 |
| 部署服务挂了 | 离线模式不受影响照常可用；服务侧先查 Deployed Services 有没有现成可用服务（别急重训，见 AGENTS.md 部署要点） |
| 分数与页面差 <0.005 | GPU 推理逐批浮点抖动，判定不变（边界案例按距 θ ≥0.01 选过） |
| 启动 stderr 显示 `offline matrix: MISSING` | `data_open/village_scores.json` 缺席：重新取样向量后覆盖该文件（流程见 AGENTS.md） |

## 实现说明

- **零第三方依赖**：stdio 上的 JSON-RPC 2.0（换行分隔），纯 Python ≥3.11
  标准库实现，与项目硬约束一致；无需安装任何包。
- 协议方法：`initialize`（回显客户端协议版本，支持 2024-11-05 起）/
  `notifications/initialized`（不回包）/ `tools/list` / `tools/call` / `ping`；
  错误码 -32700/-32601/-32602/-32603；工具级失败 → `isError` 结果而非协议错误。
- 数据懒加载（握手不读 1MB 文件）；θ 与 `data/open_cases.json` 同源，
  两处不漂移。
- 行为由 `tests/test_mcp_server.py` 锁定：握手与协议版本回显、工具清单顺序、
  离线分数必须等于入库矩阵值、报错引导文案、未知工具/方法的错误码。
