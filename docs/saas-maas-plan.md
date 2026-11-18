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

## 1. 现状盘点（2026-11-17 盘点，2026-11-18 复核更新）

| 层 | 资产 | 状态 |
|---|---|---|
| 训练底座 | 平台 MCP 全工具面：`upload_dataset` → `create_finetune_job` → `evaluate_model` → `deploy_model` → `list_resources` 取 `service_url`/`curl_command`；skill `inf-retriever-finetune` 即操作 runbook | ✅ 已打通并真实跑通一轮（`data_open/eval_reports/` 六份报告 + `embedding-febrl3-er-gemma` 服务） |
| MaaS API 雏形 | `scripts/play_live_server.py`（同源转发 + θ 同源 + 端口冲突人话）+ `src/entity_arena/embed_client.py`（TLS1.2/重试/余弦，三处共用） | ✅ 原型在；单模型、单令牌、localhost（多租户版见 W3 `er_gateway.py`） |
| 体验层 | demo 页数据驱动契约（换 `data/*.json` 页面零改动，键校验在 `build_demo.py:_load_open_cases`）；`build_open_cases.py` 的 θ 扫描 / 断言链 / 组件纯度 / 方向性指标 / 五分桶 / Arena·Explain 极值规则 | ✅（2026-11-18 复核）统计核心 W2 起抽为 schema 无关的 `cases_core.py`，Febrl 只是适配层；租户入口 `build_tenant_cases.py` 已实测（§3.2） |
| 数据入口 | `docs/data-contract.md` §1–§2 定义完整（双标签格式、field-role 三分、intake 校验、自动硬负例） | ✅（2026-11-18 复核）W1 已落地：`make_client_dataset.py` + `intake.py`（任意 schema，序列化与训练/页面同一份，§3.1） |
| AI-native 接入 | `scripts/er_mcp_server.py` 三工具；租户参数化已就绪（`ER_VILLAGE`/`ER_SCORES`/`ER_EMB_URL`/`ER_EMB_MODEL`/`ER_THETA` 环境变量） | ✅（2026-11-18 复核）W4 起租户模式就绪：`ER_META` 交付包标签/字段对照/θ 全按租户口径（§3.4） |
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
  三步确定性重建（~38s）或人话提示；README 测试数同步校正（评审当日
  126→127，W2–W5 测试落地后最终 233，见 §3.5）。
  客户不可见，只服务内部同事与交付操作员。**已达成**，见 §3.5。

### W6 · 客户自助服务控制台 `er_console.py` ✅ 已完成（2026-11-17）

- **来源**：用户拍板（2026-11-17）——能力展示维持 `play_live_server.py`
  不动，另开一个**干净的客户界面**：数据上传 → 初版展示 → 模型训练 →
  效果展示 → 服务部署 五步，对标 INF 训练控制台的形态；两者明确分界，
  互不掺和（此前租户 demo 页"太乱"的反馈由此解决：那是能力展示，不是服务面）。
- **架构**：逻辑全在 `src/entity_arena/console.py`（`Workspace`：
  **状态纯文件推导，不存第二份状态**——`uploads/`+`package/intake_report.json`
  →①，`cases.json`+`demo.html`→③④，`mcp_bundle/START.md`→⑤，四态
  empty/ready/waiting/done 自动点亮）；`scripts/er_console.py` 只是薄
  HTTP 壳（模式同 er_gateway.py：build_handler + 纯标准库 + 同源零外链）。
- **诚实边界**：③ 训练不假装——操作员中介（"本控制台不假装自己会训练"），
  产物落工作区即点亮；② 打分器是字面相似度**基线代理**，页面标注
  NOT a model output；⑤ 展示的 api_key 是发给该租户的静态键
  （er_gateway 注册表同源），**平台 EMB_TOKEN 绝不进控制台**。
