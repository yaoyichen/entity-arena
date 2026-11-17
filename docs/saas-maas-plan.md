# ER SaaS/MaaS 演进计划（v0.1）

> 定位：**代码仓库不交付给客户**。客户拿到的是服务与体验——部署 endpoint +
> θ 判定规则 + 评测报告 + 按其数据定制的托管演示页 + MCP 工具。训练底座是
> 自研检索后训练微调平台
> （console：https://agent-inf-retriever-train-service.openapi-inspire.inf.tech/ ，
> 工具面见 `.claude/skills/inf-retriever-finetune/references/api.md`）。
> 本仓库的角色：**体验层 / 接入层的参考实现 + 交付工具链**。
>
> 来源：2026-11-17 全量评审对齐（127 项测试全过、fresh clone 链路验证、
> 平台能力面核对）。客户侧输入输出承诺见 `docs/data-contract.md`（v0.1），
> 本计划是它的工程落地路线。**本文档是后续实施的执行蓝本**：每个工作项
> 带文件级落点与验收标准，按里程碑顺序执行。

## 0. 边界与不变量（先于一切）

- **仓库不出门**；客户可见物只有：API、报告、托管页、MCP 端点。
- **仓库硬约束延续到全部新代码**：零第三方依赖（Python ≥3.11 纯标准库）、
  一切随机走 seed 确定性可重建、失败不静默（校验/断言直接 raise 或带人话报错）、
  诚实标注（租户 demo 页 provenance / 状态条 / 统计条三处口径声明，
  基线列照旧声明"代理"）。
- **令牌绝不进**仓库 / 页面 / 任何交付物（现有三级解析约定不变：
  `--token` > `EMB_TOKEN` > `~/.config/entity-arena/emb_token`）。
- **平台网关两条实测约束写死在设计里**：无 CORS（浏览器直连不通，
  托管页打分必须同源网关转发）；TLS1.2 强制 + 重试（embed_client.py 已内置，
  别绕）。
- **θ 与判定规则是产品发布物的一部分**，不是模型的：每租户交付四元组
`(endpoint, model, θ, 序列化规则)`，与数据合同 §3 对齐；θ 一律
> link-F1 扫描得出并写进 provenance，永不手调。

## 1. 现状盘点（2026-11-17，均已验证）

| 层 | 资产 | 状态 |
|---|---|---|
| 训练底座 | 平台 MCP 全工具面：`upload_dataset` → `create_finetune_job` → `evaluate_model` → `deploy_model` → `list_resources` 取 `service_url`/`curl_command`；skill `inf-retriever-finetune` 即操作 runbook | ✅ 已打通并真实跑通一轮（`data_open/eval_reports/` 六份报告 + `embedding-febrl3-er-gemma` 服务） |
| MaaS API 雏形 | `scripts/play_live_server.py`（同源转发 + θ 同源 + 端口冲突人话）+ `src/entity_arena/embed_client.py`（TLS1.2/重试/余弦，三处共用） | ✅ 原型在；单模型、单令牌、localhost |
| 体验层 | demo 页数据驱动契约（换 `data/*.json` 页面零改动，键校验在 `build_demo.py:_load_open_cases`）；`build_open_cases.py` 的 θ 扫描 / 断言链 / 组件纯度 / 方向性指标 / 五分桶 / Arena·Explain 极值规则 | ⚠️ 核心统计 schema-light，入口 Febrl 专用（字段词典、`_BASE_W` 基线权重、story 文案） |
| 数据入口 | `docs/data-contract.md` §1–§2 定义完整（双标签格式、field-role 三分、intake 校验、自动硬负例） | ❌ **无代码**；`make_open_dataset.py` 列名写死 Febrl，共享逻辑在 `febrl.py` |
| AI-native 接入 | `scripts/er_mcp_server.py` 三工具；租户参数化已就绪（`ER_VILLAGE`/`ER_SCORES`/`ER_EMB_URL`/`ER_EMB_MODEL`/`ER_THETA` 环境变量） | ✅ 基本就绪，缺租户数据文件与字段对照泛化 |
| 容量 / SLA 锚点 | `data_open/bench_embeddings.json`：单条峰值 15.9 QPS（4 线程）、batch32 峰值 272 rps（8 线程）、16 并发触 429；结论"加 batch 不加并发" | ✅ 实测在库，待进 SLA 条款 |

