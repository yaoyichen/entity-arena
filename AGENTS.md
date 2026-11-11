# AGENTS.md

实体归能（Entity Resolution）演示台：**双轨数据集**（Febrl3 实测主轨 + 合成剧本轨，
页面顶部一键切换、四视图跟随）→ 双模型前后对比 → 单文件离线演示页 +
**页内试玩台**（play 视图双出题模式：挑村记录对=内嵌全量真实分数矩阵离线打分 /
直接输入两条=本地 sidecar 转发部署模型在线打分）+
MCP 试用通道（connect 视图 + `scripts/er_mcp_server.py`）。
背景与决策见 `docs/adr/`，完整用法见 `README.md`。

## 硬约束（违反即 bug）

1. **零第三方依赖**：运行时纯标准库（Python ≥3.11）。
2. **单文件、零外链**：`build/demo.html` 断网双击能开；唯一允许的 URL 字符串是
   SVG 命名空间常量（有测试锁定）。在线打分走**同源相对路径** `api/score`
   （`scripts/play_live_server.py` 把页面与接口挂同一源转发）——相对路径不是
   URL 字符串，契约不破；离线双击时该模式自然缺席，页面其余功能照常。
3. **确定性**：一切随机走 seed（默认 42）；同 seed 两次生成逐字节一致（有测试）；
   实测轨由已入库分数离线确定性重建（同样双跑锁定）。
4. **失败不静默**：构建带 self-check 标记；数据撞车/引用悬空直接 raise。
5. **诚实标注（按轨声明）**：合成轨 footer 声明 SYNTHETIC DATA、分数是代理评分
   （ADR 0002）；实测轨 footer 声明 Febrl3 公开基准、微调分数=真实模型实测、
   逐对基线=字面相似度代理。三处标注：状态条 / 统计条 / provenance。

## 常用命令

```bash
python3 scripts/make_mock_data.py            # 生成 data/ 合成轨（确定性）
python3 scripts/update_open_benchmark.py     # 生成 data/open_benchmark.json（开源基准轨，从已入库报告解析）
python3 scripts/build_open_cases.py             # 生成 data/open_cases.json（实测主轨全量：网络三态+Arena+指标板+Explain 卡，从已入库分数离线复算）
python3 scripts/build_demo.py --open         # 构建 build/demo.html 并打开（三个数据文件缺一即 raise）
python3 scripts/play_live_server.py          # 日常演示唯一命令：页面过期自动重建→起服务→自动开浏览器（含在线打分）
PYTHONPATH=src python3 -m unittest discover -s tests   # 测试（或装 pytest 后 pytest）
```

改了 `synth/simulate/layout` → 必须重跑生成+构建；改了 `page/theme` → 重跑构建；
重新评测了平台模型 → 重跑 `update_open_benchmark.py` + 构建；
重新取样了案例村向量 → 覆盖 `data_open/village_scores.json` 后重跑
`build_open_cases.py` + 构建（village_scores 缺席时构建显式提示，
页面只出合成轨——优雅降级路径保留，平台再故障时用）。

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
  429 限流档）与 batch=32 吞吐，数据缺席优雅降级），这两个视图不随双轨切换）
- `src/entity_arena/theme.py` 双主题调色板（`LIGHT` 默认 / `DARK`，各过六查）与 CSS
- `scripts/make_mock_data.py` 合成数据入口（带三态剧本契约断言：基线又误连
  又漏连、微调零误连+≥1 诚实漏连，不满足即 SystemExit）；`scripts/build_demo.py` 页面构建入口
- `scripts/make_open_dataset.py` 开源基准（Febrl）→ inf-retriever 训练平台上传包；
  源数据在 `data_open/`（随仓库提交）；训练/评测走 MCP `inf-retriever`（token 在
  `~/.claude.json`，不进仓库），流程见 skill `inf-retriever-finetune`
- `scripts/update_open_benchmark.py` 平台评测报告（`data_open/eval_reports/`，随仓库
  提交作溯源）→ `data/open_benchmark.json`；基线口径漂移会 raise。指标板第二板块
  （开源基准轨：真实模型实测）由它供数，双轨 footer 各自诚实标注