- **④ 网络三态速览**（用户追加，2026-11-17）：完整效果页的 Network
  三态（原始纠缠 → 基线代理红标 → 微调归并）浓缩成三张小图内嵌进
  ④ 效果展示——客户不跳页就能看到"效果变化"。数据来自工作区
  `cases.json` 的 `network`/`baseline.network` 块（build_tenant_cases
  产物自带，零额外构建），`Workspace.network()` 服务端瘦身成三阶段
  payload（"A|B" 串拆二元组、光环带 mixed 纯度位），`GET /api/network`
  供前端 SVG 渲染；缺块人话报错（旧版产物提示重建），影子线用
  `--edge` token 与演示页同可见度，渐变钮文字走 `--on-sig` 同
  CTA 约定。完整大图仍由"打开完整效果页"跳 demo.html。
- **验收**：`tests/test_er_console.py` 17 项（逻辑 8 + HTTP 8 + 模板 1）；
  6 视图 × 双主题 12 张截图过查（含 5 处视觉修复 + 网络速览追加
  2 处对比度修复，见 §3.6）。**已达成**。

### W7 · 控制台直连训练平台（交互式五步闭环）✅ 已完成（2026-11-17）

- **来源**：用户五点需求（2026-11-17）——① 效果页要可交互（静态三小图
  太丑）；② 上传与数据查看合并同一 tab；③ 去掉原生 file input 的
  「选择文件」中文控件；④ 内置 Febrl 示例数据一键载入方便熟悉上手；
  ⑤ 训练与 θ 选择直接打通训练平台，不在多界面间跳转。
- **架构**：
  - `src/entity_arena/train_client.py`：**MCP-over-HTTP 客户端**（纯标准库，
    与 .tmp/mcp_call.sh 同一已验证通路）——`initialize`
    （protocolVersion 2025-03-26）取 `Mcp-Session-Id` →
    `notifications/initialized` → `tools/call`；SSE `data:` 行解析 +
    整体 JSON 兜底；"Session not found" 重握手一次自愈；平台网关掐
    OpenSSL 3.x ClientHello → **强制 TLS1.2**（同 embed_client）。令牌
    调用方注入，本模块不落盘不打印。
  - `src/entity_arena/console_train.py`：`Trainer` 编排层。`start`
    （zip_package → `upload_dataset` → `create_finetune_job`）/
    `status`（轮询 job 推进 phase：training→trained / failed，失败带
    人话）/ `deploy`（`deploy_model` → deploying→deployed，服务地址
    `_service_url` 多级兜底 + `list_services` 兜底）/ `build`
    （`fetch_village_embeddings --records --fieldmap` 取样向量 →
    `build_tenant_cases --emb --demo-dir --mcp-dir` → ④⑤ 点亮）。
    外部状态只记 `.train/state.json`（**平台世界的指针文件，不是第二份
    工作区状态**——③④⑤ 点亮仍纯文件推导）；argv 组装是纯函数（测试锁）。
  - `scripts/er_console.py` 新路由：`GET /api/sample`（Febrl 示例上传
    载荷）、`GET /api/train[?models=1]`（实时状态 + 平台基模列表）、
    `POST /api/train/start|deploy|build`；`load_train_token` 四级解析
    （--train-token > TRAIN_TOKEN > EMB_TOKEN > ~/.claude.json 的
    inf-retriever Authorization，启动打印来源）。
  - 控制台 SPA：①② 合并为「数据」tab（自定义 dropzone 去 native 控件 +
    「载入 Febrl 示例数据」按钮）；③ 训练配置面板（基模下拉=平台实时
    列表、输出名/服务名自动生成可改）+ 实时进度面板（phase chip 流）；
    ④ 效果页换成**全交互网络三态**（阶段页签/悬停高亮气泡/点击锁定，
    与演示页同款交互）。
- **诚实边界**：训练任务真耗 GPU——由客户自己点「上传训练包并开始训练」
  （平台 skill 安全约定：不擅自替客户开训），控制台只编排不假装训练；
  缺令牌自动回落 W6 人工模式（`can_train=False`，下载 zip + 操作员中介，
  行为与旧版一致）；`--api-key` 仍只展示发给客户的静态键；**平台训练
  令牌只进进程内存与子进程环境变量（EMB_TOKEN 取样），绝不落盘/进页面/
  进工作区**；向量只落 `.train/`（工作区内部）；GPU 抖动 ±6e-4 惯例
  （判定看 θ 两侧 ≥0.01）。