## 2. 目标架构

```
客户侧（可见）                                 内部（仓库工具链）
────────────────────────────                ─────────────────────────────
托管体验页（租户 demo.html）                   操作员跑交付流水线：
  └ 同源 /api/score ──────────────┐           ① intake：客户 CSV/JSONL + 标签
MaaS API（/v1/er/score·embed·     │              + field-role YAML
  resolve，每租户 θ/序列化/鉴权）──┼─→ er-gateway（多租户）→ 平台部署的
MCP 工具（AI 客户端接入）─────────┘              /v1/embeddings 服务
                                              ② 平台循环（MCP skill 驱动）：
评测报告 + θ + 决定规则（交付物）                  upload → train → eval → deploy
                                              ③ 取样回填：embeddings → 成对矩阵
                                              ④ build_tenant_cases → 租户 demo
                                              ⑤ 交付四件套 + MCP env 配置
```

关键事实（已实测，设计不得违反）：平台网关无 CORS → 托管页打分必须走
er-gateway 同源转发（就是 play_live_server 模式上云）；GPU 抖动 ±6e-4 →
判定以 θ 两侧 ≥0.01 为稳判边界（现有规则沿用）。

## 3. 工作项（执行顺序即依赖顺序）

### W1 · 客户数据入口 `make_client_dataset.py`（第一优先）✅ 已完成（2026-11-17）

把数据合同 §1→§2 从文档变成代码：任意客户 schema 进 → 平台上传包出。

- **新建** `scripts/make_client_dataset.py`、`src/entity_arena/intake.py`
  （校验 + 切分 + 序列化 + 硬负例，供 W2 复用）、`tests/test_intake.py`。
- **输入**：记录文件（CSV/JSONL，`record_id` + 任意业务字段，缺失留空不垫）；
  标签二选一——格式 A（`entity_id` 列）或格式 B（`rid_a,rid_b,label` 对文件）；
  field-role 元数据（YAML：每字段 `embedding`/`unique-key`/`ignore` + 可选
  归一化说明）。
- **要点**：
  - intake 校验全清单照合同 §2.1：record_id 唯一、标签引用完整性、空文件/
    单实体守卫，每条失败带行号人话报告（失败不静默）；
  - 实体级 80/20 切分（seed 钉死，格式 B 按对切）；防泄漏三道锁沿用；
  - 序列化严格按声明字段序拼 `field: value | …`，`unique-key`/`ignore`
    剔除（与训练同口径——这个序列化器就是 W3 在线打分要用的同一份）；
  - 硬负例规则参数化：同"关键属性值"的异实体（关键属性 = field-role 里
    声明的 embedding 字段子集，默认全部），≤5/查询，不足种子随机补 ≥1；
  - 输出 BEIR 包（queries/corpus/qrels + neg），`upload_dataset` 一次过
    `format_validation`。
- **验收**：① 用 Febrl3 走 `make_client_dataset.py`（field-role 声明 Febrl
  列）产出的包与现管线 `make_open_dataset.py` 语义等价（qrels 集合一致，
  硬负例规则对齐后允许差异但需在测试中显式断言）；② 构造坏输入 fixture
  （重复 id / 悬空标签 / 单实体 / 空 unique-key 违规）逐条触发人话报错；
  ③ 双跑逐字节一致（确定性测试）。

### W2 · 租户体验页 `build_tenant_cases.py` ✅ 已完成（2026-11-17）

让"客户拿到自己数据的 demo 页"成为一条命令（合同 §3.3 的承诺）。

- **改动** `scripts/build_open_cases.py`：抽出 schema-agnostic 核心（θ 扫描、
  断言链、连通分量纯度、方向性指标、五分桶、play 矩阵量化入页）到
  `src/entity_arena/cases_core.py`；**新建** `scripts/build_tenant_cases.py`
  作为租户入口 + `tests/test_tenant_cases.py`。
