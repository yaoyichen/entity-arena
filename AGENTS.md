# AGENTS.md

实体归能（Entity Resolution）演示台：**双轨数据集**（Febrl3 实测主轨 + 合成剧本轨，
页面顶部一键切换、四视图跟随）→ 双模型前后对比 → 单文件离线演示页 +
**页内试玩台**（play 视图双出题模式：挑村记录对=内嵌全量真实分数矩阵离线打分 /
直接输入两条=本地 sidecar 转发部署模型在线打分）+
MCP 试用通道（connect 视图 + `scripts/er_mcp_server.py`）+
**多租户打分网关 er-gateway**（`scripts/er_gateway.py`，SaaS/MaaS 路线 W3：
租户托管页 + MaaS API 单进程，见 `docs/saas-maas-plan.md`）+
**客户自助服务控制台 er-console**（`scripts/er_console.py`，W6：给客户看的
干净五步界面——数据→训练→效果→部署；能力展示归 play_live_server，
两者分界明确；W7：③ 直连训练平台一键开训/部署/建效果数据 + ④ 全交互
网络三态，缺令牌自动回落人工模式）。
+
**一体化产品服务 er-server**（`scripts/er_server.py`，W8：对外只起这一个
进程——`/` 演示页+在线打分、`/console/` 客户控制台、`/healthz` 组件状态，
单进程双面同源挂载）。
背景与决策见 `docs/adr/`，完整用法见 `README.md`。

## 硬约束（违反即 bug）

1. **零第三方依赖**：运行时纯标准库（Python ≥3.11）。
2. **单文件、零外链**：`build/demo.html` 断网双击能开；唯一允许的 URL 字符串是
   SVG 命名空间常量（有测试锁定）。在线打分走**同源相对路径** `api/score`
   （`scripts/play_live_server.py` 把页面与接口挂同一源转发）——相对路径不是
   URL 字符串，契约不破；离线双击时该模式自然缺席，页面其余功能照常。