- **θ 不手选**：`build` 走 build_tenant_cases 的 link-F1 扫描，θ 选择
  口径与全仓一致（页面 chip 只展示扫描结果）。
- **验收**：`tests/test_er_console.py` 17 → **31 项**（+14：SSE 解析/会话
  自愈/错误面、Trainer 全 phase 推进与失败人话、argv 纯函数、HTTP 路由
  门控与降级）；全仓 **264 项 OK**。data/train/effect 三视图双主题
  重过（§3.7）。

### W8 · 一体化产品服务 `er_server.py`（对外单进程）✅ 已完成（2026-11-18）

- **来源**：用户需求（2026-11-18）——演示页（play_live_server）与客户
  控制台（er_console）两个进程合成一个对外服务，「期望是成熟的对外的
  产品」：一个基地址交给客户，能力展示与自助服务同源可达。
- **架构（纯组合，零逻辑拷贝）**：`scripts/er_server.py` importlib 载入
  两个薄壳的 `build_handler`，两个 Handler 类**多继承按路径分发**：
  - `/` 能力演示页 + `POST /api/score` 在线打分（play_live_server 原面，
    深链 `?view=`/`?track=` 照带；fresh clone 数据引导 + 页面自动重建
    同一入口逻辑）；
  - `/console/` 客户控制台整面（er_console 原面）——控制台 SPA 的
    fetch/href **全相对路径**（`fetch('api/state')`、
    `href="download/package.zip"`），服务端剥 `/console` 前缀再委托原
    handler，`er_console.py` 与 `play_live_server.py` **零改动、行为与
    单独起完全一致**（两薄壳套件原样全绿即证）；
  - `GET /healthz` 组件状态 JSON（页面 built / 打分 live+θ / 五步 /
    训练 phase）——对外可探测，不静默。
- **顺带收敛**：er_console 的直连训练接线抽成 `setup_trainer`（er_console
  与 er_server 共用一份，横幅语义不变）。
- **命名空间与安全**：根级 `/api/*` 只认 `POST /api/score`，其余 404 带
  「控制台在 /console/api/」人话指路；`/console` 无斜杠 302 补齐且保留
  查询串（`?theme=` 深链不丢）；默认绑 127.0.0.1，绑非环回地址启动即
  警告（控制台接口无鉴权、`train/start` 耗 GPU——跨机器暴露必须前置
  带鉴权反代，口径与 W6/W7 相同）。
- **验收**：新增 `tests/test_er_server.py` **14 项**（挂载拓扑/前缀剥除
  委托/命名空间边界/healthz/无令牌诚实降级/OPTIONS 预检，stub 注入零
  网络）；er_console 35 项 + play_live_server 13 项组合面回归全绿；
  全仓 281 → **295 项 OK**。

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

## 3.6 实测记录（W6，2026-11-17）

- `tests/test_er_console.py` 17 项全绿（逻辑 8：步骤推导/全量收集/文件名
  护栏/序列化同口径/代理打分语义/产物点亮/报告形状/网络三态 payload；
  HTTP 8：零外链锁、state 形状、400 人话、proxy-score、network 端点、
  zip 双请求逐字节一致、MCP 下载防穿越防缺席、demo 404 人话；模板 1：
  占位符填满）。全仓 **250 项 OK**（网络速览前 248 + 2）。
- **端到端联调**（三进程：stub embeddings :8899 + er_gateway :8790 +
  er_console :8801）：alpha 租户工作区五步全亮——① 上传 22 记录/13 实体/
  qrels 14/4、hard negatives 79；② 预览序列化与训练同口径
  （`name: alice smith | city: springfield`）、代理打分 exact=1.0 /
  单侧缺失 0.75 / 影子对 1.0（代理看不见实体边界，页面标注）；③ package.zip
  下载 1371B 确定性；④ θ=0.9650 / 断言 8 / 误连 0 / 漏连 1 vs 基线代理
  θ=0.6944 / 误连 5；⑤ API 表（key-alpha-demo mono 展示）+ MCP 七件套
  下载 + START.md。