- **要点**：
  - 输入：intake 产物（切分 + 序列化后记录）+ 成对真实分数矩阵
    （取样脚本泛化自 `fetch_village_embeddings.py`）+ field-role 元数据；
  - Febrl 专属物参数化：字段词典（labels）从 field-role 生成；基线代理权重
    表改为"每字段等权字面相似度"默认 + YAML 可覆盖；story 文案模板化，
    字段语义不足时诚实降级（空池置"—"，页面已支持）；
  - Arena F1–F6 / Explain R1–R6 极值规则按分布与真值定义的部分原样泛化
    （大家族=记录数最多实体、缺失=缺失字段最多等），依赖字段语义的
    （错字风暴）用序列化文本模糊度近似，规则全文照旧进 provenance；
  - 产出与 `open_cases.json` 完全同 shape（键清单以
    `build_demo.py:_load_open_cases` 的校验列表为准）→ **页面零改动点亮**。
- **验收**：① Febrl3 案例村经租户路径重建，指标与现有 `open_cases.json`
  逐字段一致（回归锚）；② 构造最小租户 fixture（两字段、20 实体）跑通
  全链 build_demo self-check；③ 每租户 provenance 含 θ 扫描结果、基线
  代理口径、missed_rule / metrics_rule / arena_rule 全文。

### W3 · 多租户打分网关 er-gateway（MaaS API 原型）✅ 已完成（2026-11-17）

`play_live_server.py` 模式上云 + 补上在线序列化缺口。

- **新建** `scripts/er_gateway.py`（或 `src/entity_arena/gateway.py` +
  入口脚本）+ `tests/test_er_gateway.py`；复用 `embed_client.py`，不另写客户端。
- **要点**：
  - 租户注册表（仓库外配置文件）：`tenant → {endpoint, model, θ,
    field-role 序列化规则, 鉴权键}`；θ 默认从该租户 open_cases 产物读，
    与页面/报告同源不漂移；
  - **在线序列化**（当前最大缺口）：客户 schema 下自由输入不能"输入即文档"，
    必须按该租户 embedding 字段序拼 `field: value | …` 与训练同口径；
    试玩台「直接输入」页签对租户改为结构化字段表单（开放决策 §6.2）；
  - 端点：`POST /v1/er/score`（一对）、`POST /v1/er/embed`（批量向量化）、
    `POST /v1/er/resolve`（一批记录 → 断言链归并组件），判定含 θ 两侧
    ≥0.01 稳判标记；同源静态托管各租户 demo.html（/api/score 兼容旧路径）；
  - 鉴权雏形：每租户静态键即可，v1 不做账号体系；
  - 容量护栏按 bench 锚点：并发上限 + batch 引导（429 人话重试提示）。
- **验收**：① 单进程本地起双租户 fixture，两租户 θ/字段序互不串；
  ② 在线打分与该租户入库矩阵逐对同值（±6e-4 容差，判定一致）——沿用
  `test_play_live_server.py` 的 stub 注入模式，不碰真网络；③ 无令牌/
  未知租户/超并发三条错误路径全部人话返回。

### W4 · 租户 MCP 交付打包（小）✅ 已完成（2026-11-17）

- er_mcp_server.py 参数化已就绪，缺的是把租户四元组变成一份"开箱 env +
  启动命令"交付物（连接视图文案模板化），以及 `er_score_pair` 字段对照
  表按 field-role 泛化（现在是 Febrl 字段名）。
- **验收**：租户 fixture 下 `er_score_pair(ridA, ridB)` 离线矩阵判定 ==
  页面试玩台判定 == er-gateway 在线判定（三方一致测试）。**已达成**，
  见 §3.4。

### W5 · 内部便利项（低优先，顺手做）✅ 已完成（2026-11-17）

- fresh clone 一条命令 bootstrap：`ensure_page` 检测 `data/` 缺失自动跑
  三步确定性重建（~38s）或人话提示；README 测试数 126→127 校正。
  客户不可见，只服务内部同事与交付操作员。**已达成**，见 §3.5。

## 3.1 实测记录（W1，2026-11-17）

- `tests/test_intake.py` 39 项全绿；全仓 166 项 OK（原 127 + 39）。
- **Febrl3 回归锚通过**：泛化路径与 `make_open_dataset.py` 的
  train/test qrels **逐字节一致**（切分算法单一事实来源成立）；
  Febrl3 经泛化入口重建出与数据合同 §前言完全一致的试点规模
  （train 1600 实体 / 10478 正例对 / test 400 实体 / 2598 对）。