- `scripts/pick_open_village.py` 从 Febrl3 测试实体选"案例村"（228 条记录/
  78 实体/1223 影子对，预算 `--budget 230`；**选村对模型分数盲**，规则写在
  provenance 里，避免挑案例之嫌）→ `data_open/village.json`
- `scripts/fetch_village_embeddings.py` 调平台已部署微调服务的 `/v1/embeddings`
  取真实向量（`EMB_TOKEN` 环境变量传令牌；向量只落 `.tmp/`，不入库）
- `scripts/live_pair_score.py` 现场演示打分器：两条记录（案例村实体/rid/自由文本）
  → 部署服务实时余弦 + θ 判定；演示剧本见 `docs/live-demo.md`（深链 `?q=`
  直达 Arena 指定剧本查询）
- `scripts/er_mcp_server.py` **entity-er MCP 服务器**（stdio JSON-RPC，纯标准库）：
  把案例村打分器交给任意 MCP 客户端。三工具 `er_score_pair`/`er_find_records`/
  `er_about`；双模式：rid 对走 `village_scores.json` 离线矩阵（零配置、确定性=
  实时），自由文本走 `EMB_TOKEN` 实时调用（embed_client.py 同源）。仓库根
  `.mcp.json` 已带 Claude Code 项目级注册（无秘钥）；使用文档 `docs/mcp.md`、
  页面「接入」视图（`?view=connect`，命令/示例可一键复制）；行为由
  `tests/test_mcp_server.py` 锁定（离线分数必须等于入库矩阵值）
- `scripts/play_live_server.py` **试玩台在线打分 sidecar = 日常演示唯一入口**
  （纯标准库）：把 `build/demo.html` 与 `POST /api/score` 挂同一本地源——平台
  网关（istio-envoy）无 CORS（预检 401、响应无 ACAO，2026-11-09 实测），
  浏览器直连部署服务走不通，这是唯一干净路径。页面 fetch 相对路径
  `api/score`（零 URL 字符串）。**一个进程全包**：`ensure_page` 检测
  demo.html 缺失/构建输入（build_demo.py/page.py/theme.py/data/*.json）更新
  → 自动重建；默认启动即开浏览器（`--no-open`/`--no-build` 可关）。令牌
  `load_token` 三级解析：`--token` > `EMB_TOKEN` 环境变量 > 本机文件
  `~/.config/entity-arena/emb_token`（chmod 600、仓库外——**令牌绝不进仓库/页面**，
  本机已配好，启动零输入）；θ 与 live_pair_score/er_mcp_server 同源
  （open_cases.json，兜底 0.9208）；缺令牌 503 带人话、embed 失败 502、
  端口被占给人话提示（旧 sidecar 残留时 lsof|kill），行为由
  `tests/test_play_live_server.py` 锁定（score_fn 注入 stub，不碰网络）
- `src/entity_arena/embed_client.py` embeddings 客户端共用层（TLS1.2 强制+重试、
  余弦），live_pair_score / er_mcp_server / play_live_server 三处共用，别再写一份
- `scripts/build_open_cases.py` 案例村 + 真实成对分数 → `data/open_cases.json`
  （θ 扫描 F1 最大化 → 断言链 → 组件纯度；溯源分数写 `data_open/village_scores.json`
  入库，默认从该文件离线复算）。**实测主轨全量数据都由它供**：
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
  入场动画+网络往返都要走完，否则 `.view.active` 会被拍在半途发虚）。

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
数据进页面）。深链：`?view=` 直达七视图（缺省=home 导览落地页）、
`?track=open|synth` 全局双轨、`?stage=0|1|2` 网络三态
（双轨通用）、`?q=F1|Q2` / `?case=R3|P2`
按前缀自动切轨、`?pair=ridA,ridB` 试玩台直达指定对（挑村页签）、
`?fa=文本&fb=文本` 试玩台直达「直接输入」页签并自动打分（rid 或自由文本，
双 rid 走内嵌矩阵、其余走在线；需 sidecar 在线，空态照常预填）、
`?theme=dark|light` 黑白主题直达（默认浅色）。