- **截图驱动的返工**（6 视图 × 双主题 12 张，1440×1000，virtual-time
  9000）：修了五处——① 正文链接落到浏览器默认蓝（theme.py 只锁
  :focus-visible 不锁链接色）→ `.cmain a` 统一 cyan-b token；② sidebar
  `ready` 态点无规则落灰 → 空心 cyan 环（就绪≠完成）；③ 深色主题原生
  file input 白得刺眼 → `color-scheme: dark` 跟随主题；④ 上传槽位不显示
  当前生效文件（"—"）→ 预填 intake_report.source 文件名 + 说明行；
  ⑤ 效果页两卡只有标题着色 → 角色色左线（cyan=微调/violet=基线代理，
  同 stepcard.on 视觉语言）；另部署文案"key 另行安全交付"与页内展示
  自相矛盾 → 改为"下表展示的就是发给你的"。
- **截图方法备忘**：控制台主题深链是 `?theme=dark`（localStorage 优先），
  与 hash 路由并用写作 `/?theme=dark#/view`——漏了参数时双主题截图会
  逐字节相同（本轮实际踩到，靠文件 size 相同发现）。
- **④ 网络三态速览追加**（用户需求：Full Results Page 的 Network 集成进
  控制台）：`Workspace.network()` + `GET /api/network` + 前端三小图 SVG
  （190px 高，坐标按 canvas 等比缩放）；真实工作区实测影子 51 / 基线
  13 断言·5 误连·1 漏连·8 光环（含红） / 微调 8 断言·0 误连·1 诚实
  漏连·8 cyan 光环，与指标卡数字一一对应。双主题截图各过一遍 AI 视觉
  查检，追加修两处对比度：影子线从 `--line2`（28% 灰，深色下近乎不可见）
  换 `--edge` @ .7（与演示页影子边同 token）；渐变钮文字 `#fff` 换
  `var(--on-sig)`（对齐演示页 `.cta.primary` 家族约定）。视觉模型在 1×
  小字号下把深色主题钮文误报为不可读，2×（--force-device-scale-factor=2）
  重截人眼复核为清晰可读——小元素结论存疑时放大再判，别急着改。

## 3.7 实测记录（W7，2026-11-17）

- `tests/test_er_console.py` 17 → 31 项全绿；全仓 **264 项 OK**（250 + 14）。
- **④ 效果页交互化 + 三连修**（截图与像素双重驱动）：
  1. 固定高 360px + `meet` 保比例 → 两侧 letterbox 死白：改 `height:auto`
     由 viewBox 宽高比自适应；
  2. 小数据集（22 条租户试点）分位归一坐标挤在 1200×780 中段、离群点
     落画布外被裁：消费端补 `netViewBox()`——按各阶段内容包围盒取景
     （含光环半径、+26 呼吸边距、宽高比收进 [1.4, 2.6]）。layout.py
     有字节锁**零改动**，问题全在消费侧；
  3. 画布只有 632px 宽：theme.py `.netwrap`（演示页 1fr+270px 双栏）
     与控制台同名 wrapper 撞名，grid 把画布挤进 1fr 列 → 控制台改名
     `netwrapc`。**教训：theme.py CSS 两站共用，控制台新增类名必须先
     对表查撞名**。
  - 修后像素实测：画布 918px 双主题全列宽，22 节点双主题零裁切，
    宽高比 ~1:1.45（带内）。
- **AI 视觉误报与命中台账（方法论修正）**：视觉模型两轮都标记深色主题
  渐变钮文字"看不清"，第一轮被判"1× 小字号误报"驳回（含一次 2× 重截
  人眼复核"清晰可读"）。最终像素取证（纯标准库 PNG 解码 + 局部渐变
  偏差扫描）证明**驳回本身才是误报**：`.cmain a { color:var(--cyan-b) }`
  特异度 (0,1,1) 压过 `.btn` 的 (0,1,0)，链接形态的实心按钮文字渲染成
  亮青 #48c5d0——深色主题亮渐变上 ≈1.2:1 真不可见（浅色主题深青可存活，
  故一直漏网；§3.6 记录的"2× 复核可读"看的是 button 形态，锚点形态
  一直坏着）。修复：`.cmain a.btn:not(.ghost) { color:var(--on-sig) }`
  （ghost 透明底上 on-sig 近黑同样不可读，故排除）。修后字形强偏差像素
  0→1086（深）/468→971（浅），on-sig 对两主题渐变端点 10.5/7.0:1
  （深）与 5.4/4.7:1（浅）全 ≥4.5。**方法论结论：小元素结论存疑时
  像素实测才是终审——"AI 误报"与"人眼复核可读"都可能错；同一评审
  第二次标记同一处时，值得一次客观验证而不是第三次主观判**。