- 硬负例泛化规则实测：19960 条挖掘、0 条兜底（Febrl3 上覆盖充分）；
  不变量（异实体 + 共享 ≥1 个 neg_key 字段规范化非空值）由测试锁定，
  与旧复合键规则（同姓/同 suburb+postcode/同生日）的差异为泛化的已知选择。
- `--zip` 确定性打包实测：双跑逐字节一致、包内五件套（report 不进包），
  可直接 `upload_dataset`。
- 实现细节（与计划的偏差，均有因）：fieldmap 落地为 CSV 表 + JSON 数组
  （零依赖不引 YAML 解析器，合同 §1.3 本就允许 "table"）；格式 B 的
  label=0 对直接用作金标负例（优于挖掘），不足时随机非正例兜底。

## 3.2 实测记录（W2，2026-11-17）

- `tests/test_tenant_cases.py` 26 项全绿；全仓 192 项 OK（W1 后 166 + 26）。
- **回归锚（字节级）双保险**：
  - 锚 A：`build_open_cases.py` 瘦身为 Febrl 适配层（统计全部走新核心
    `src/entity_arena/cases_core.py`，叙事经 `name_of`/`stub_of`/
    `narrate_*` 注入——核心只算数不写文案），输出与重构前
    `data/open_cases.json` **逐字节一致**，抽核心零漂移；
  - 锚 B：Febrl3 案例村导出 CSV + fieldmap（带 Febrl 字段标签与基线权重
    列），经 `build_tenant_cases.py` 租户路径重建，θ/断言链/组件/comp_of/
    top3/play 矩阵/网络三态/基线块/指标**逐字段一致**；文案豁免按设计
    （intake 序列化与 Febrl 序列化标签口径不同：比 fields/entity，不比 text）。
- **最小租户 fixture 全链**：22 记录 / 13 实体 / 2 字段（name+city）、
  model=client-er-pilot θ=0.9650；truth=9 asserted=8 tp=8 false=0 missed=1、
  components=14（全纯）；基线代理 θ=0.6944 false=5 missed=1；影子对 51
  （`--shadow-max-bucket` 上限生效有专测）；Arena 5 查询、Explain
  R1–R4+R6（R5 池空诚实缺席）；`--demo-dir` 一条命令组装数据目录后
  `build_demo.py` self-check 通过，零外链、零残留占位符。
- **页面租户化（截图驱动追加项，本轮主要返工）**：`open_cases.json` 新增
  可选键 `page_labels`（i18n 覆盖——页面 `t()` 先查它再落 I18N 默认）与
  `play_fields`（试玩台字段表键，字段名即标签）；Febrl 主构建不带两键，
  主页面渲染零变化（锚 A 保护）。租户构建注入 **25 键**，把页面里所有
  Febrl 硬编码换成按 payload 实算的租户文案——包括首页数字带影子对数
  （原硬编码 1,223）、微调态误连/漏连（原硬编码 0/0）、阶段 0 chip 与
  状态栏的 "· Febrl3" 后缀（page.py 原是 JS 拼死串，已改为
  `home.step0.chip.open`/`stat.rec.open` 占位符键）、头部徽标 `badge.open`、
  轨道卡描述（原宣称"公开基准"）、试玩台自由输入占位符（原是 Febrl
  chelsea 记录，现=该租户首条记录的真实序列化，顺带教会输入格式）与
  sidecar 缺席提示（原提 "village rid"）。双主题 × home/network/play +
  自由输入页签共 7 张截图逐项核验：Febrl3 仅存于开源基准板块（如实——
  那一板块本来测的就是 Febrl3）；数字 22/14/13/51/231 全部与构建统计
  一致；θ 与判定口径未动。
- fieldmap 扩两列：`baseline_weight`（缺省等权、相对值）与 `label`
  （`"en|zh"` 双语字段标签，缺省=字段名）——锚 B 字段行完全一致的前提。
- 与计划的偏差（有因）：基线权重未走 YAML（fieldmap 列即可，零依赖，
  与 W1 同一决策）；`conn.*` 视图文案与 `play.free.cmd` 启动命令的租户化
  推迟 W4/W3（指向我们自己的 MCP/sidecar，属交付打包范畴，见 W3/W4 条目）。

## 3.3 实测记录（W3，2026-11-17）