3. **确定性**：一切随机走 seed（默认 42）；同 seed 两次生成逐字节一致（有测试；
   口径=数据生成物 data/*.json，`test_e2e_build` 双跑比对——demo.html 里带
   build 时间戳 `generated_at`，不在此列）；实测轨由已入库分数离线确定性重建
   （同样双跑锁定）。
4. **失败不静默**：构建带 self-check 标记；数据撞车/引用悬空直接 raise。
5. **诚实标注（按轨声明）**：合成轨 footer 声明 SYNTHETIC DATA、分数是代理评分
   （ADR 0002）；实测轨 footer 声明 Febrl3 公开基准、微调分数=真实模型实测、
   逐对基线=字面相似度代理。三处标注：状态条 / 统计条 / provenance。

## 常用命令

```bash
python3 scripts/make_mock_data.py            # 生成 data/ 合成轨（确定性）
python3 scripts/update_open_benchmark.py     # 生成 data/open_benchmark.json（开源基准轨，从已入库报告解析）
python3 scripts/build_open_cases.py             # 生成 data/open_cases.json（实测主轨全量：网络三态+Arena+指标板+Explain 卡，从已入库分数离线复算）
python3 scripts/build_tenant_cases.py --records client.csv --fieldmap fm.csv --scores scores.json --dataset-label "Acme records" --out data/open_cases.json --demo-dir demo_acme --mcp-dir demo_acme/mcp_bundle   # 租户体验页 + MCP 交付包一条命令（W2/W4：cases+demo+bundle 全包，见 docs/saas-maas-plan.md）
python3 scripts/build_demo.py --open         # 构建 build/demo.html 并打开（三个数据文件缺一即 raise）
python3 scripts/play_live_server.py          # 日常演示唯一命令：页面过期自动重建→起服务→自动开浏览器（含在线打分）
python3 scripts/er_server.py --workdir .tmp/console_acme --label "Acme pilot"   # 对外一体化产品服务（W8）：单进程双面——/ 演示页+在线打分、/console/ 客户控制台、/healthz；组合 play_live_server 与 er_console 两个面，对外交付只起这一个
python3 scripts/er_gateway.py --registry ~/.config/entity-arena/er_tenants.json   # 多租户打分网关：租户托管页 /t/<name>/ + MaaS API /v1/er/score|embed|resolve（W3）
python3 scripts/er_console.py --workdir .tmp/console_acme --label "Acme pilot" --api-url http://127.0.0.1:8790/v1/er/score --api-key '<租户静态键>'   # 客户服务控制台：五步界面（W6/W7；--api-key 只放发给客户的静态键，绝不放平台 EMB_TOKEN；W7 直连训练令牌走 --train-token/TRAIN_TOKEN/~/.claude.json，缺了自动回落人工模式）
PYTHONPATH=src python3 -m unittest discover -s tests   # 测试（或装 pytest 后 pytest）
```

改了 `synth/simulate/layout` → 必须重跑生成+构建；改了 `page/theme` → 重跑构建；
改了 `embed_client`（TLS/θ/cosine 单源层）→ 先跑 `tests/test_embed_client.py`
再跑消费方套件（gateway/er_gateway、play_live_server、mcp_*）；
改了 `cases_core/intake` → 先跑 `tests/test_tenant_cases.py`（锚 A 字节锁）再重建；
改了 `gateway`/`er_gateway` → 先跑 `tests/test_er_gateway.py`（在线=入库逐对锁）再重建；
改了 `console`/`er_console` → 先跑 `tests/test_er_console.py`（步骤推导 +
HTTP 全链 + 零外链锁）；改了 `train_client`/`console_train` → 同一文件
（SSE 解析/会话自愈/Trainer phase 推进都在里面）；
改了 `er_server`（路由/前缀委托/healthz）→ 先跑 `tests/test_er_server.py`
（挂载拓扑 + 命名空间边界 + 无令牌降级），再跑两个薄壳套件
（test_play_live_server / test_er_console——组合面不能把被组合面改坏）；
（SSE 解析/会话自愈/Trainer phase 推进都在里面）；
改了 `er_mcp_server`/MCP 交付（`--mcp-dir`/conn.* 覆盖键）→ 先跑
`tests/test_mcp_server.py`（Febrl 默认路径字节锁）+ `tests/test_mcp_delivery.py`
（交付包三方一致）再重建；
重新评测了平台模型 → 重跑 `update_open_benchmark.py` + 构建；
重新取样了案例村向量 → 覆盖 `data_open/village_scores.json` 后重跑
`build_open_cases.py` + 构建（village_scores 缺席时构建显式提示，
页面只出合成轨——优雅降级路径保留，平台再故障时用）。
租户侧同理：重新取样租户向量 → 覆盖 scores 文件后重跑
`build_tenant_cases.py` + 构建。

## 代码地图

- `src/entity_arena/synth.py` 核心剧本人物（手工）+ 填充人物（seed 生成）、关系网
- `src/entity_arena/simulate.py` 字段相似度、双模型代理评分、检索评测、指标；
  `pair_matrix`/`undirected_scores`/`link_components`/`link_stats` 供网络三态——
  成对分数与 `results.json` 同 rng 流同分（retrieve_all 从 pair_matrix 派生，
  保证网络断言链与 Arena/记分牌永不矛盾），改打分逻辑只动一处
- `src/entity_arena/layout.py` 网络三态坐标：原始孤岛 + 双模型断言组件
  （FR + 分位归一 + 光环防撞 + 标签防撞 `_comp_layout`；微调态带关系边、
  基线态纯斥力），组件成员坐标 `comp_members` 供前端节点落位
- `src/entity_arena/page.py` 演示页模板 + 前端 JS（双语文案、七个视图、深链；
  home=导览·默认落地页（无 `?view=` 即落在它：开场数字带/三步叙事直达
  `?stage=`/双轨卡/六视图导览，数字全部按 TRACK 现算、口径随 tile 名标注）；
  network 三态双轨同构——synth `network.json.baseline` 与 open `open_cases.baseline`
  逐字段同 shape，一套渲染两轨通用，`?stage=` 双轨直达；
  play=试玩台·双页签出题（挑村记录对=内嵌矩阵离线打分 / 直接输入两条=
  `fetch('api/score')` 同源在线打分，双 rid 输入走矩阵捷径、自由文本走 sidecar，
  `?fa=`/`?fb=` 深链直达并自动打分；sidecar 缺席时报错卡带启动命令，不静默）、
  connect=MCP 接入指南 + 部署服务容量实测卡（`renderBench`：`data_open/
  bench_embeddings.json` → 双口径 SVG 细柱——batch=1 QPS 饱和（16 并发红柱=
  429 限流档）与 batch=32 吞吐，数据缺席优雅降级），这两个视图不随双轨切换）；
  **租户文案机制（W2）**：`t()` 先查 `DATA.open_cases.page_labels[k]` 再落内置
  I18N，`plFieldTable` 优先 `OC.play_fields`——两个键都缺席时 Febrl 主页面
  渲染零变化，新增页面文案凡带数字一律占位符键（如 `stat.rec.open`/
  `home.step0.chip.open`），别在 JS 里拼死串
- `src/entity_arena/theme.py` 双主题调色板（`LIGHT` 默认 / `DARK`，各过六查）与 CSS
- `scripts/make_mock_data.py` 合成数据入口（带三态剧本契约断言：基线又误连
  又漏连、微调零误连+≥1 诚实漏连，不满足即 SystemExit）；`scripts/build_demo.py` 页面构建入口
- `scripts/make_open_dataset.py` 开源基准（Febrl）→ inf-retriever 训练平台上传包；
  源数据在 `data_open/`（随仓库提交）；训练/评测走 MCP `inf-retriever`（token 在
  `~/.claude.json`，不进仓库），流程见 skill `inf-retriever-finetune`
- `scripts/make_client_dataset.py` **客户数据入口**（SaaS/MaaS 路线 W1，见
  `docs/saas-maas-plan.md`）：任意 schema 的 CSV/JSONL 记录 + 双标签格式
  （A=entity_id 列 / B=--pairs 逐对标签）+ fieldmap 字段角色表 → 校验（行号定位、
  全量收集）→ BEIR 平台上传包（`--zip` 确定性打包，可直接 upload_dataset）+
  `intake_report.json` 溯源。共享逻辑在 `src/entity_arena/intake.py`——
  **序列化/实体切分与后续租户体验页、在线打分是同一份**（单一事实来源），
  切分算法与 febrl.split_entities 逐行同款（qrels 逐字节一致由
  `tests/test_intake.py::TestFebrlAnchor` 锁定，改任一侧先跑它）
- `scripts/update_open_benchmark.py` 平台评测报告（`data_open/eval_reports/`，随仓库
  提交作溯源）→ `data/open_benchmark.json`；基线口径漂移会 raise。指标板第二板块
  （开源基准轨：真实模型实测）由它供数，双轨 footer 各自诚实标注
- `scripts/pick_open_village.py` 从 Febrl3 测试实体选"案例村"（228 条记录/
  78 实体/1223 影子对；**选村对模型分数盲**，规则写在
  provenance 里，避免挑案例之嫌）→ `data_open/village.json`（入库的村是
  `--budget 230` 跑出来的——脚本默认 `BUDGET=48`，改预算=另选一个村，
  需重取向量再重建实测轨）
- `scripts/fetch_village_embeddings.py` 调平台已部署微调服务的 `/v1/embeddings`
  取真实向量（`EMB_TOKEN` 环境变量传令牌；向量只落 `.tmp/`，不入库）。
  双输入：`--village`（Febrl3 案例村）或 `--records`+`--fieldmap`（租户记录，
  intake 序列化与训练同口径）
- `scripts/live_pair_score.py` 现场演示打分器：两条记录（案例村实体/rid/自由文本）
  → 部署服务实时余弦 + θ 判定；演示剧本见 `docs/live-demo.md`（深链 `?q=`
  直达 Arena 指定剧本查询）
- `scripts/er_mcp_server.py` **entity-er MCP 服务器**（stdio JSON-RPC，纯标准库）：
  把案例村打分器交给任意 MCP 客户端。三工具 `er_score_pair`/`er_find_records`/
  `er_about`；双模式：rid 对走 `village_scores.json` 离线矩阵（零配置、确定性=
  实时），自由文本走 `EMB_TOKEN` 实时调用（embed_client.py 同源，双路径 import：
  仓库 `src/entity_arena/` → 交付包同目录 `embed_client.py` 兜底，交付拷贝无仓库
  也能跑）。**W4 租户模式**：`ER_META` 指向交付包 `er_meta.json` 时标签/真值
  称谓/字段对照表/θ 来源全按租户口径泛化（θ 只认 meta，meta 只认
  build_tenant_cases 产物），无 ER_META 的 Febrl 默认路径**逐字节不变**（由
  tests/test_mcp_server.py 锁定）；租户行为由 `tests/test_mcp_delivery.py` 锁定
  （三方一致：MCP 离线判定 == 页面试玩台 == er-gateway 在线）。仓库根
  `.mcp.json` 已带 Claude Code 项目级注册（无秘钥）；使用文档 `docs/mcp.md`、
  页面「接入」视图（`?view=connect`，命令/示例可一键复制；租户构建经
  page_labels 覆盖 conn.* 键，注册命令指向交付包绝对路径）
- `scripts/play_live_server.py` **试玩台在线打分 sidecar = 日常演示唯一入口**
  （纯标准库）：把 `build/demo.html` 与 `POST /api/score` 挂同一本地源——平台
  网关（istio-envoy）无 CORS（预检 401、响应无 ACAO，2026-11-09 实测），
  浏览器直连部署服务走不通，这是唯一干净路径。页面 fetch 相对路径
  `api/score`（零 URL 字符串）。**一个进程全包**：`ensure_page` 检测
  demo.html 缺失/构建输入（build_demo.py/page.py/theme.py/data/*.json）更新
  → 自动重建；**fresh clone 引导（W5）**：前置 `ensure_data` 检测 `data/`
  生成物缺失 → 三步确定性重建（make_mock_data → update_open_benchmark →
  build_open_cases，每步只在产物缺且已入库输入齐时跑，village_scores 缺席
  的降级路径不硬跑，~36s；行为由 test_play_live_server 注入式步骤锁定）；
  默认启动即开浏览器（`--no-open`/`--no-build` 可关）。令牌
  `load_token` 三级解析：`--token` > `EMB_TOKEN` 环境变量 > 本机文件
  `~/.config/entity-arena/emb_token`（chmod 600、仓库外——**令牌绝不进仓库/页面**，
  本机已配好，启动零输入）；θ 与 live_pair_score/er_mcp_server 同源
  （open_cases.json，兜底 0.9208）；缺令牌 503 带人话、embed 失败 502、
  端口被占给人话提示（旧 sidecar 残留时 lsof|kill），行为由
  `tests/test_play_live_server.py` 锁定（score_fn 注入 stub，不碰网络）
- `src/entity_arena/gateway.py` **多租户 ER 打分网关逻辑层**（W3，纯标准库）：
  租户注册表加载（仓库外 JSON，逐租户全量校验；θ 与模型名**只认
  build_tenant_cases 产物**——注册表没有 theta 键，model 与产物不一致启动即
  报错，永不手调不漂移）+ `Gateway`（embed_fn 可注入）：`score`/`embed_records`/
  `resolve` 三 API；结构化输入 `serialize_input` 走 **intake.serialize_record
  同一份**（fieldmap 声明序，未知字段 400 列允许清单）；稳判 |score−θ|≥0.01
  与页面/MCP/Explain 卡同口径；上游在途信号量护栏（默认 8，bench 锚点
  16 并发触 429 → 429 人话）；resolve ≤256 条/批 + borderline 对清单
  （|score−θ|<0.01，≤50 条，人工复核用）
- `scripts/er_gateway.py` 网关 HTTP 入口（**单进程三合一**：`/t/<name>/`
  各租户 demo 页同源托管——平台网关无 CORS，浏览器打分必须同源，这是
  play_live_server 模式的多租户版 + `/v1/er/score|embed|resolve` MaaS API
  （Bearer 每租户静态 api_key，X-Tenant 头或 body.tenant 选租户）+ `/`
  落地页租户清单表）；令牌三级解析 importlib 复用 play_live_server 的
  `load_token`（不抄一份，同样绝不进仓库/注册表）；Febrl 主页 `/api/score`
  兼容路径保留（default_tenant + raw 文本模式），与 play_live_server 不冲突；
  行为由 `tests/test_er_gateway.py` 锁定（25 项：双租户 θ/字段隔离、
  FakeEmbed 注入下在线分数==入库矩阵逐对、无令牌/未知租户/超并发三路人话）
- `src/entity_arena/console.py` **客户服务控制台逻辑层**（W6/W7，纯标准库）：
  `Workspace`——**状态纯文件推导，不存第二份状态**（`uploads/`+
  `package/intake_report.json`→① 上传 done；`cases.json`+`demo.html`→
  ③④；`mcp_bundle/START.md`→⑤ 部署；四态 empty/ready/waiting/done）；
  `upload`（base64 原件落 `uploads/`，intake 校验全量收集→ConsoleError，
  失败不清工作区）、`preview`（序列化与训练同一份 intake.serialize_record）、
  `proxy_score`（基线代理打分 + same/diff/missing 逐字段态）、`summary`
  （cases.json 统计，records 兼容 dict/list 两种 shape）、`network`
  （④ 效果三态 payload：`network`/`baseline.network` 块 → 原始影子/
  基线代理红标/微调归并三阶段，"A|B" 串拆对、光环带 mixed 纯度位，
  缺块人话报错）、`sample_files`（Febrl 示例上传载荷，一键载入用）；
  页面 `render_console`
  双语 SPA（hash 路由 `#/overview|data|train|effect|deploy`，W7 起 ①② 合并
  「数据」tab；主题/语言与演示页同约定 `ea_theme`/`ea_lang` + `?theme=`
  深链；④ 视图**全交互网络三态**——阶段页签/悬停高亮气泡/点击锁定，
  `netViewBox()` 按阶段内容包围盒取景（小数据集分位归一离群点防裁切，
  layout.py 字节锁零改动），画布类名 `netwrapc` **别改回 netwrap**——
  theme.py 那是演示页 1fr+270px 双栏会撞名；渐变钮文字 `--on-sig`，
  链接形态实心钮必须 `.cmain a.btn:not(.ghost)` 显式夺回字色——
  `.cmain a` 的 cyan-b 特异度会压过 `.btn`，深色主题下字会消失；⑤ 页
  **专业交付形态**：整包一个 `mcp.zip` 主 CTA（带文件数/体积，不再是
  散落小文件列表）+ 一行注册命令代码块可复制（`<bundle>` 占位与 START.md
  同形态）+ 包内清单表（角色/大小/单文件⇣）+ HTTP API 三态（未开通给
  占位卡不整段蒸发，导语 `dp.sub`/`dp.sub.off` 随在场切换）+ 五步完成
  收尾条（er_about 冒烟指引）；state 带 `mcp_bytes` 供 CTA 体积元数据；
  「怎么用」层=三工具紧凑表（租户通用静态文案）+ 完整指南出链
  `demo/?view=connect`（工作区演示页 connect 视图已被租户构建个性化，
  示例提问/两种模式/容量实测只在那一处——**别往控制台复制**，单源））
- `src/entity_arena/train_client.py` **INF 训练平台 MCP-over-HTTP 客户端**
  （W7，纯标准库）：initialize（2025-03-26）→ `Mcp-Session-Id` →
  notifications/initialized → tools/call；SSE `data:` 行解析+整体 JSON
  兜底；"Session not found" 重握手自愈；强制 TLS1.2（同 embed_client，
  别改回去）。令牌调用方注入，本模块不落盘不打印。业务面：
  upload_dataset/create_finetune_job/get_job_status/deploy_model/
  list_models/list_services
- `src/entity_arena/console_train.py` **③ 训练编排层**（W7）：`Trainer`——
  start（zip→upload_dataset→create_finetune_job）/status（轮询推进 phase，
  前端驱动）/deploy（deploy_model→服务地址多级兜底）/build（取样向量→
  build_tenant_cases --emb→④⑤ 点亮）；外部状态只记 `.train/state.json`
  （平台指针，**不是第二份工作区状态**）；argv 组装纯函数（测试锁）；
  θ 不手选（link-F1 扫描即 θ 选择）；向量只落 `.train/`
- `scripts/er_console.py` 控制台 HTTP 入口（薄壳，模式同 er_gateway：
  `build_handler`+ThreadingHTTPServer，纯标准库同源零外链；`/` SPA、
  `/api/state`、`GET /api/network`（三态数据）、`GET /api/sample`
  （Febrl 示例）、`GET /api/train[?models=1]`（实时状态+平台基模列表）、
  `POST /api/upload|/api/proxy-score|/api/train/start|deploy|build`、
  `/download/package.zip` 按需确定性打包、`/download/mcp.zip` 整包交付
  （⑤ 页主 CTA，同款确定性）、`/download/mcp/<f>` 防穿越、
  `/demo/` 托管工作区 demo.html 404 人话）；**分界约定：能力展示归
  play_live_server.py，控制台只做客户五步服务面**——③ 训练由客户自己
  点按钮开训（不替客户开训，GPU 成本；缺令牌自动回落 W6 人工模式，
  行为不变）、② 代理打分标注 NOT a model output、⑤ `--api-key` 只展示
  发给租户的静态键（er_gateway 注册表同源），**平台 EMB_TOKEN/训练令牌
  绝不进控制台/工作区**——训练令牌四级解析（--train-token >
  TRAIN_TOKEN > EMB_TOKEN > ~/.claude.json inf-retriever），只进进程
  内存与子进程环境变量；行为由 `tests/test_er_console.py` 锁定
  （35 项：步骤推导/全量收集/序列化同口径/代理语义/网络三态
  payload+端点/HTTP 全链/zip 确定性/零外链锁/train_client SSE 与会话
  自愈/Trainer 全 phase 推进与失败人话）；**只绑本机**——API 无鉴权
  （`/api/train/start` 能花 GPU 钱），跨机器暴露必须前置带鉴权反代
- `scripts/er_server.py` **一体化产品服务入口**（W8，纯标准库）：对外只起
  这一个进程——`/` 能力演示页 + `POST /api/score` 在线打分
  （play_live_server 同一面）+ `/console/` 客户控制台（er_console 同一面）
  + `GET /healthz` 组件状态（页面 built/打分 live+θ/五步/训练 phase）。
  实现是纯组合：importlib 载入两个薄壳的 `build_handler`，两个 Handler
  类**多继承按路径分发**；控制台 SPA 全相对路径
  （`fetch('api/state')`、`href="download/package.zip"`），服务端剥掉
  `/console` 前缀再委托原 handler——**er_console.py / play_live_server.py
  零改动，行为与单独起完全一致**；训练接线走 er_console.setup_trainer
  （共用不抄一份）。命名空间：根级 `/api/*` 只认 `POST /api/score`，
  其余 404 带「控制台在 /console/api/」人话指路；`/console` 无斜杠 302
  补齐且保留查询串；默认绑 127.0.0.1，绑非环回启动即警告（控制台接口
  无鉴权）；行为由 `tests/test_er_server.py` 锁定（挂载拓扑/前缀委托/
  命名空间边界/healthz/无令牌诚实降级 13 项）
- `src/entity_arena/embed_client.py` embeddings 客户端**单源层**：它是 MCP
  交付包里唯一的共享拷贝（`--mcp-dir` 原样带走），所以四个全仓单源件都住这：
  `ssl_ctx`（TLS1.2 钳制，bench/fetch 复制品已删）、`cosine`（零向量守卫）、
  `load_theta`（open_cases.json θ + 0.9208 兜底，live_pair_score/
  play_live_server/er_mcp_server 三处收敛）、embed 重试。消费方：
  live_pair_score / er_mcp_server / play_live_server / gateway /
  bench_embeddings / fetch_village_embeddings / cases_core——**别在任何
  脚本里再写一份**；行为由 `tests/test_embed_client.py` 锁定（改它先跑）
- `scripts/build_open_cases.py` 案例村 + 真实成对分数 → `data/open_cases.json`
  （**W2 起瘦身为 Febrl 适配层**：字段标签/基线权重 `_BASE_W`/叙事文案留在这，
  统计全走 `cases_core.py`；输出与重构前逐字节一致由
  `tests/test_tenant_cases.py::TestFebrlRebuildAnchor` 锁定，改核心先跑它）。
  θ 扫描 F1 最大化 → 断言链 → 组件纯度；溯源分数写 `data_open/village_scores.json`
  入库，默认从该文件离线复算。**实测主轨全量数据都由它供**：
  ① 网络三态：原始=影子关系纠缠簇 → 基线=字面相似度**代理**打分（平台无法逐对
  部署基模，权重/口径写进 provenance，误连红线+漏连红虚线⊘+混合组件红光环）→ 微调=真实断言
  组件。**漏连双轨统一组件级口径**：真对落在不同组件才算（与图上 ⊘ 一一对应，
  被链式合并进同组件的不计；对级口径归记分牌），规则进 `provenance.missed_rule`；
  ② Arena 六查询 F1–F6（六类极值规则：大家族/缺失/影子纠缠/基线误连/
  基线漏连/错字风暴，规则全文进 `provenance.arena_rule`，类别池空则缺席）；
  ③ 指标板（方向性口径与 simulate.py 逐条对齐，五分桶
  missing>conflict>typo_storm>typo>lookalike，空池诚实置 null 渲染"—"，
  recall@5=1.0 照登不隐藏、口径文本说明，规则进 `provenance.metrics_rule`）；
  ④ Explain 六张实测卡 R1–R6（极值规则挑选、距 θ ≥0.01 防 GPU 抖动翻判定，
  对叙事盲；R5/R6 注脚带基线代理分数）；⑤ 试玩台 play 矩阵：全量对按 1e-4
  量化（u16 小端 b64，rid 排序上三角行主序）入 `open_cases.json`，构建期逐对
  断言量化判定与全精度一致（量化翻判定即 raise），完整性由
  `tests/test_playground.py` 锁定（与 village_scores 逐对相等、判定集==links）
- `src/entity_arena/cases_core.py` **schema 无关的统计核心**（W2 从
  build_open_cases.py 抽出）：θ 扫描/断言链/组件纯度/方向性指标/五分桶/
  影子对挖掘/基线代理打分/play 矩阵量化/Explain·Arena 极值规则/
  `build_cases_payload` 组装。**只算数不写文案**——Febrl 适配层与租户入口
  注入 `name_of`/`stub_of`/`narrate_explain`/`narrate_arena`（ctx 只带
  预计算统计，叙事层永不重算）
- `scripts/build_tenant_cases.py` **租户体验页 + MCP 交付包入口**（SaaS/MaaS
  路线 W2/W4，见 `docs/saas-maas-plan.md`）：intake 记录（格式 A/B 真值）+
  成对真实分数（`--scores` 入库文件 / `--emb` 向量现算二选一，全对覆盖校验；
  `--emb` 单用时溯源分数自动落向量旁 `<同名>.scores.json`，不依赖 --scores）+
  fieldmap（含 `baseline_weight`/`label` 可选列）→ 与 open_cases.json 完全
  同 shape 的 payload，另注入 `page_labels`（40 个 i18n 覆盖键；带 `--mcp-dir`
  时 42——多出 `conn.install.cmd`/`conn.install.cfg` 两键：把页面所有
  Febrl 硬编码文案换成按 payload 实算的租户文案——首页数字带/阶段 chip/
  状态栏/徽标/轨道卡/试玩台占位符=首条记录真实序列化/W4 起 connect 视图
  全套：`conn.install.cmd`/`conn.install.cfg` 注入交付包绝对路径、示例问题
  用租户数据、容量卡去案例村措辞，数字仍占位符 `{TH}`/`{D}` 运行期替换）与
  `play_fields`（试玩台字段表键）；影子对默认按 neg_key 桶挖掘
  （`--shadow-max-bucket` 上限），也可 `--shadow-pairs` 显式给。`--demo-dir`
  一条命令组装数据目录并跑 build_demo（**相对路径一律先 resolve 成绝对**：
  子进程 cwd=仓库根，相对路径会静默写错位置——加过 mtime 守卫，demo.html
  比刚写入的数据旧即 raise）。`--mcp-dir`（W4）另产出交付七件套（server +
  embed_client 逐字节拷贝 + records/scores/er_meta + env + START.md），θ/模型
  /字段表与 cases 产物同源。回归锚双保险在 `tests/test_tenant_cases.py`：
  锚 A=Febrl 主构建字节不变，锚 B=案例村走租户路径逐字段一致（文案豁免：
  比 fields/entity 不比 text）；交付包由 `tests/test_mcp_delivery.py` 锁定
- `src/entity_arena/febrl.py` Febrl 数据共用工具（实体切分/序列化/索引）
- **部署要点（2026-11-08 实测）**：平台 serving 当日整体故障过一轮（新产物
  `train`/`pipeline`、旧产物一律 ~4min FAILED；`deploy_model` 起的服务全挂），
  后经**手动在控制台创建服务**（`embedding-febrl3-er-gemma`）恢复并完成取样。
  再遇部署故障：先查 Deployed Services 有没有现成可用服务，别急着重训。
  另两个坑：FAILED 部署留僵尸 serving 名（重试换新名）；平台网关掐 OpenSSL 3.x
  默认 ClientHello（后量子密钥共享），`fetch_village_embeddings.py` 已强制
  TLS1.2 + 重试，别改回去。**网关无 CORS**（同日实测：OPTIONS 预检 401、
  200 响应不带 Access-Control-Allow-Origin）——浏览器页面无法直连部署服务，
  在线打分类需求一律走本地同源 sidecar（`play_live_server.py`），别再试直连。
  GPU 抖动 ±6e-4 属正常（在线分数 vs 入库矩阵），判定以 θ 两侧 ≥0.01 距离为准。

## 视觉语言（继承 retrieval-forge ADR 0005 · 2026-11-09 双主题：浅色默认 + 深空可切，两套角色色各过六查）

- **双主题机制**：`<html data-theme="light|dark">`，**浅色默认**（白纸底
  `#f4f6f9`/面板纯白），深空（`#0c1117`）一键切回；`theme.py` 里 `LIGHT`/`DARK`
  两套调色板 → `:root` 携带浅色、`html[data-theme="dark"]` 整块覆盖，其余 CSS
  全部走 token 不写死色。选择记 `localStorage.ea_theme`，`?theme=dark` 深链
  可截图；head 内有先行脚本在首帧前定主题（防闪白/闪黑）。顶栏 ☾/☀ 切换钮；
- 靠**明度**分层：深底重要的更亮，浅底重要的更暗；token 化排版见 `theme.py`
  （字号阶 11–28、8px 网格、圆角 4/8/16、`--shadow-1/2`、`.eyebrow` 小眉标）；
- 全场只有两个角色色：**cyan = 微调模型/正确**，**violet = baseline**；红是批注红，
  只给**错误断言**，不做警报色滥用。**两套主题各有独立角色色**（dark
  `#10a6ad/#787bd2/#cf6d58`，light `#12907a/#5a5dc0/#b84732`——浅色整体压深
  换对比度，cyan 色相偏绿一档是彩度地板所迫，数值见 `theme.py` 留档），均经
  dataviz 六查（OKLab ΔE + Machado CVD：亮度带 dark 0.48–0.67 / light 0.50–0.62、
  彩度 ≥0.10、相邻 CVD ≥8、正常视觉 ≥15、对底/面板 ≥3:1），由
  `tests/test_theme_palette.py` **按主题分别锁定，改任一侧先跑该测试**；
  彩色文字用对应主题的 `cyan-b/violet-b/red-b/cyan-l`（面板 ≥4.5:1）；
- **平台家族签名**：135° 青→紫渐变 `--sig` 双档（dark `#22d3ee→#a78bfa` 取自
  INF 控制台 styles.css 同款；light `#0895b5→#7a6bf0` 压深保可读），只做界面
  铬件——logo/导航激活态/双轨切换器/CTA 主按钮/卡片左线 `.gline`/玻璃上缘高光；
  **绝不做数据编码**，数据标记永远用实色角色色（chrome-only 约定由
  test_theme_palette.py 锁定）；
- 图表按 dataviz 标记规格：细柱（≤24px）、数据端 4px 圆角基线端方、发丝实线
  网格、值只标关键列（其余进 tooltip/表格）、文字穿文字色不穿系列色、
  ≥2 系列必带图例（`chartLegend()`）；
- 任何新 UI 先截图过一遍涉及视图（`?view=` 深链直达，导览+六视图见 page.py）、
  **两个主题各截一遍**（`?theme=dark` 直达）再合入（headless 截图记得加
  `--virtual-time-budget`：静态页 3000 起步，试玩台/深链自动打分态用 9000——
  入场动画+网络往返都要走完，否则 `.view.active` 会被拍在半途发虚；
  **经 er_gateway 网关服务的页面给到 20000**，多一跳转发，9000 会把入场
  渐隐拍在半途）。

## 数据契约（换真模型时）

合成轨：`results.json`：`{models: {baseline: {qid: [{r, s}×5]}, tuned: …}}`
（**key 是 record_id**，不是 query_id——适配层按 `q.record_id` 查）；
`metrics.json`：overall + by_scenario；`network.json` 自带三态键
（`theta/components/comp_of/stats/links/false_links/missed_pairs` + 基线块
`baseline{kind:"proxy",…同 shape}`，缺 baseline 块构建即 raise——防半新半旧）。
替换 results/metrics 两个文件，页面零改动（network.json 由生成器一并产出）。
开源基准轨：`open_benchmark.json`（由 `data_open/eval_reports/` 报告重建）——
重新评测后用新报告覆盖该目录再跑 `update_open_benchmark.py` 即可。
实测主轨：`open_cases.json`（由 `data_open/village.json` + `village_scores.json`
重建；village_scores 缺席时优雅降级为仅合成轨、切换器隐藏）——重新取样向量后
覆盖 `village_scores.json` 再跑 `build_open_cases.py` 即可。必备键含
`arena`（F1–F6 查询）与 `metrics`（方向性指标+分桶），缺即 raise（防半新半旧
数据进页面）。**两个可选键（W2/W4，租户轨专用，Febrl 主构建不带）**：
`page_labels`——i18n 覆盖表 `{key: {en, zh}}`，页面 `t()` 先查它再落内置
I18N（数字一律占位符 `{N}/{E}/{S}/{A}/{T}/{F}/{M}/{TH}/{D}` 运行期替换，
构建期实算注入，文案里不手写数字；W4 的 `conn.install.cmd`/`conn.install.cfg`
例外——交付包绝对路径构建期写死进键值）；`play_fields`——试玩台字段表的键列表（字段名即
标签），缺席回落 Febrl 五字段。深链：`?view=` 直达七视图（缺省=home 导览落地页）、
`?track=open|synth` 全局双轨、`?stage=0|1|2` 网络三态
（双轨通用）、`?q=F1|Q2` / `?case=R3|P2`
按前缀自动切轨、`?pair=ridA,ridB` 试玩台直达指定对（挑村页签）、
`?fa=文本&fb=文本` 试玩台直达「直接输入」页签并自动打分（rid 或自由文本，
双 rid 走内嵌矩阵、其余走在线；需 sidecar 在线，空态照常预填）、
`?theme=dark|light` 黑白主题直达（默认浅色）。