- **训练直连链路**：FakeTrain 注入单测锁全 phase 推进
  （start→training→trained→deploying→deployed→building→done，含失败
  态人话与 BUSY 互斥）；基模列表对 ~/.claude.json 连接器令牌实测
  （list_models 返回 embeddinggemma-2 等）。**真实训练任务未启动**
  ——平台 skill 安全约定：GPU 成本，须用户明确确认才开训。
- **代码评审加固批（2026-11-18）**：修复 4 个高优先级问题 + 补 7 项回归
  测试，全仓 264 → **271 项 OK**（test_er_console 31 → 35）：
  ① `build_tenant_cases --emb` 单用必崩（`sp=args.scores` 为 None，
  控制台「生成效果数据」整链断）——溯源 scores 改为自动落向量旁
  `<同名>.scores.json`，并补**真跑 CLI** 的端到端测试（此前控制台测试
  用假子进程、本文件夹具全走 --scores，264 绿测没拦住）；
  ② 「失败不静默」漏洞族收敛——er_console `/api/upload` 缺键裸崩、
  play_live_server 非 dict JSON 裸崩、两台服务器缺 Content-Length
  KeyError、console.state() 遇 mcp_bundle 为文件裸崩：一律人话 JSON
  （400/500）+ er_console 加兜底 except（服务端打栈）；
  ③ intake 格式 B 回退负例在星形标签（某记录正例伙伴覆盖其余全部记录）
  下死循环 → 前置可达性检查，人话 IntakeError；
  ④ Trainer start/deploy/build 加互斥锁（ThreadingHTTPServer 防并发
  双发；非阻塞抢锁人话报错，status 只读不锁）。
- **单源收敛批（2026-11-18）**：四个"必须全仓单源"的东西收进
  `embed_client.py`（它是 MCP 交付包唯一的共享拷贝，放这里交付包才能
  独立跑）：`ssl_ctx`（bench/fetch 各自的复制品删除）、`cosine`
  （cases_core/gateway 改 import，零向量守卫同口径）、`load_theta`
  （live_pair_score/play_live_server/er_mcp_server 三处 θ 兜底 0.9208
  收敛为一份，live_pair_score 过期文档串"102 条记录 θ=0.9328"一并修正）、
  令牌三级解析（bench_embeddings 改 importlib 复用 play_live_server.
  load_token，硬失败语义保留）。附带：`tuned_mix` 混合式抽到 simulate.py
  单点定义（make_mock_data P3 共用，**重生成 data/ 逐字节一致已验证**，
  仅 demo.html 的 build 时间戳变化）；gateway api_key 比对改
  `hmac.compare_digest`（恒时，key 是长期凭据）；er_gateway 落地页
  label/model 统一 html 转义（防注册表侧注入，含回归测试）；theme.py
  死别名 TOKENS 删除（零消费者，旧注释与现口径相反）。新增
  `tests/test_embed_client.py` 9 项 + 落地页转义 1 项，全仓 271 → **281 项 OK**。

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
   **已拍板（2026-11-18 注记）**：走结构化——W2 起租户 payload 带
   `play_fields` 字段表键，试玩台按 fieldmap 渲染结构化输入（§3.2 实测）。
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
**状态（2026-11-18 复核）：W1–W8 全部完成**（各节 ✅ + §3.1–§3.7 实测
记录，含两批代码评审加固；全仓 295 项测试绿）。v1 的 DoD（真实客户
schema 全链 ≤1 天）待第一个真实客户实证——工具链已就位。*