- `tests/test_er_gateway.py` 25 项全绿；全仓 217 项 OK（W2 后 192 + 25）。
- **落点**：`src/entity_arena/gateway.py`（逻辑层，`embed_fn` 可注入——沿用
  test_play_live_server 的 stub 模式，测试不碰真网络）+ `scripts/er_gateway.py`
  （HTTP 入口，ThreadingHTTPServer 单进程三合一：各租户 demo 页同源静态托管
  `/t/<name>/` + MaaS API `/v1/er/score|embed|resolve` + 落地页租户清单表）。
- **θ 永不手调（比计划更严）**：注册表里没有 theta 键——θ 与模型名只认
  build_tenant_cases 产物；注册表 model 与产物 model 不一致直接启动报错
  （防半新半旧配置静默漂移）。注册表在仓库外（默认
  `~/.config/entity-arena/er_tenants.json`），逐租户校验、全量收集后人话报错。
- **在线序列化与训练同一份**：结构化输入（`{字段: 值}`）走
  `intake.serialize_record`（fieldmap 声明序，就是训练/取样用过的那个函数，
  不是第二份实现）；未知字段 400 列出允许字段，值必须标量。判定回报
  `score/theta/model/mode(structured|raw)/verdict/stable(|score−θ|≥0.01)/
  margin/dim`——与页面 / MCP / Explain 卡同口径。
- **容量护栏**：对上游在途 embed 调用加非阻塞信号量（默认 8；bench 锚点
  16 并发触平台 429），超限 429 人话重试提示；resolve 上限 256 条/批并回报
  borderline 对清单（|score−θ|<0.01，≤50 条）供人工复核；embed 按 32/批
  （与 fetch_village_embeddings 同批量口径），维度不一致 502。令牌三级解析
  与 play_live_server 同一套（importlib 复用 `load_token`，不抄一份），
  不进注册表。
- **验收①（双租户隔离）**：单进程双租户 fixture，alpha（name+city，
  θ=0.9650）与 beta（字段集与 θ 均不同）互不串：同一余弦 0.955 探针
  alpha refuse / beta link；alpha 收 beta 的字段 → 400 列出自己的字段表。
- **验收②（在线=入库）**：FakeEmbed 注入 stub，在线分数与租户 cases 产物
  入库分数**逐对相等、判定一致**（选文本可区分对；精确孪生对序列化相同，
  任何 embedder 都只会给 1.0，不在可证范围——测试注释写明）。33 条输入
  自动分批 [32,1]。
- **验收③（三条错误路径人话）**：无令牌 503（带三级解析指引）/ 未知租户
  404（列出可用租户）/ 超并发 429（bench 锚点 + 重试或改批量建议）；另有
  401·403 键错、400 坏 JSON 或缺字段、未配 page 404、page 读不了 503。
- **试玩台结构化表单（W2 推迟到本项的页面部分）**：`play_fields` 在场时
  「直接输入」页签渲染 字段×A/B 结构化表单（缺席回落原 textarea，Febrl
  主页零变化——锚 A 保护），提交 `{a:{字段:值}, b:{…}}` 走网关结构化路径；
  展示序列化 `plSer()` 与 intake 同格式；`?fa=/?fb=` 深链严格解析（认不出
  的段留空表单，不猜格式）。page_labels 新增/改写 4 键（`play.free.sub`
  改为"逐字段填写…序列化按本数据集的字段序，与训练完全同口径"、
  `play.free.ph`=该租户首条记录真实序列化、`play.free.cmd`=er_gateway 启动
  命令、`play.free.err2`=挑村页签离线照常），W3 末共 26 键（W4 起 40 键，
  见 §3.4）。
- **端到端 dogfood**：`.tmp` 下 stub trigram embeddings 服务（8899，sha256
  桶向量确定性、忽略鉴权、只落 .tmp 不入库——诚实声明它不是微调模型）+
  er_gateway（8790）+ 租户页深链 → 结构化序列化 → 网关判定全链 curl 验证；
  双主题 ×（LINK 1.0000 / REFUSED 0.9463）共 3 张截图逐项核验：表单
  name/city × A/B 两行两列、结果卡分数 + 判定 chip + θ 刻度、页脚租户品牌
  （Pilot Co records），零 Febrl 残留。**截图口径备忘**：经网关的深链自动
  打分 headless 预算要给到 20000（比直连 sidecar 多一跳网络往返，9000 会
  把入场渐隐拍在半途——已写进 AGENTS.md）。
- 与计划的偏差（有因）：注册表 JSON 不用 YAML（零依赖，与 W1/W2 同一
  决策）；鉴权为每租户静态 api_key + Bearer（v1 无账号体系，按计划）；
  Febrl 主页 `/api/score` 兼容路径保留（default_tenant + raw 文本模式），
  er_gateway 与 play_live_server 不冲突（前者管租户托管页与 MaaS API，
  后者仍是 Febrl 主演示日常入口）。

## 3.4 实测记录（W4，2026-11-17）

- **交付包七件套**：`build_tenant_cases.py --mcp-dir` 一条命令产出
  `er_mcp_server.py` + `embed_client.py`（均为仓库文件逐字节拷贝，测试
  锁定防漂移）+ `records.json`（village 形状，含影子对）+ `scores.json`
  （全量对 canonical）+ `er_meta.json`（θ/模型/字段表/示例 rid 与 cases
  产物同源——θ 只认构建产物，永不手调）+ `er_mcp.env`（绝对路径 export，
  令牌只以注释指引出现）+ `START.md`（`claude mcp add` 一行命令 + .mcp.json
  配置 + 双模式说明）。令牌绝不进交付物由测试逐文件正则锁定。
- **er_mcp_server 租户口径（ER_META）**：标签/真值称谓/字段对照表/θ 来源
  全按 meta 泛化，Febrl 默认路径（无 ER_META）逐字节不变——由
  `tests/test_mcp_server.py` 既有 12 项全程绿锁定。交付包拷贝双路径 import
  （仓库 `src/entity_arena/embed_client` → 同目录 `embed_client.py` 兜底），
  无仓库也能跑。
- **页面 connect 视图模板化**：`conn.install.cmd`/`conn.install.cfg` 两个
  新 i18n 键 + 14 个 conn.* 覆盖键（page_labels 注入交付包绝对路径、租户
  示例问题、231 对实算数字、θ={TH} 运行期替换）；容量实测卡两键同步去
  案例村措辞（"model service"/"pure-stdlib harness"，不提 228 条村语料
  与仓库脚本路径——bench 是平台侧共享数据，数字不动只改称谓）。代码块
  CSS 改 pre-wrap：绝对路径长命令换行可读，不再横向截断。
- **三方一致验收**：`tests/test_mcp_delivery.py` 15 项全绿——MCP 离线矩阵
  判定 == 页面试玩台判定（links 集合）== er-gateway 在线判定（成对工艺
  向量 stub，在线分数==入库矩阵逐对；精确孪生对豁免与 W3 同口径）；含
  s=θ 边界对（判定 link、stable=false、margin=0）。
- **修掉一个静默错位 bug**：`--demo-dir` 相对路径 + 子进程 cwd=仓库根会把
  build_demo 写到仓库根、读仓库 Febrl 数据，租户页停在旧构建（截图首验
  抓到：connect 视图全是 Febrl 文案）。修复 = demo_dir/mcp_dir 一律
  先 resolve 成绝对路径 + 构建后 mtime 守卫（demo.html 必须比刚写入的
  数据新，否则 SystemExit 人话报错）。
- 双主题 connect 截图（1440×2400 全页）逐项核验：注册命令指向 mcp_bundle
  绝对路径、示例引用租户数据（alice smith / C-1001×C-1002 / Pilot
  labels）、离线卡"dataset rid pairs · 231 pairs"、诚实段 θ=0.9650 +
  START.md、零 Febrl/village/docs 残留。全仓 232 项测试 OK（217 + 15）。

## 3.5 实测记录（W5，2026-11-17）

- **fresh clone 引导**：`play_live_server.py` 新增 `ensure_data`——`data/`
  是 gitignore 的生成物，缺失时按序自动跑三步确定性重建（make_mock_data →
  update_open_benchmark → build_open_cases，seed 42）。每步只在「产物缺失
  且已入库输入齐全」时执行：`village_scores.json` 缺席的平台故障降级路径
  不硬跑，交给 build_demo 原有人话提示（页面只出合成轨）。
- **验收（模拟 fresh clone）**：把 `data/` + `build/` 挪走后一条
  `ensure_page` 走完全链——36.2s，9 个 data/*.json 与重建前**逐字节一致**
  （确定性硬约束成立），demo.html 仅 `meta.generated_at` 构建时间戳不同
  （既有设计：页面何时构建的诚实溯源，非数据漂移）。
- **踩坑记档**：DATA_STEPS 第三步的 inputs 少写一个逗号——`(path)` 只是
  分组表达式不是单元素元组，`all(p.exists() for p in inputs)` 直接炸
  TypeError。单测注入的 steps 形状对所以全绿、默认参数才暴露——fixture
  与生产同形状这条以后要警惕。
- README 测试数 126 → 233 校正；`ensure_data` 行为由
  `tests/test_play_live_server.py` 注入式步骤锁定（4 分支：输入缺席跳过 /
  只报告不动手 / 执行落地 / 产物在不再跑）。

## 4. 里程碑

| 阶段 | 内容 | 完成判据（DoD） |
|---|---|---|
| **v0 · 试点（现在即可跑）** | 操作员用 skill 人肉驱动平台循环，Febrl 管线即模板；交付四件套手工组装 | 已达成（Jeff 演示即此形态）；skill 即 runbook 是优点，**不急着自动化** |
| **v1 · 试点规模化** | W1 + W2 + W3 + W4 落地 | 拿到第一个真实客户 schema，从 CSV 到托管页 + API + MCP 全链 ≤ 1 天操作员工时；全部验收项绿 |
| **v2 · 服务化** | 编排服务化（包同一 MCP 工具面）、平台控制台 ER 场景页、unique-key 否决规则进 logic layer（合同 §4.4） | 客户自助上传 → 校验报告 → 一键训练；另行立项，不在本仓库范围 |

## 5. 风险与预案（全部有实测先例）

| 风险 | 预案（已写进仓库记忆，执行时直接查） |
|---|---|
| 平台 serving 整体故障（2026-11-08 发生过：新旧产物一律 ~4min FAILED） | 先查 Deployed Services 有没有现成可用服务，别急着重训；手动控制台建服务是已验证恢复路径 |
| FAILED 部署留僵尸 serving 名 | 重试换新名 |
| 网关掐 OpenSSL 3.x ClientHello | embed_client.py 已强制 TLS1.2+重试，新代码只许复用不许绕 |
| 网关无 CORS | 一切浏览器打分走同源网关（W3 架构已按此设计） |
| GPU 抖动 ±6e-4 翻判定 | θ 两侧 ≥0.01 才算稳判，全链路统一口径 |
| 客户数据边界 | 合同 §5：数据留在约定 tenancy、unique-key 可先哈希、向量只落 .tmp |

## 6. 开放决策点（实施前需拍板，不阻塞 W1）

1. **托管形态**：租户 demo 页放哪（域名/静态托管方案）、租户隔离方式
   （路径 `/{tenant}/` vs 子域）、访问控制（带签名链接 vs 静态键）。
2. **试玩台「直接输入」的租户形态**：结构化字段表单（序列化同口径，推荐）
   vs 保留自由文本（需向客户解释口径差异）。
3. **格式 B（pair 标签）** 是否进 v1（合同开放问题；影响 W1 工作量约 +20%）。
4. **租户基线列口径**：等权字面相似度默认 vs 每租户权重表（影响故事叙事
   强度 vs 配置成本）。
5. **SLA 档位**：bench 锚点（15.9 QPS 单条 / 272 rps batch32 / 429 档）
   已备，定价与承诺线待产品定。
6. **W3 网关部署位置**：与平台同域（吃现网关）vs 独立轻量部署（吃自主性）。

---
*执行约定：后续按 W1 → W2 → W3 → W4 顺序实施（W5 随手捎带）；每完成一个
工作项：跑全量测试 + 重建页面双主题截图过查 + 本文档勾选状态并追加
「实测记录」小节。本文档由 2026-11-17 评审产出，v0.1。
**状态（2026-11-17）：W1–W5 全部完成**（各节 ✅ + §3.1–§3.5 实测记录；
全仓 233 项测试绿）。v1 的 DoD（真实客户 schema 全链 ≤1 天）待第一个
真实客户实证——工具链已就位。*
