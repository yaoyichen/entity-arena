"""演示页组装：数据 bundle + 主题 CSS + 交互 JS → 单文件 HTML。

约束（继承 ADR 0005）：单文件、零外链、离线双击可开。
模板用占位符替换（不用 f-string——CSS/JS 花括号会打架）。
"""

from __future__ import annotations

import json

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entity Arena · 实体归并演示台</title>
<style>
__CSS__
</style>
</head>
<body>
<header>
  <div class="hbar">
    <div class="logo"><span class="mark">◆ ENTITY ARENA</span><span class="zh" data-i18n="app.zh"></span></div>
    <span class="pill" id="headPill"></span>
    <div class="trackbar hdr" id="trackBar"></div>
    <nav id="nav">
      <button data-view="arena" class="active" data-i18n="nav.arena"></button>
      <button data-view="network" data-i18n="nav.network"></button>
      <button data-view="explain" data-i18n="nav.explain"></button>
      <button data-view="scoreboard" data-i18n="nav.scoreboard"></button>
      <button data-view="play" data-i18n="nav.play"></button>
      <button data-view="connect" data-i18n="nav.connect"></button>
    </nav>
    <button class="lang" id="langBtn">中文</button>
  </div>
</header>
<main>
  <section id="view-arena" class="view active">
    <div class="vhead">
      <h1 data-i18n="arena.h"></h1>
      <p id="arenaSub"></p>
      <div class="legendline">
        <span class="k"><span class="dot" style="background:var(--cyan)"></span><span data-i18n="lg.tuned"></span></span>
        <span class="k"><span class="dot" style="background:var(--violet)"></span><span data-i18n="lg.baseline"></span></span>
        <span class="k"><span class="dot" style="background:var(--red)"></span><span data-i18n="lg.false"></span></span>
        <span class="k mono" id="thr-note"></span>
      </div>
    </div>
    <div class="arena-grid">
      <div><div class="qlist" id="qlist"></div></div>
      <div id="arenaMain"></div>
    </div>
  </section>

  <section id="view-network" class="view">
    <div class="vhead">
      <h1 data-i18n="net.h"></h1>
      <p data-i18n="net.sub"></p>
    </div>
    <div id="netTrackSub"></div>
    <div class="netwrap">
      <div class="netbox" id="netbox"></div>
      <div>
        <div class="card sidecard" id="netSide"></div>
        <div class="legend-grid" id="netLegend"></div>
      </div>
    </div>
  </section>

  <section id="view-explain" class="view">
    <div class="vhead">
      <h1 data-i18n="exp.h"></h1>
      <p id="expSub"></p>
    </div>
    <div class="pairsel" id="pairsel"></div>
    <div id="explainMain"></div>
  </section>

  <section id="view-scoreboard" class="view">
    <div class="vhead">
      <h1 data-i18n="sb.h"></h1>
      <p id="sbSub"></p>
    </div>
    <div id="scoreMain"></div>
    <div id="openBench"></div>
  </section>

  <section id="view-play" class="view">
    <div class="vhead">
      <h1 data-i18n="play.h"></h1>
      <p id="playSub"></p>
    </div>
    <div id="playMain"></div>
  </section>

  <section id="view-connect" class="view">
    <div class="vhead">
      <h1 data-i18n="conn.h"></h1>
      <p data-i18n="conn.sub"></p>
    </div>
    <div id="connectMain"></div>
  </section>
</main>
<footer>
  <div class="fin">
    <span class="pill" id="seed-pill"></span>
    <span id="footNote"></span>
    <span class="mono" id="buildInfo"></span>
  </div>
</footer>
<script>const DATA = __DATA__;</script>
<script>
__JS__
</script>
</body>
</html>
"""

JS = r"""
'use strict';
/* ============================ i18n ============================ */
const I18N = {
  'app.zh':        {en:'Entity Resolution Demo', zh:'实体归并演示台'},
  'badge.synthetic':{en:'SYNTHETIC DATA', zh:'合成演示数据'},
  'badge.open':    {en:'FEBRL3 · REAL MODEL SCORES', zh:'FEBRL3 · 真实模型实测'},
  'nav.arena':     {en:'Arena', zh:'对比台'},
  'nav.network':   {en:'Network', zh:'关系网络'},
  'nav.explain':   {en:'Explain', zh:'匹配解释'},
  'nav.scoreboard':{en:'Scoreboard', zh:'指标板'},
  'nav.play':      {en:'Playground', zh:'试玩台'},
  'nav.connect':   {en:'Connect', zh:'接入'},
  'lg.tuned':      {en:'Fine-tuned', zh:'微调模型'},
  'lg.baseline':   {en:'Baseline', zh:'基线模型'},
  'lg.false':      {en:'False link', zh:'误连'},
  'thr.at':        {en:'assert threshold', zh:'断言阈值'},

  'arena.h':       {en:'One query, two models', zh:'同一查询，两个模型'},
  'arena.sub':     {en:'Pick a scripted query. Left: off-the-shelf baseline. Right: fine-tuned. Records of the same true identity should be LINKED; look-alikes must NOT be.',
                   zh:'选择一个剧本查询。左栏：开箱基线模型；右栏：微调后模型。同一真实身份的记录应被连接（LINKED）；相似的"影子人"绝不能连上。'},
  'arena.sub.open':{en:'Febrl3 village, 228 real records. Left: literal-similarity baseline proxy. Right: the fine-tuned model\'s real scores. Same identity should be LINKED; look-alikes must NOT be.',
                   zh:'Febrl3 案例村，228 条真实记录。左栏：字面相似度基线代理；右栏：微调模型真实分数。同一身份的记录应被连接（LINKED）；相似的"影子人"绝不能连上。'},
  'arena.proxy':   {en:'literal-similarity proxy', zh:'字面相似度代理'},
  'arena.qs':      {en:'Scripted queries', zh:'剧本查询'},
  'arena.qrecord': {en:'Query record', zh:'查询记录'},
  'arena.missed':  {en:'not retrieved', zh:'未召回'},
  'arena.asserted':{en:'asserted', zh:'已断言'},
  'arena.of':      {en:'of', zh:'/'},
  'arena.true-links':{en:'true links', zh:'条真链路'},
  'v.linked':      {en:'LINKED ✓ same identity', zh:'已连接 ✓ 同一身份'},
  'v.candidate':   {en:'CANDIDATE below θ', zh:'候选 未过线'},
  'v.false':       {en:'FALSE LINK ✗ different person', zh:'误连 ✗ 不同的人'},
  'v.unrelated':   {en:'unrelated', zh:'无关'},
  'f.name':        {en:'Name', zh:'姓名'},
  'f.id':          {en:'ID', zh:'证件号'},
  'f.dob':         {en:'DOB', zh:'生日'},
  'f.pob':         {en:'POB', zh:'籍贯'},
  'f.phone':       {en:'Phone', zh:'电话'},
  'f.source':      {en:'Source', zh:'来源'},

  'net.h':         {en:'The same network, before & after resolution', zh:'同一张关系网：解析前 vs 解析后'},
  'net.sub':       {en:'Before: five systems, five islands — relationships fragment across aliases. After: duplicates merge into entity halos and the true network appears. Hover to focus, click a node for its dossier.',
                   zh:'解析前：五个系统五座孤岛，关系被打碎在各个化名之间。解析后：重复记录合并为实体光环，真实关系网浮出水面。悬停聚焦邻域，点击节点查看档案。'},
  'net.resolve':   {en:'⟶ Resolve entities', zh:'⟶ 执行实体归并'},
  'net.unresolve': {en:'⟲ Back to raw records', zh:'⟲ 退回原始记录'},
  'net.state.before':{en:'RAW · 47 records · 5 sources', zh:'原始态 · 47 条记录 · 5 个来源'},
  'net.state.after': {en:'RESOLVED · 19 entities', zh:'已解析 · 19 个实体'},
  'net.clickhint': {en:'Click any node to open its dossier.', zh:'点击任意节点查看档案。'},
  'net.members':   {en:'Entity members', zh:'实体成员'},
  'net.rels':      {en:'Relationships', zh:'关系证据'},
  'net.lg.record': {en:'record (raw)', zh:'记录（原始）'},
  'net.lg.halo':   {en:'resolved entity', zh:'解析后实体'},
  'net.lg.src':    {en:'source island', zh:'来源孤岛'},
  'net.track.synth': {en:'Scripted world', zh:'合成剧本'},
  'net.track.open':  {en:'Febrl3 · real model', zh:'Febrl3 · 真实模型'},
  'net.open.state.before': {en:'RAW · look-alike ties tangle different people together', zh:'原始态 · 相似纠缠把不同的人缠在一起'},
  'net.open.state.baseline': {en:'BASELINE · literal-similarity proxy · red links = false merges, red halo = mixed', zh:'基线打分（字面相似度代理）· 红线 = 误连，红光环 = 混入异实体'},
  'net.open.state.after':  {en:'RESOLVED by the fine-tuned model · red halo = mixed', zh:'微调模型归并完成 · 红色光环 = 混入异实体'},
  'net.open.btn.base':   {en:'⟶ Score with baseline', zh:'⟶ 基线打分归并'},
  'net.open.btn.tuned':  {en:'⟶ Resolve with fine-tuned model', zh:'⟶ 微调模型归并'},
  'net.open.base.note':  {en:'Proxy baseline: classic field-similarity ER scorer, same θ rule (F1-optimal). Not per-record base-model scores — base models cannot be served on this platform; real base-model metrics are in the Scoreboard.', zh:'代理基线：经典字段相似度打分器，θ 同规则（F1 最优）。非基模逐条分数——基模无法在平台部署，真实基线指标见记分牌。'},
  'net.open.lg.blink':   {en:'baseline asserted link', zh:'基线断言连线'},
  'net.open.lg.blink.bad': {en:'false merge (baseline)', zh:'误连（红线）'},
  'ob.baseline.tag':     {en:'BASELINE · literal-similarity proxy', zh:'基线 · 字面相似度代理'},
  'ob.shadow':           {en:'look-alike ties', zh:'影子对'},
  'ob.shadow.note':      {en:'same surname / address / DOB — different people', zh:'同姓 / 同址 / 同生日的异实体，不是一伙的'},
  'net.open.lg.mixed': {en:'MIXED component (false link)', zh:'混合组件（误连）'},
  'net.open.lg.island': {en:'look-alike cluster', zh:'相似纠缠簇'},
  'ob.trueEnt':    {en:'True entity', zh:'真实实体'},
  'ob.comp':       {en:'Model component', zh:'模型组件'},
  'ob.mixed':      {en:'MIXED', zh:'混合'},
  'ob.top3':       {en:'Top-3 similarity', zh:'相似度 Top-3'},
  'ob.truth':      {en:'truth pairs', zh:'真值对'},
  'ob.asserted':   {en:'asserted', zh:'断言'},
  'ob.false':      {en:'false links', zh:'误连'},
  'ob.missed':     {en:'missed', zh:'漏连'},
  'ob.comps':      {en:'components (n/pure)', zh:'组件（总/纯）'},

  'exp.h':         {en:'Why did it match — or refuse to', zh:'为什么连上了——或者为什么拒绝'},
  'exp.sub':       {en:'Field-by-field evidence for the decisive pairs. The tick on each score bar marks the decision threshold θ; scores above it are asserted as links. Scripted world: both models\' verdicts side by side.',
                   zh:'关键记录对的逐字段证据。分数条上的刻度线是判定线 θ，过线即断言。合成剧本：两模型判决并排对比。'},
  'exp.sub.open':  {en:'Field-by-field evidence from the Febrl3 village. Scores are the fine-tuned model\'s real outputs; θ is this track\'s F1-optimal threshold. Cards are picked by deterministic extreme-value rules, not by story.',
                   zh:'Febrl3 案例村的逐字段证据。分数是微调模型的真实输出，θ 是本轨 F1 最优判定线。案例卡由确定性的极值规则挑出，不是看故事挑的。'},
  'exp.truth':     {en:'Ground truth', zh:'真实标签'},
  'exp.match':     {en:'TRUE MATCH', zh:'真·同一人'},
  'exp.nonmatch':  {en:'TRUE NON-MATCH', zh:'真·不同人'},
  'exp.realonly':  {en:'Base models cannot be served on this platform (deploy supports trained models only), so no per-record baseline score here — the before/after comparison is in the Scoreboard (same-set offline eval).',
                    zh:'基线模型无法在平台部署（deploy 仅支持已训练模型），这里没有逐条基线分数——训练前后对比见记分牌（同集离线评测）。'},
  'exp.ok-link':   {en:'correctly linked', zh:'正确连接'},
  'exp.ok-sep':    {en:'correctly separated', zh:'正确分开'},
  'exp.bad-miss':  {en:'MISSED — same person, not linked', zh:'漏连——同一人未被连接'},
  'exp.bad-false': {en:'FALSE LINK — two people merged into one', zh:'误连——两个人被并成一个人'},
  'st.exact':      {en:'exact', zh:'一致'},
  'st.fuzzy':      {en:'fuzzy', zh:'相近'},
  'st.conflict':   {en:'conflict', zh:'冲突'},
  'st.missing':    {en:'missing', zh:'缺失'},

  'sb.h':          {en:'Before / after, with the fine print', zh:'训练前后对比，附口径'},
  'sb.sub':        {en:'Every record queries the other 46; top-5 retrieved; links asserted at θ. Synthetic world, seed 42 — numbers illustrate the mechanism, not production performance.',
                   zh:'每条记录检索其余 46 条，取 top-5，过 θ 断言。合成世界（seed 42）——数字演示的是机制差异，不代表生产性能。'},
  'sb.sub.open':   {en:'Every one of the 228 Febrl3 records queries the other 227; top-5 retrieved; links asserted at each model\'s own θ. Tuned column: real model scores. Baseline column: literal-similarity proxy (base models cannot be served pairwise) — real baseline metrics below.',
                   zh:'228 条 Febrl3 记录两两检索，各取 top-5，按各模型自己的 θ 断言。微调列：真实模型分数；基线列：字面相似度代理（基模无法逐对部署）——真实基线指标见下方开源基准板块。'},
  'sb.linkrecall': {en:'Link recall', zh:'断言召回'},
  'sb.linkprec':   {en:'Link precision', zh:'断言精度'},
  'sb.falselink':  {en:'False-link rate (look-alikes)', zh:'误连率（相似实体）'},
  'sb.chart':      {en:'Link recall by scenario', zh:'各场景断言召回'},
  'sb.table':      {en:'Per-scenario detail', zh:'分场景明细'},
  'sb.col.sc':     {en:'Scenario', zh:'场景'},
  'sb.col.n':      {en:'queries', zh:'查询数'},
  'sb.col.r5':     {en:'Recall@5', zh:'Recall@5'},
  'sb.col.lr':     {en:'Link recall', zh:'断言召回'},
  'sb.col.fl':     {en:'False-link', zh:'误连率'},
  'sb.proto':      {en:'Protocol — each of 47 records queries the other 46; models return top-5 with scores; a hit = same ground-truth entity. Link recall = true links asserted (score ≥ θ) ÷ all true links. Link precision = asserted links that are true ÷ all asserted. False-link rate = share of look-alike queries (same name / twins) where a look-alike record was asserted. All data and scores are synthetic (seed 42); swap in real model outputs via the data contract (results.json) and every number on this page updates.',
                   zh:'口径——47 条记录两两检索，各取 top-5 及分数；命中 = 真同一实体。断言召回 = 被断言（分数≥θ）的真链路 ÷ 全部真链路；断言精度 = 断言中为真的比例；误连率 = 相似实体（同名/双胞胎）查询中断言了影子记录的比例。全部数据与分数均为合成（seed 42）；按数据契约替换 results.json 为真实模型输出，本页所有数字随之更新。'},
  'ob.h':          {en:'Open-benchmark track — real models × Febrl3', zh:'开源基准轨——真实模型 × Febrl3'},
  'ob.badge':      {en:'REAL MEASUREMENT', zh:'真实实测'},
  'ob.sub':        {en:'Three base embedding models, each fine-tuned on Febrl3 (5,000 person records / 2,000 identities, OCR & transliteration noise) with hard negatives on. Every number below is a real measurement on the held-out test split — the board above stays the scripted synthetic demo.',
                   zh:'三个基座检索模型各自在 Febrl3（5000 条个人记录 / 2000 个身份，含 OCR 错与音译错）上微调，硬负例开启。本板块所有数字均为留出测试集上的真实实测——上方板块仍是剧本合成演示。'},
  'ob.chart':      {en:'NDCG@10 before ⟶ after (axis 90–100)', zh:'NDCG@10 前 ⟶ 后（轴 90–100）'},
  'ob.table':      {en:'All metrics before ⟶ after', zh:'各指标 前 ⟶ 后'},
  'ob.col.model':  {en:'Model', zh:'模型'},
  'ob.col.wtl':    {en:'win/tie/loss · 850 queries', zh:'胜/平/负 · 850 查询'},
  'ob.proto':      {en:'Protocol — queries and documents share one pool (every record both queries and is retrieved), so top-1 usually hits the record itself: read absolute values as effective k−1; identical terms for all six runs, so before/after comparisons are fair. After fine-tuning all three models converge to Recall@1 = 0.444 ≈ 1/2.25 — the theoretical ceiling when top-1 is always relevant (on average 2.25 relevant records per query). Fixed test split (400 identities / 850 queries), pytrec_eval, hard negatives on. nemotron starts near-perfect (Recall@10 = 1.000) so its fine-tuning gain is necessarily tiny — shown as measured. Reports verbatim: data_open/eval_reports/.',
                   zh:'口径——查询与文档同池（每条记录既查也被查），top-1 常命中自身：绝对值按"有效 k−1"解读；六个运行同口径，前后对比公平。微调后三个模型的 Recall@1 一致收敛到 0.444 ≈ 1/2.25，即 top-1 必中相关文档时的理论上限（平均每查询 2.25 个相关记录）。固定 test split（400 实体 / 850 查询），pytrec_eval，硬负例开启。nemotron 基线已近满分（Recall@10 = 1.000），微调增益必然微小——如实呈现。报告原文：data_open/eval_reports/。'},
  'sb.proto.open': {en:'Protocol — each of the 228 village records queries the other 227; models return top-5 with scores; a hit = same ground-truth identity; links asserted at each model\'s own F1-optimal θ (tuned 0.921, baseline proxy 0.560). Directional counting, identical to the scripted track. recall@5 = 1.00 for BOTH models here — ranking true pairs into the top-5 is easy on this village; the difference is in the assertion metrics (dare-to-link, link-precision, false-link rate), where the tuned model is perfect and the proxy is not. Fine-tuned scores are real model outputs; pairwise baseline is a literal-similarity proxy (weights in provenance) — real base-model numbers are in the open-benchmark board below. Buckets classify each query by its field profile against true siblings (missing > conflict > typo-storm > typo > look-alike).',
                   zh:'口径——228 条村记录两两检索，各取 top-5 及分数；命中 = 真同一身份；断言 = 分数 ≥ 各模型自己的 F1 最优 θ（微调 0.921、基线代理 0.560）。方向性计数，与合成轨完全同口径。本村两模型 recall@5 均为 1.00——把真对排进前五不难，差距在断言指标（敢连、连得对、不误连）：微调模型全对，字面代理不是。微调分数为真实模型输出；逐对基线是字面相似度代理（权重见 provenance）——真实基线数字在下方开源基准板块。分桶按每条查询对其真身兄弟的字段形态分类（缺失 > 冲突 > 错字风暴 > 零星错字 > 影子相似）。'},
  'conn.h':        {en:'Take the scorer into your own AI', zh:'把打分器装进你的 AI 助手'},
  'conn.sub':      {en:'This page tells the story offline; the MCP server ships the same scorer — real model scores, θ verdict, field-by-field evidence — into any MCP client (Claude Code, Claude Desktop, any agent). Pure standard library, zero dependencies; village-rid pairs score offline with zero config.',
                   zh:'本页负责离线讲故事；MCP 服务器把同一个打分器——真实模型分数、θ 判定、逐字段证据——装进任意 MCP 客户端（Claude Code、Claude Desktop、任何 agent）。纯标准库、零第三方依赖；案例村 rid 对零配置即可离线打分。'},
  'conn.s1.h':     {en:'1 · Register once', zh:'① 一次注册'},
  'conn.s1.t':     {en:'One command (or the repo\'s bundled .mcp.json) — approve once and done.', zh:'一行命令（或用仓库自带的 .mcp.json），点一次允许即可。'},
  'conn.s2.h':     {en:'2 · Ask in your own words', zh:'② 自然语言提问'},
  'conn.s2.t':     {en:'"Are these two records the same person?" — your agent picks the right tool.', zh:'“这两条记录是同一个人吗？”——agent 自己会选对工具。'},
  'conn.s3.h':     {en:'3 · Get verdict + evidence', zh:'③ 拿到判定与证据'},
  'conn.s3.t':     {en:'Real cosine, θ decision line, field diff, ground-truth check.', zh:'真实余弦、θ 判定线、字段对照、真值核验。'},
  'conn.install.h':{en:'Register the server', zh:'注册服务器'},
  'conn.install.cc':{en:'Claude Code — one command', zh:'Claude Code —— 一行命令'},
  'conn.install.repo':{en:'…or simply open this repo in Claude Code: the bundled .mcp.json offers the server — approve when prompted. Rid-pair scoring works immediately, no token, no network.',
                   zh:'……或者直接用 Claude Code 打开本仓库：自带的 .mcp.json 会提示启用，点允许即可。rid 对打分立即可用——不要令牌、不要联网。'},
  'conn.install.any':{en:'Claude Desktop / any MCP client — server entry', zh:'Claude Desktop / 任意 MCP 客户端 —— 服务器配置'},
  'conn.tools.h':  {en:'Three tools', zh:'三个工具'},
  'conn.tools.name':{en:'Tool', zh:'工具'},
  'conn.tools.args':{en:'Arguments', zh:'参数'},
  'conn.tools.does':{en:'What it does', zh:'做什么'},
  'conn.t1.d':     {en:'Two records → real cosine + θ verdict + field diff + truth check. rid×rid offline; free text needs EMB_TOKEN.', zh:'两条记录 → 真实余弦 + θ 判定 + 字段对照 + 真值核验。rid×rid 离线可用；自由文本需 EMB_TOKEN。'},
  'conn.t2.d':     {en:'Search the 228-record village (name / dob / ssn / address substring) → rids for scoring.', zh:'检索 228 条村记录（姓名/生日/SSN/地址子串）→ 供打分用的 rid。'},
  'conn.t3.d':     {en:'Provenance & config: model, θ origin, mode status, honesty statement.', zh:'口径与配置：模型、θ 来源、模式状态、诚实声明。'},
  'conn.ex.h':     {en:'Try asking (copy into your AI)', zh:'示例提问（复制给你的 AI）'},
  'conn.ex1':      {en:'Find records named morrison in the village, then score the two most alike — same person?', zh:'在案例村找姓 morrison 的记录，挑最像的两条打分：是同一个人吗？'},
  'conn.ex2':      {en:'Score rec-476-dup-0 vs rec-476-dup-1: cosine, above θ or not, and what does Febrl3 truth say?', zh:'给 rec-476-dup-0 和 rec-476-dup-1 打分：余弦多少？过没过 θ？Febrl3 真值怎么说？'},
  'conn.ex3':      {en:'(live mode, needs EMB_TOKEN) Same person? A: "name: spencer green | DOB: 1963-08-13" B: "name: spencxr green | DOB: 1963-08-13"',
                   zh:'（在线模式，需 EMB_TOKEN）这两条是同一个人吗？A：“name: spencer green | DOB: 1963-08-13” B：“name: spencxr green | DOB: 1963-08-13”'},
  'conn.ex4':      {en:'What exactly am I looking at — are the scores real? How was θ chosen?', zh:'我看到的分数是真的吗？θ 是怎么选的？口径是什么？'},
  'conn.mode.h':   {en:'Two scoring modes', zh:'两种打分模式'},
  'conn.mode.off.h':{en:'Offline · village rid pairs', zh:'离线 · 案例村 rid 对'},
  'conn.mode.off.t':{en:'Zero config. Scores come from the pre-sampled real matrix — all 25,878 pairs of the 228-record village. The deployed model is deterministic, so offline equals live; and it never breaks on stage.',
                   zh:'零配置。分数取自已入库的真实分数矩阵——228 条记录两两全量 25,878 对。部署模型输出确定，离线 = 实时；且现场永不掉链子。'},
  'conn.mode.on.h':{en:'Live · free text', zh:'在线 · 自由文本'},
  'conn.mode.on.t':{en:'Set the EMB_TOKEN environment variable, then any two free-text records (mixing in village rids is fine) are scored live by the deployed fine-tuned model — bring your own data, same θ ruler.',
                   zh:'设置 EMB_TOKEN 环境变量后，任意两条自由文本记录（可与 rid 混搭）由部署的微调模型实时打分——出你自己的题，θ 同一把尺子。'},
  'conn.proto':    {en:'Honesty — every score is the fine-tuned model\'s real output (offline: pre-sampled deterministic matrix; live: deployed service). θ = {TH} was picked by link-F1 scan on this village, entity selection blind to scores; a verdict is the model\'s assertion, checked against Febrl3 truth (public benchmark, programmatically generated, no real PII). Village: 228 records / 78 identities / 1,223 look-alike shadow pairs. Full guide: docs/mcp.md.',
                   zh:'诚实口径——所有分数均为微调模型真实输出（离线 = 预取样确定性矩阵；在线 = 部署服务实时）。θ = {TH} 由本村 link-F1 扫描选出，选村对分数盲；判定是模型断言，与 Febrl3 真值核验（公开基准、程序生成、无真实隐私）。案例村：228 条记录 / 78 个身份 / 1,223 对影子相似。完整指南：docs/mcp.md。'},
  'conn.copy':     {en:'copy', zh:'复制'},
  'conn.copied':   {en:'copied ✓', zh:'已复制 ✓'},
  'play.h':        {en:'Playground — pick a pair, call the verdict', zh:'试玩台 · 挑一对，亲手下判定'},
  'play.sub':      {en:'All 25,878 pairs of the 228-record village, scored by the fine-tuned model — pick any two records (or draw a classic), guess first if you dare, then see the real cosine against θ. Fully offline: this page embeds the pre-sampled real matrix, identical to the live service output.',
                   zh:'228 条村记录两两全量 25,878 对，分数全部来自微调模型——任意挑两条（或抽一对名场面），敢的话先盲猜，再看真实余弦对着 θ 的判定。全程离线：页面内嵌预取样真实矩阵，与线上服务输出一致。'},
  'play.none':     {en:'Febrl3 village data is not bundled in this build — the playground needs it. Rebuild after village scores land.', zh:'本构建未打入 Febrl3 案例村数据——试玩台需要它。案例村分数入库后重新构建即可。'},
  'play.preset.h': {en:'Or start from a classic', zh:'名场面一键开打'},
  'play.preset.t': {en:'random true pair', zh:'随机真对'},
  'play.preset.n': {en:'closest stranger', zh:'最像的陌生人'},
  'play.preset.r': {en:'surprise me', zh:'随便来一对'},
  'play.pick.h':   {en:'Pick two records', zh:'挑两条记录'},
  'play.search':   {en:'search name / dob / ssn / address / rid…', zh:'搜姓名 / 生日 / 证件号 / 地址 / rid…'},
  'play.nomatch':  {en:'no match', zh:'没有匹配'},
  'play.empty':    {en:'Pick two records (or hit a preset above) — scoring works offline, no token, no network.', zh:'挑两条记录（或点上面的名场面）——离线即可打分，不要令牌、不要联网。'},
  'play.same':     {en:'That\'s the same record twice — pick two different ones.', zh:'这是同一条记录——换一条吧。'},
  'play.guess.h':  {en:'Your call first: same person?', zh:'先你来判断：是同一个人吗？'},
  'play.guess.link':{en:'LINK', zh:'连'},
  'play.guess.refuse':{en:'REFUSE', zh:'不连'},
  'play.guess.skip':{en:'just show me', zh:'直接看结果'},
  'play.tally':    {en:'you vs model: {OK}/{N}', zh:'你 vs 模型：{OK}/{N}'},
  'play.you':      {en:'you said', zh:'你判的是'},
  'play.verdict.linked':{en:'LINKED — asserted same identity', zh:'LINKED · 断言同一个人'},
  'play.verdict.refused':{en:'REFUSED — not asserted', zh:'REFUSED · 拒绝断言'},
  'play.above':    {en:'above θ', zh:'超 θ'},
  'play.below':    {en:'below θ', zh:'距 θ 不足'},
  'play.truth.same':{en:'Febrl3 truth: same identity', zh:'Febrl3 真值：同一人'},
  'play.truth.diff':{en:'Febrl3 truth: different people', zh:'Febrl3 真值：不同人'},
  'play.truth.ok': {en:'assertion CORRECT', zh:'断言正确'},
  'play.truth.bad':{en:'assertion WRONG', zh:'断言错误'},
  'play.mode':     {en:'score mode: offline pre-sampled real matrix — equals the live service output (free-text scoring lives in the MCP server; see Connect view).',
                   zh:'打分模式：离线预取样真实矩阵——与线上服务输出一致（自由文本打分在 MCP 服务器里，见「接入」视图）。'},
  'play.f.name':   {en:'Name', zh:'姓名'},
  'play.f.dob':    {en:'DOB', zh:'生日'},
  'play.f.address':{en:'Address', zh:'地址'},
  'play.f.locality':{en:'Locality', zh:'地区'},
  'play.f.ssn':    {en:'ID No.', zh:'证件号'},
  'play.open':     {en:'▶ open this pair in the Playground', zh:'▶ 这对去试玩台亲手打'},
  'conn.toplay':   {en:'▶ Try it right on this page — Playground', zh:'▶ 先在本页零安装试一把 —— 试玩台'},
  'footer.note':   {en:'Scripted track: records, relations and scores are synthetic, generated deterministically from seed. Open-benchmark track: real measurements of fine-tuned models on Febrl3. Single-file build — opens offline.',                   zh:'剧本轨：记录、关系与分数均为种子确定性生成的合成数据；开源基准轨：微调模型在 Febrl3 上的真实实测。单文件构建，可离线打开。'},
  'footer.note.open': {en:'Open track: Febrl3 public benchmark (programmatically generated person records, no real PII); fine-tuned scores are real model measurements; pairwise baseline is a literal-similarity proxy. Scripted track available via the header switch. Single-file build — opens offline.',
                   zh:'实测轨：Febrl3 公开基准（程序合成的个人记录，无真实隐私数据）；微调分数为真实模型实测；逐对基线为字面相似度代理。可用头部切换器切回合成剧本轨。单文件构建，可离线打开。'},
};

let LANG = localStorage.getItem('ea_lang')
  || new URLSearchParams(location.search).get('lang') || 'en';
function t(k){ const e = I18N[k]; return e ? (e[LANG] || e.en) : k; }
function bi(obj){ return obj ? (obj[LANG] || obj.en) : ''; }
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function f2(x){ return (x===null||x===undefined) ? '—' : Number(x).toFixed(2); }

const TAU = DATA.meta.threshold;
const REC = DATA.records, ENT = DATA.entities;

function srcName(slug){ const s = DATA.sources[slug]; return s ? (LANG==='zh'?s.zh:s.en) : slug; }
function entName(eid){ const e = ENT[eid]; return e ? (LANG==='zh' ? e.canonical : (e.canonical_latin||e.canonical)) : eid; }
function scenName(k){ const s = DATA.scenarios[k]; return s ? (LANG==='zh'?s.zh:s.en) : k; }
function recMetaHTML(r){
  const bits = [];
  if (r.id_number) bits.push('<span>' + t('f.id') + ' <b class="mono">' + esc(r.id_number) + '</b></span>');
  if (r.dob) bits.push('<span>' + t('f.dob') + ' <b class="mono">' + esc(r.dob) + '</b></span>');
  if (r.pob) bits.push('<span>' + t('f.pob') + ' <b>' + esc(r.pob) + '</b></span>');
  if (r.phone) bits.push('<span>' + t('f.phone') + ' <b class="mono">' + esc(r.phone) + '</b></span>');
  bits.push('<span>' + t('f.source') + ' <b>' + esc(srcName(r.source)) + '</b></span>');
  return bits.join('');
}
function recCardHTML(r, extra){
  return '<div class="card gline qcard"><div class="rec-name">' + esc(r.name) +
    (extra||'') + '</div><div class="fields">' + recMetaHTML(r) + '</div></div>';
}

/* ============================ router ============================ */
document.querySelectorAll('#nav button').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('#nav button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('view-' + b.dataset.view).classList.add('active');
    if (b.dataset.view === 'network') netFit();
  });
});

document.getElementById('langBtn').addEventListener('click', () => setLang(LANG === 'en' ? 'zh' : 'en'));
function setLang(l){
  LANG = l; localStorage.setItem('ea_lang', l);
  document.getElementById('langBtn').textContent = (l === 'en' ? '中文' : 'EN');
  document.documentElement.lang = l;
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  applyTrackChrome();
  renderArena(); renderNetLegend(); netLabels(); renderTrackBar(); renderExplain(); renderScoreboard(); renderOpenBench(); renderPlay(); renderConnect();
}
function applyTrackChrome(){
  /* 按当前数据集轨道刷新徽章与口径文案（诚实标注随轨切换） */
  document.getElementById('headPill').textContent = t(TRACK === 'open' ? 'badge.open' : 'badge.synthetic');
  document.getElementById('thr-note').textContent = TRACK === 'open'
    ? 'θ base ' + OC.baseline.theta.toFixed(3) + ' (proxy) / tuned ' + OC.theta.toFixed(3) + ' · ' + t('thr.at')
    : 'θ = ' + TAU + ' · ' + t('thr.at');
  /* buildInfo 也随轨：open 轨报案例村规模，synth 轨报生成时间戳（合成数据专属） */
  document.getElementById('buildInfo').textContent = (TRACK === 'open' && OC)
    ? Object.keys(OC.records).length + ' rec / ' + OC.stats.components + ' ent · Febrl3'
    : DATA.meta.generated_at + ' · ' + DATA.meta.n_records + ' rec / ' + DATA.meta.n_entities + ' ent';
  document.getElementById('seed-pill').textContent = TRACK === 'open'
    ? t('badge.open') + ' · ' + OC.model
    : t('badge.synthetic') + ' · seed ' + DATA.meta.seed;
  document.getElementById('arenaSub').textContent = t(TRACK === 'open' ? 'arena.sub.open' : 'arena.sub');
  document.getElementById('expSub').textContent = t(TRACK === 'open' ? 'exp.sub.open' : 'exp.sub');
  document.getElementById('sbSub').textContent = t(TRACK === 'open' ? 'sb.sub.open' : 'sb.sub');
  document.getElementById('footNote').textContent = t(TRACK === 'open' ? 'footer.note.open' : 'footer.note');
}

/* ============================ Arena ============================ */
let arenaSel = null;
function openRecMeta(rec){
  /* Febrl 记录字段行（与网络视图档案卡同风格） */
  const f = rec.fields, bits = [];
  if (f.dob) bits.push('<span>' + t('f.dob') + ' <b class="mono">' + esc(f.dob) + '</b></span>');
  if (f.address) bits.push('<span>addr <b class="mono">' + esc(f.address) + '</b></span>');
  if (f.locality) bits.push('<span>' + t('f.pob') + ' <b class="mono">' + esc(f.locality) + '</b></span>');
  if (f.ssn) bits.push('<span>SSN <b class="mono">' + esc(f.ssn) + '</b></span>');
  return bits.join('');
}
function arenaAdapter(){
  /* Arena 双轨适配层（与网络视图 trackAdapter 同模式）：
     synth = 合成剧本（现状数据契约）；open = Febrl3 实测（真实微调分数 vs 字面代理）。 */
  if (TRACK === 'open') {
    const mem = {};
    Object.keys(OC.records).forEach(rid => {
      const e = OC.records[rid].entity;
      (mem[e] = mem[e] || []).push(rid);
    });
    return {
      queries: OC.arena.queries,
      results: (model, q) => q.results[model],
      rec: rid => OC.records[rid],
      recMeta: rec => openRecMeta(rec),
      nameOf: (rec, rid) => (rec.fields.name || rid),
      relevantOf: q => mem[OC.records[q.record_id].entity].filter(m => m !== q.record_id),
      scenName: k => bi(OC.arena.labels[k] || {en: k, zh: k}),
      scenRed: [],
      thr: { baseline: OC.baseline.theta, tuned: OC.theta },
      colSub: { baseline: t('arena.proxy'), tuned: OC.model },
      baseNote: true,   // 基线列挂"代理"诚实口径 tooltip
    };
  }
  return {
    queries: DATA.queries,
    results: (model, q) => DATA.results[model][q.record_id],  /* 合成轨按 record_id 查 */
    rec: rid => REC[rid],
    recMeta: rec => recMetaHTML(rec),
    nameOf: (rec, rid) => rec.name,
    relevantOf: q => ENT[REC[q.record_id].entity_id].members.filter(m => m !== q.record_id),
    scenName: k => scenName(k),
    scenRed: ['similar_entity'],
    thr: { baseline: TAU, tuned: TAU },
    colSub: { baseline: 'off-the-shelf', tuned: 'fine-tuned' },
    baseNote: false,
  };
}
function renderArena(){
  const A = arenaAdapter();
  if (!A.queries.some(x => x.query_id === arenaSel)) arenaSel = A.queries[0].query_id;
  const ql = document.getElementById('qlist');
  ql.innerHTML = '';
  A.queries.forEach(q => {
    const b = document.createElement('button');
    b.className = q.query_id === arenaSel ? 'active' : '';
    b.innerHTML = '<div class="qtitle">' + esc(bi(q.title)) + '</div>' +
      '<div class="qmeta"><span class="chip ' + (A.scenRed.includes(q.scenario) ? 'rd' : 'cy') + '">' +
      esc(A.scenName(q.scenario)) + '</span><span class="mono">' + q.record_id + '</span></div>';
    b.addEventListener('click', () => { arenaSel = q.query_id; renderArena(); });
    ql.appendChild(b);
  });
  const q = A.queries.find(x => x.query_id === arenaSel);
  const r = A.rec(q.record_id);
  const relevant = A.relevantOf(q);

  let html = '<div class="card gline qcard"><div class="rec-name">' + esc(A.nameOf(r, q.record_id)) +
    ' <span class="chip cy" style="margin-left:8px">' + esc(A.scenName(q.scenario)) + '</span></div>' +
    '<div class="fields">' + A.recMeta(r) + '</div>' +
    '<div class="mono" style="opacity:.55;font-size:10.5px;margin-top:6px">' + q.record_id + '</div></div>' +
    '<div class="story">' + esc(bi(q.story)) + '</div><div style="height:14px"></div><div class="duel">';
  ['baseline','tuned'].forEach(model => { html += duelCol(A, model, q, relevant); });
  html += '</div>';
  document.getElementById('arenaMain').innerHTML = html;
}
function duelCol(A, model, q, relevant){
  const isBase = model === 'baseline';
  const accent = isBase ? 'violet' : 'cyan';
  const tau = A.thr[model];
  const res = A.results(model, q);
  const topIds = res.map(h => h.r);
  let asserted = 0;
  res.forEach(h => { if (relevant.includes(h.r) && h.s >= tau) asserted++; });
  const missed = relevant.filter(rid => !topIds.includes(rid));

  let html = '<div><div class="col-head">' +
    '<span class="name" style="color:var(--' + accent + ')">' + (isBase ? t('lg.baseline') : t('lg.tuned')).toUpperCase() + '</span>' +
    '<span class="sub"' + (isBase && A.baseNote ? ' title="' + esc(t('net.open.base.note')) + '"' : '') + '>' +
    esc(A.colSub[model]) + '</span>' +
    '<span class="bar-accent accent-' + (isBase ? 'vi' : 'cy') + '"></span></div>';

  res.forEach((h, i) => {
    const r = A.rec(h.r);
    let cls = 'dormant', chip = 'v-unrel', label = t('v.unrelated');
    if (relevant.includes(h.r)) {
      if (h.s >= tau) { cls = 'linked'; chip = 'v-linked'; label = t('v.linked'); }
      else { cls = 'candidate'; chip = 'v-candidate'; label = t('v.candidate'); }
    } else if (h.s >= tau) {
      // 过线但非真身 = 断言了错误连接（无关路人或相似影子人都算误连）
      cls = 'false-link'; chip = 'v-false'; label = t('v.false');
    }
    html += '<div class="hit ' + cls + '"><div class="row1">' +
      '<span class="rank">#' + (i+1) + '</span><span class="hname">' + esc(A.nameOf(r, h.r)) + '</span>' +
      '<span class="vchip ' + chip + '">' + label + '</span></div>' +
      '<div class="meta">' + A.recMeta(r) + '</div>' +
      '<div class="score"><div class="srow"><span class="num">' + f2(h.s) + '</span>' +
      '<div class="sbar"><div class="fill" style="width:' + Math.round(h.s*100) + '%;background:var(--' + accent + ')"></div>' +
      '<div class="tick" style="left:' + Math.round(tau*100) + '%"></div></div></div></div></div>';
  });

  html += '<div class="col-foot"><span>' + t('arena.asserted') + ' <b style="color:var(--' + accent + ')">' +
    asserted + '</b> ' + t('arena.of') + ' ' + relevant.length + ' ' + t('arena.true-links') + '</span>';
  if (missed.length) {
    html += '<span class="miss">' + t('arena.missed') + ': ' +
      missed.map(rid => '<span class="misschip">' + rid + ' ' + esc(A.nameOf(A.rec(rid), rid)) + '</span>').join('') + '</span>';
  }
  html += '</div></div>';
  return html;
}

/* ============================ Network ============================ */
const OC = DATA.open_cases;          // Febrl3 实测轨数据（缺席 → 仅合成轨可看）
let TRACK = OC ? 'open' : 'synth';   // 全局数据集轨道（四视图共用），默认 Febrl3 实测
let NET = TRACK === 'open' ? OC.network : DATA.network;  // 当前轨道的网络数据
const OPEN_RELS = {lookalike: {en: 'Look-alike tie', zh: '相似纠缠'}};
let netT = 0, netResolved = false, netAnim = null, netMax = 1;
const netNodes = {}, netPos = {};
let svg = null, gSrc, gEB, gEA, gHalo, gNode, gELabel;
let gBL = null, gHaloB = null, gELabelB = null, BASE_ENTS = null;

function openComps() {
  const comps = {};
  OC.components.forEach(c => {
    comps[c.id] = {members: OC.network.comp_members[c.id] || [],
                   pure: c.pure, entities: c.entities};
  });
  return comps;
}
function baselineEnts() {
  /* 基线代理组件表（"微调前"态）：结构与 openComps 一致，坐标/成员来自 baseline.network */
  const m = {};
  (OC.baseline ? OC.baseline.components : []).forEach(c => {
    m[c.id] = {members: OC.baseline.network.comp_members[c.id] || [],
               pure: c.pure, entities: c.entities};
  });
  return m;
}
function trackAdapter() {
  if (TRACK === 'open') {
    return {
      net: OC.network,
      name: rid => (OC.records[rid].fields.name || rid),
      eid: rid => OC.comp_of[rid],
      ent: openComps(),
      entLabel: function(eid) {
        const c = this.ent[eid];
        if (!c) return eid;
        const n = c.members.length;
        const suffix = n > 1 ? ' ×' + n : '';
        return c.pure ? 'E' + c.entities[0] + suffix : 'MIXED' + suffix;
      },
      islandName: k => k.toUpperCase(),
      rels: OPEN_RELS,
    };
  }
  return {
    net: DATA.network,
    name: rid => REC[rid].name,
    eid: (rid, beforeNode) => beforeNode.e,
    ent: ENT,
    entLabel: eid => entName(eid),
    islandName: k => srcName(k).toUpperCase(),
    rels: DATA.rels || {},
  };
}
function switchNetTrack(track) {
  /* 全局数据集切换器（函数名保留：历史测试标记锁定）：synth 合成剧本 | open Febrl3 实测。
     四个视图全部跟随切轨；URL ?track= 同步（可分享/可截图）。 */
  if (track === TRACK) return;
  if (track === 'open' && !OC) return;
  TRACK = track;
  NET = trackAdapter().net;
  netT = 0; netResolved = false; netMax = 1;
  if (netAnim) cancelAnimationFrame(netAnim);
  Object.keys(netNodes).forEach(k => delete netNodes[k]);
  if (svg) svg.remove();
  buildNet();
  netLabels(); renderNetLegend(); netSyncUI();
  renderTrackBar();
  renderArena(); renderExplain(); renderScoreboard(); applyTrackChrome();
  syncTrackParam();
}
function syncTrackParam() {
  try {
    const url = new URL(location.href);
    url.searchParams.set('track', TRACK);
    history.replaceState(null, '', url);
  } catch (e) { /* file:// 下个别浏览器限制 replaceState，忽略 */ }
}

function svgEl(tag, attrs){
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}
function dashFor(rel){
  return {family:'', co_address:'7 4', co_travel:'3 4', shared_phone:'9 3 2 3'}[rel] || '';
}
function lerp(a, b, u){ return a + (b - a) * u; }
function easeIO(u){ return u < .5 ? 4*u*u*u : 1 - Math.pow(-2*u + 2, 3) / 2; }
function smooth(u, a, b){ return Math.max(0, Math.min(1, (u - a) / (b - a))); }

function openIslands() {
  /* 开源轨 before 态的"孤岛"= 影子关系连通分量；标签取簇内主姓 */
  const parent = {};
  const find = x => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
  OC.network.nodes_before.forEach(n => parent[n.r] = n.r);
  OC.network.edges_before.forEach(e => {
    const ra = find(e.a), rb = find(e.b);
    if (ra !== rb) parent[ra] = rb;
  });
  const groups = {};
  OC.network.nodes_before.forEach(n => (groups[find(n.r)] = groups[find(n.r)] || []).push(n.r));
  const labelOf = {};
  Object.keys(groups).forEach(root => {
    const tally = {};
    groups[root].forEach(rid => {
      const nm = (OC.records[rid].fields.name || '?').trim().split(/\s+/);
      const sn = nm.length > 1 ? nm[nm.length - 1] : nm[0];
      tally[sn] = (tally[sn] || 0) + 1;
    });
    labelOf[root] = Object.keys(tally).sort((a, b) => tally[b] - tally[a] || a.localeCompare(b))[0].toUpperCase();
  });
  const rid2label = {};
  Object.keys(groups).forEach(root => groups[root].forEach(rid => rid2label[rid] = labelOf[root]));
  return rid2label;
}

function buildNet(){
  const box = document.getElementById('netbox');
  const A = trackAdapter();
  const openIsl = TRACK === 'open' ? openIslands() : null;
  const hasBase = TRACK === 'open' && !!OC.baseline;   // 三态：原始 → 基线(代理) → 微调
  netMax = hasBase ? 2 : 1;
  BASE_ENTS = hasBase ? baselineEnts() : null;
  svg = svgEl('svg', {viewBox: '0 0 ' + NET.canvas.w + ' ' + NET.canvas.h, 'aria-label': 'network'});
  box.appendChild(svg);
  gSrc = svgEl('g', {}); gEB = svgEl('g', {});
  gBL = hasBase ? svgEl('g', {}) : null;               // 基线断言连线
  gEA = svgEl('g', {});
  gHaloB = hasBase ? svgEl('g', {}) : null;            // 基线组件光环
  gHalo = svgEl('g', {}); gNode = svgEl('g', {});
  gELabelB = hasBase ? svgEl('g', {}) : null;
  gELabel = svgEl('g', {});
  const layers = [gSrc, gEB];
  layers.push.apply(layers, hasBase ? [gBL, gEA, gHaloB, gHalo, gNode, gELabelB, gELabel]
                                    : [gEA, gHalo, gNode, gELabel]);
  svg.append.apply(svg, layers);

  // 来源孤岛标签：取各孤岛的质心 x / 最高点 y，落在孤岛正上方（避免撞节点）
  const bySrc = {};
  NET.nodes_before.forEach(n => {
    const key = openIsl ? openIsl[n.r] : REC[n.r].source;
    (bySrc[key] = bySrc[key] || []).push(n);
  });
  Object.keys(bySrc).forEach(key => {
    const ns = bySrc[key];
    const cx = ns.reduce((s, n) => s + n.x, 0) / ns.length;
    const minY = Math.min.apply(null, ns.map(n => n.y));
    const el = svgEl('text', {x: cx, y: minY - 30, 'class': 'elabel', opacity: .5});
    el.textContent = key.toUpperCase();
    el.dataset.srcLabel = key;
    gSrc.appendChild(el);
  });

  // before 边
  NET.edges_before.forEach(e => {
    const el = svgEl('line', {'class': 'edge', 'stroke-dasharray': dashFor(e.rel)});
    el.dataset.a = e.a; el.dataset.b = e.b; el.dataset.kind = 'before';
    gEB.appendChild(el);
  });
  // 基线层（仅开源轨）：断言连线（红线=误连）+ 组件光环（红=混入异实体）+ 标签
  if (hasBase) {
    const badSet = new Set(OC.baseline.false_links);
    OC.baseline.links.forEach(l => {
      const isBad = badSet.has([l.a, l.b].sort().join('|'));
      const el = svgEl('line', {'class': 'edge'});
      if (isBad) { el.style.stroke = 'var(--red)'; el.style.strokeWidth = '2'; el.style.strokeOpacity = '.85'; }
      else { el.style.stroke = 'var(--edge)'; el.style.strokeWidth = '1.1'; el.style.strokeOpacity = '.3'; }
      el.dataset.a = l.a; el.dataset.b = l.b; el.dataset.kind = 'blink';
      gBL.appendChild(el);
    });
    OC.baseline.network.entities.forEach(en => {
      const meta = BASE_ENTS[en.e];
      const bad = meta && !meta.pure;
      const R = 30 + 4 * en.size;   // 与 layout._halo_radius 保持一致
      const halo = svgEl('circle', {'class': bad ? 'halo bad' : 'halo', r: 4, opacity: 0});
      halo.dataset.R = R; halo.dataset.cx = en.x; halo.dataset.cy = en.y;
      gHaloB.appendChild(halo);
      const n = meta ? meta.members.length : en.size;
      const lab = svgEl('text', {'class': bad ? 'elabel badlabel' : 'elabel',
                                  x: en.lx != null ? en.lx : en.x,
                                  y: en.ly != null ? en.ly : en.y - R - 8, opacity: 0});
      lab.textContent = (bad ? 'MIXED' : 'E' + (meta ? meta.entities[0] : '?')) + (n > 1 ? ' ×' + n : '');
      gELabelB.appendChild(lab);
    });
  }
  // after 边（实体中心之间）
  NET.edges_after.forEach(e => {
    const el = svgEl('line', {'class': 'edge', 'stroke-width': 2, 'stroke-opacity': .6,
      'stroke-dasharray': dashFor(e.rels[0])});
    el.dataset.ea = e.a; el.dataset.eb = e.b; el.dataset.kind = 'after';
    gEA.appendChild(el);
  });
  // 实体光环 + 标签（开源轨：混入异实体的组件标红）
  NET.entities.forEach(en => {
    const R = 30 + 4 * en.size;   // 与 layout._halo_radius 保持一致
    const meta = A.ent[en.e];
    const bad = TRACK === 'open' && meta && !meta.pure;
    const halo = svgEl('circle', {'class': bad ? 'halo bad' : 'halo', r: 4, opacity: 0});
    halo.dataset.eid = en.e; halo.dataset.R = R; halo.dataset.cx = en.x; halo.dataset.cy = en.y;
    gHalo.appendChild(halo);
    const lab = svgEl('text', {'class': bad ? 'elabel badlabel' : 'elabel',
                                x: en.lx != null ? en.lx : en.x,
                                y: en.ly != null ? en.ly : en.y - R - 8, opacity: 0});
    lab.dataset.eid = en.e; gELabel.appendChild(lab);
  });
  // 记录节点（开源轨三态：bx/by 原始 → cx/cy 基线组件 → ax/ay 微调组件）
  const before = {}, after = {}, basePos = {};
  NET.nodes_before.forEach(n => before[n.r] = n);
  NET.nodes_after.forEach(n => after[n.r] = n);
  if (hasBase) OC.baseline.network.nodes_after.forEach(n => basePos[n.r] = n);
  Object.keys(before).forEach(rid => {
    const g = svgEl('g', {});
    const c = svgEl('circle', {'class': 'node', r: 7});
    const ti = svgEl('title', {}); ti.textContent = rid + ' · ' + A.name(rid);
    g.append(c, ti);
    g.dataset.rid = rid;
    g.addEventListener('mouseenter', () => netFocus(rid, true));
    g.addEventListener('mouseleave', () => netFocus(rid, false));
    g.addEventListener('click', () => netSide(rid));
    gNode.appendChild(g);
    netNodes[rid] = {el: g, circle: c, bx: before[rid].x, by: before[rid].y,
                     ax: after[rid].x, ay: after[rid].y,
                     cx: basePos[rid] ? basePos[rid].x : null,
                     cy: basePos[rid] ? basePos[rid].y : null,
                     eid: A.eid(rid, before[rid]),
                     bid: hasBase ? OC.baseline.comp_of[rid] : null};
  });
  netApply(0);
  netSide(null);
}
function netApply(u){
  /* 三态开源轨：u∈[0,2] —— 0 原始 / 1 基线代理组件 / 2 微调组件（两跳插值）；
     合成轨与无基线数据时 u∈[0,1]，行为与原版一致。 */
  const two = netMax === 2;
  const e1 = easeIO(Math.max(0, Math.min(1, u)));
  const e2 = easeIO(Math.max(0, Math.min(1, u - 1)));
  Object.keys(netNodes).forEach(rid => {
    const n = netNodes[rid];
    const mx = n.cx != null ? n.cx : n.ax, my = n.cy != null ? n.cy : n.ay;
    let x, y;
    if (!two || u <= 1) { x = lerp(n.bx, mx, e1); y = lerp(n.by, my, e1); }
    else { x = lerp(mx, n.ax, e2); y = lerp(my, n.ay, e2); }
    n.el.setAttribute('transform', 'translate(' + x + ',' + y + ')');
    netPos[rid] = [x, y];
  });
  const oB = 1 - smooth(u, .12, .45);
  const oHB = two ? smooth(u, .42, .8) * (1 - smooth(u, 1.42, 1.8)) : 0;   // 基线层
  const u2 = two ? u - 1 : u;                                              // 微调层的局部进度
  const oA = smooth(u2, .5, .82), oH = smooth(u2, .42, .8);
  // before 边：跟随节点当前位置（否则 line 无坐标、塌缩在原点不可见）
  gEB.childNodes.forEach(el => {
    const pa = netPos[el.dataset.a], pb = netPos[el.dataset.b];
    if (pa && pb) {
      el.setAttribute('x1', pa[0]); el.setAttribute('y1', pa[1]);
      el.setAttribute('x2', pb[0]); el.setAttribute('y2', pb[1]);
    }
    el.setAttribute('opacity', oB);
  });
  gSrc.childNodes.forEach(el => el.setAttribute('opacity', .5 * oB));
  if (gBL) gBL.childNodes.forEach(el => {   // 基线断言连线：同样跟随节点
    const pa = netPos[el.dataset.a], pb = netPos[el.dataset.b];
    if (pa && pb) {
      el.setAttribute('x1', pa[0]); el.setAttribute('y1', pa[1]);
      el.setAttribute('x2', pb[0]); el.setAttribute('y2', pb[1]);
    }
    el.setAttribute('opacity', oHB);
  });
  gEA.childNodes.forEach(el => {
    const a = NET.entities.find(x => x.e === el.dataset.ea), b = NET.entities.find(x => x.e === el.dataset.eb);
    el.setAttribute('x1', a.x); el.setAttribute('y1', a.y);
    el.setAttribute('x2', b.x); el.setAttribute('y2', b.y);
    el.setAttribute('opacity', oA);
  });
  if (gHaloB) {
    gHaloB.childNodes.forEach(h => {
      h.setAttribute('cx', h.dataset.cx); h.setAttribute('cy', h.dataset.cy);
      h.setAttribute('r', lerp(6, +h.dataset.R, smooth(u, .35, .9)));
      h.setAttribute('opacity', oHB * .95);
    });
    gELabelB.childNodes.forEach(l => l.setAttribute('opacity', oHB));
  }
  gHalo.childNodes.forEach(h => {
    h.setAttribute('cx', h.dataset.cx); h.setAttribute('cy', h.dataset.cy);
    h.setAttribute('r', lerp(6, +h.dataset.R, smooth(u2, .35, .9)));
    h.setAttribute('opacity', oH * .95);
  });
  gELabel.childNodes.forEach(l => l.setAttribute('opacity', oH));
}
function netStage(){
  return netMax === 2 ? Math.max(0, Math.min(2, Math.round(netT))) : (netResolved ? 1 : 0);
}
function netSyncUI(stage){
  const s = stage != null ? stage : netStage();
  const btn = document.getElementById('resolveBtn');
  const st = document.getElementById('netState');
  st.title = '';
  if (TRACK === 'open' && netMax === 2) {
    btn.textContent = t(['net.open.btn.base', 'net.open.btn.tuned', 'net.unresolve'][s]);
    btn.classList.toggle('off', s === 2);
    st.textContent = t(['net.open.state.before', 'net.open.state.baseline', 'net.open.state.after'][s]);
    if (s === 1) st.title = t('net.open.base.note');   // 诚实口径挂 tooltip
    renderOpenStats(s);
    return;
  }
  btn.textContent = s ? t('net.unresolve') : t('net.resolve');
  btn.classList.toggle('off', !!s);
  if (TRACK === 'open') {
    st.textContent = t(s ? 'net.open.state.after' : 'net.open.state.before');
    renderOpenStats(s);
  } else {
    st.textContent = t(s ? 'net.state.after' : 'net.state.before');
    const sub = document.getElementById('netTrackSub');
    if (sub) sub.innerHTML = '';
  }
}
function renderOpenStats(stage){
  /* 开源轨统计条：按阶段显示真实数字（0 影子对 / 1 基线代理 / 2 微调模型） */
  const sub = document.getElementById('netTrackSub');
  if (!sub) return;
  const cell = (v, cls) => '<b class="mono ' + (cls || '') + '">' + v + '</b>';
  if (stage === 1 && OC.baseline) {
    const b = OC.baseline.stats;
    sub.innerHTML =
      '<span class="ob-sub mono">' + t('ob.baseline.tag') + '</span>' +
      '<span>θ ' + cell(OC.baseline.theta.toFixed(3)) + '</span>' +
      '<span>' + t('ob.asserted') + ' ' + cell(b.asserted) + '</span>' +
      '<span>' + t('ob.false') + ' ' + cell(b.false_links, 'num-bad') + '</span>' +
      '<span>' + t('ob.missed') + ' ' + cell(b.missed_pairs, 'num-bad') + '</span>' +
      '<span>' + t('ob.comps') + ' ' + cell(b.components + '/' + b.pure_components) + '</span>';
    return;
  }
  if (stage === 0) {
    sub.innerHTML =
      '<span>' + t('ob.shadow') + ' ' + cell(NET.edges_before.length) + '</span>' +
      '<span style="opacity:.6">' + t('ob.shadow.note') + '</span>';
    return;
  }
  const s = OC.stats;
  sub.innerHTML =
    '<span class="ob-sub mono">' + esc(OC.model) + '</span>' +
    '<span>' + t('ob.truth') + ' ' + cell(s.truth_pairs) + '</span>' +
    '<span>' + t('ob.asserted') + ' ' + cell(s.asserted) + '</span>' +
    '<span>' + t('ob.false') + ' ' + cell(s.false_links, 'num-bad') + '</span>' +
    '<span>' + t('ob.missed') + ' ' + cell(s.missed_pairs, 'num-bad') + '</span>' +
    '<span>' + t('ob.comps') + ' ' + cell(s.components + '/' + s.pure_components) + '</span>' +
    '<span>θ ' + cell(OC.theta.toFixed(3)) + '</span>';
}
function netToggle(){
  const target = netMax === 2 ? (netStage() >= 2 ? 0 : netStage() + 1)
                              : (netResolved ? 0 : 1);
  if (netMax !== 2) netResolved = !netResolved;
  netSyncUI(target);
  const from = netT, t0 = performance.now(), DUR = 900;
  if (netAnim) cancelAnimationFrame(netAnim);
  const step = now => {
    const u = Math.min(1, (now - t0) / DUR);
    netT = lerp(from, target, u);
    netApply(netT);
    if (u < 1) netAnim = requestAnimationFrame(step);
  };
  netAnim = requestAnimationFrame(step);
}
function netFocus(rid, on){
  if (!on) {
    document.querySelectorAll('#netbox .dim').forEach(el => el.classList.remove('dim'));
    return;
  }
  const eid = netNodes[rid].eid;
  const A = trackAdapter();
  const neigh = new Set([rid]);
  if (TRACK === 'open' && netMax === 2) {
    const stage = netStage();
    if (stage === 1 && BASE_ENTS) {          // 基线态：聚焦基线组件成员
      const ent = BASE_ENTS[netNodes[rid].bid];
      if (ent) ent.members.forEach(m => neigh.add(m));
    } else if (stage === 2) {                // 微调态：聚焦微调组件成员
      (A.ent[eid] ? A.ent[eid].members : []).forEach(m => neigh.add(m));
    } else {                                 // 原始态：影子关系邻居
      NET.edges_before.forEach(e => { if (e.a === rid) neigh.add(e.b); if (e.b === rid) neigh.add(e.a); });
    }
  } else {
    (A.ent[eid] ? A.ent[eid].members : []).forEach(m => neigh.add(m));
    if (netResolved || netT > .5) {
      NET.edges_after.forEach(e => {
        if (e.a === eid && A.ent[e.b]) A.ent[e.b].members.forEach(m => neigh.add(m));
        if (e.b === eid && A.ent[e.a]) A.ent[e.a].members.forEach(m => neigh.add(m));
      });
    } else {
      NET.edges_before.forEach(e => { if (e.a === rid) neigh.add(e.b); if (e.b === rid) neigh.add(e.a); });
    }
  }
  Object.keys(netNodes).forEach(k => {
    if (!neigh.has(k)) netNodes[k].el.classList.add('dim');
  });
}
function netSide(rid){
  const el = document.getElementById('netSide');
  if (!rid) { el.innerHTML = '<div style="color:var(--t3);font-size:12.5px">' + t('net.clickhint') + '</div>'; return; }
  if (TRACK === 'open') return openNetSide(rid);
  const r = REC[rid], ent = ENT[r.entity_id];
  let html = recCardHTML(r);
  html += '<div class="entname">' + t('net.members') + ' — <b>' + esc(entName(r.entity_id)) + '</b> (' +
    ent.members.length + ')</div><div class="members">' +
    ent.members.map(m => '<span class="mono">' + m + '</span> · ' + esc(REC[m].name) + ' <span style="opacity:.6">' +
      REC[m].source_code + '</span>').join('<br>') + '</div>';
  const rels = NET.edges_before.filter(e => e.a === rid || e.b === rid);
  if (rels.length) {
    html += '<div class="rel">' + t('net.rels') + ':<br>' + rels.map(e => {
      const other = e.a === rid ? e.b : e.a;
      return '· <b>' + esc(bi(e.note)) + '</b> — ' + esc(REC[other].name) + ' (' + other + ')';
    }).join('<br>') + '</div>';
  }
  el.innerHTML = html;
}
function openNetSide(rid){
  /* 开源轨档案卡：字段 + 模型归入的组件（纯/混）+ 真实分数 top-3 与 θ 判定 */
  const el = document.getElementById('netSide');
  const rec = OC.records[rid], f = rec.fields;
  const cid = OC.comp_of[rid];
  const comp = OC.components.find(c => c.id === cid);
  const mems = OC.network.comp_members[cid] || [];
  const bits = [];
  if (f.dob) bits.push('<span>' + t('f.dob') + ' <b class="mono">' + esc(f.dob) + '</b></span>');
  if (f.address) bits.push('<span>addr <b class="mono">' + esc(f.address) + '</b></span>');
  if (f.locality) bits.push('<span>' + t('f.pob') + ' <b class="mono">' + esc(f.locality) + '</b></span>');
  if (f.ssn) bits.push('<span>SSN <b class="mono">' + esc(f.ssn) + '</b></span>');
  bits.push('<span>' + t('ob.trueEnt') + ' <b class="mono">E' + esc(rec.entity) + '</b></span>');
  let html = '<div class="card gline qcard"><div class="rec-name">' + esc(f.name || rid) +
    ' <span class="chip ' + (comp.pure ? 'cy' : 'rd') + '" style="margin-left:6px">' +
    (comp.pure ? 'E' + esc(comp.entities[0]) : 'MIXED') + '</span></div>' +
    '<div class="fields">' + bits.join('') + '</div>' +
    '<div class="mono" style="opacity:.55;font-size:10.5px;margin-top:6px">' + rid + '</div></div>';
  html += '<div class="entname">' + t('ob.comp') + ' — <b style="color:var(--' + (comp.pure ? 'cyan-b' : 'red') + ')">' +
    cid + (comp.pure ? '' : ' · ' + t('ob.mixed')) + '</b> (' + mems.length + ')</div><div class="members">' +
    mems.map(m => '<span class="mono">' + m + '</span> · ' + esc(OC.records[m].fields.name || m) +
      ' <span style="opacity:.6">E' + esc(OC.records[m].entity) + '</span>').join('<br>') + '</div>';
  const top = OC.top3[rid] || [];
  if (top.length) {
    html += '<div class="rel">' + t('ob.top3') + ' (θ=' + OC.theta.toFixed(3) + '):<br>' + top.map(x => {
      const o = x[0], s = x[1];
      const same = OC.records[o].entity === rec.entity;
      const over = s >= OC.theta;
      const verdict = same ? (over ? t('v.linked') : t('v.candidate')) : (over ? t('v.false') : t('v.unrelated'));
      const color = same ? 'var(--cyan-b)' : (over ? 'var(--red)' : 'var(--t3)');
      return '· <span class="mono">' + f2(s) + '</span> <b style="color:' + color + '">' + verdict + '</b> — ' +
        esc(OC.records[o].fields.name || o) + ' <span style="opacity:.5">(' + o + ')</span>';
    }).join('<br>') + '</div>';
  }
  el.innerHTML = html;
}
function renderNetLegend(){
  const lg = document.getElementById('netLegend');
  const rels = trackAdapter().rels;
  let html = '';
  html += '<span class="k">' + t('net.lg.record') + '</span>';
  html += '<span class="k">' + t('net.lg.halo') + '</span>';
  if (TRACK === 'open') html += '<span class="k"><span class="lg-bad-halo"></span>' + t('net.open.lg.mixed') + '</span>';
  if (TRACK === 'open' && OC.baseline) html += '<span class="k"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" style="stroke:var(--red)" stroke-width="2"/></svg>' + t('net.open.lg.blink.bad') + '</span>';
  html += '<span class="k">' + t(TRACK === 'open' ? 'net.open.lg.island' : 'net.lg.src') + '</span>';
  for (const rel in rels) {
    html += '<span class="k"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" ' +
      'style="stroke:var(--edge)" stroke-width="1.6" stroke-dasharray="' + dashFor(rel) + '"/></svg>' +
      esc(LANG === 'zh' ? rels[rel].zh : rels[rel].en) + '</span>';
  }
  lg.innerHTML = html;
  netSyncUI();
}
function netLabels(){
  if (!gELabel) return;
  const A = trackAdapter();
  gELabel.childNodes.forEach(l => { l.textContent = A.entLabel(l.dataset.eid); });
  gSrc.childNodes.forEach(l => { l.textContent = A.islandName(l.dataset.srcLabel); });
}
function renderTrackBar(){
  /* 全局数据集切换器（header 内）：合成剧本 | Febrl3 实测。
     open_cases 数据未接入时（平台部署修复前）整条隐藏，页面只出合成轨。 */
  const bar = document.getElementById('trackBar');
  if (!bar) return;
  if (!OC) { bar.innerHTML = ''; bar.style.display = 'none'; return; }
  bar.style.display = '';
  const mk = (id, key) => '<button data-track="' + id + '" class="' + (TRACK === id ? 'active' : '') + '">' +
    t(key) + '</button>';
  bar.innerHTML = mk('open', 'net.track.open') + mk('synth', 'net.track.synth');
  bar.querySelectorAll('button').forEach(b => {
    b.addEventListener('click', () => switchNetTrack(b.dataset.track));
  });
}
function netFit(){ /* 预留：resize 适配；SVG viewBox 自动缩放，暂无需处理 */ }

/* ============================ Explain ============================ */
let expSel = null;
const ST_SYM = {exact:'✓', fuzzy:'≈', conflict:'✗', missing:'—'};
function trackPairs(){
  /* 案例卡按轨过滤：synth = P*（剧本双模型卡）| open = R*（Febrl3 实测卡） */
  const pre = TRACK === 'open' ? 'R' : 'P';
  const ps = DATA.pairs.filter(p => p.pair_id.charAt(0) === pre);
  return ps.length ? ps : DATA.pairs;   // 兜底：该轨无卡时全量（降级路径）
}
function renderExplain(){
  const sel = document.getElementById('pairsel');
  sel.innerHTML = '';
  const pairs = trackPairs();
  if (!pairs.some(x => x.pair_id === expSel)) expSel = pairs[0].pair_id;
  pairs.forEach(p => {
    const b = document.createElement('button');
    b.className = p.pair_id === expSel ? 'active' : '';
    b.textContent = p.pair_id + ' · ' + bi(p.title);
    b.addEventListener('click', () => { expSel = p.pair_id; renderExplain(); });
    sel.appendChild(b);
  });
  const p = pairs.find(x => x.pair_id === expSel);
  const a = REC[p.a], b = REC[p.b];

  let html = '<div class="xgrid">' + recCardHTML(a) + recCardHTML(b) + '</div>';
  html += '<div class="card gline ftable"><div class="frow"><span class="flabel"></span><span class="flabel" style="text-align:left">' +
    esc(a.name) + ' <span class="mono" style="opacity:.6">' + p.a + '</span></span><span></span>' +
    '<span class="flabel" style="text-align:left">' + esc(b.name) + ' <span class="mono" style="opacity:.6">' + p.b + '</span></span></div>';
  p.fields.forEach(f => {
    html += '<div class="frow"><span class="flabel">' + esc(bi(f.label)) + '</span>' +
      '<span class="fval' + (f.va === '—' ? ' na' : '') + '">' + esc(f.va) + '</span>' +
      '<span class="fstate st-' + f.state + '" title="' + (f.sim === null ? '' : f.sim) + '">' +
      '<span class="sym">' + ST_SYM[f.state] + '</span> ' + t('st.' + f.state) + '</span>' +
      '<span class="fval' + (f.vb === '—' ? ' na' : '') + '">' + esc(f.vb) + '</span></div>';
  });
  html += '</div>';

  const truthChip = p.truth === 'match'
    ? '<span class="truth-chip tc-match">' + t('exp.truth') + ': ' + t('exp.match') + '</span>'
    : '<span class="truth-chip tc-non">' + t('exp.truth') + ': ' + t('exp.nonmatch') + '</span>';

  html += '<div class="card gline verdict-zone"><div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">' +
    truthChip + '</div>';
  // 实测卡只有微调模型一行（基线无法部署），判定线用该轨道真实 θ
  const rows = p.real
    ? [['tuned', t('lg.tuned').toUpperCase()]]
    : [['baseline', t('lg.baseline').toUpperCase()], ['tuned', t('lg.tuned').toUpperCase()]];
  const thr = p.theta != null ? p.theta : TAU;
  rows.forEach(pair => {
    const model = pair[0], accent = model === 'baseline' ? 'violet' : 'cyan';
    const score = p.scores[model], verdict = p.verdicts[model];
    const isMatch = verdict === 'match';
    const correct = (p.truth === 'match') === isMatch;
    let note;
    if (correct) note = isMatch ? t('exp.ok-link') : t('exp.ok-sep');
    else note = isMatch ? t('exp.bad-false') : t('exp.bad-miss');
    html += '<div class="vrow"><span class="vmodel" style="color:var(--' + accent + ')">' + pair[1] + '</span>' +
      '<div class="srow"><span class="num">' + f2(score) + '</span><div class="sbar" style="height:9px">' +
      '<div class="fill" style="width:' + Math.round(score*100) + '%;background:var(--' + accent + ')"></div>' +
      '<div class="tick" style="left:' + Math.round(thr*100) + '%"></div></div></div>' +
      '<span class="vnote" style="color:' + (correct ? 'var(--t2)' : 'var(--red)') + '">' +
      (correct ? '✓ ' : '✗ ') + note + '</span></div>';
  });
  if (p.real) html += '<div class="vnote" style="color:var(--t3);font-size:12px;margin-top:4px">' +
    t('exp.realonly') + '</div>';
  html += '</div>';
  html += '<div class="story" style="margin-top:12px">' + esc(bi(p.note)) + '</div>';
  if (p.real) html += '<div style="margin-top:10px"><button class="chip cy toplay" data-a="' + p.a +
    '" data-b="' + p.b + '">' + t('play.open') + '</button></div>';
  document.getElementById('explainMain').innerHTML = html;
  document.querySelectorAll('#explainMain .toplay').forEach(function(b){
    b.addEventListener('click', function(){ playOpen(b.dataset.a, b.dataset.b); });
  });
}

/* ============================ Scoreboard ============================ */
function renderScoreboard(){
  /* 双轨分派：open = Febrl3 实测指标（真实微调 vs 字面代理）；synth = 合成剧本指标 */
  if (TRACK === 'open') {
    renderMetricsBoard(OC.metrics.overall, OC.metrics.by_bucket, 'sb.proto.open',
                       k => bi(OC.metrics.bucket_labels[k] || {en: k, zh: k}));
  } else {
    renderMetricsBoard(DATA.metrics.overall, DATA.metrics.by_scenario, 'sb.proto', k => scenName(k));
  }
}
/* ---- 柱状图共用：圆顶方底 path + hover tooltip ---- */
function barPath(x, y, w, h, r){
  /* 数据端（顶）圆角、基线端方 —— dataviz 标记规格 */
  r = Math.max(0, Math.min(r, w / 2, h));
  return 'M' + x + ' ' + (y + h) + 'V' + (y + r) +
         'Q' + x + ' ' + y + ' ' + (x + r) + ' ' + y +
         'H' + (x + w - r) +
         'Q' + (x + w) + ' ' + y + ' ' + (x + w) + ' ' + (y + r) +
         'V' + (y + h) + 'Z';
}
let ctipEl = null;
function bindBarTips(root){
  root.querySelectorAll('rect[data-tip],path[data-tip]').forEach(rc => {
    rc.addEventListener('mouseenter', () => {
      if (!ctipEl) { ctipEl = document.createElement('div'); ctipEl.className = 'ctip'; document.body.appendChild(ctipEl); }
      ctipEl.innerHTML = rc.getAttribute('data-tip');
      ctipEl.style.display = 'block';
    });
    rc.addEventListener('mousemove', ev => {
      if (!ctipEl) return;
      ctipEl.style.left = Math.min(ev.clientX + 14, window.innerWidth - 260) + 'px';
      ctipEl.style.top = (ev.clientY + 16) + 'px';
    });
    rc.addEventListener('mouseleave', () => { if (ctipEl) ctipEl.style.display = 'none'; });
  });
}
function chartLegend(){
  return '<div class="mb-legend">' +
    '<span class="k"><span class="dot" style="background:var(--violet)"></span>' + t('lg.baseline') + '</span>' +
    '<span class="k"><span class="dot" style="background:var(--cyan)"></span>' + t('lg.tuned') + '</span></div>';
}
function renderMetricsBoard(o, scs, protoKey, labelOf){
  const tiles = [
    ['sb.linkrecall', 'link_recall', false],
    ['sb.linkprec', 'link_precision', false],
    ['sb.falselink', 'false_link_rate', true],
  ];
  let html = '<div class="tiles">';
  tiles.forEach(tl => {
    const bv = o.baseline[tl[1]], tv = o.tuned[tl[1]];
    const delta = tv - bv;
    const good = tl[2] ? delta < 0 : delta > 0;
    html += '<div class="tile"><div class="tname">' + t(tl[0]) + '</div><div class="nums">' +
      '<span class="n-b"><span class="mk mk-b"></span>' + (bv*100).toFixed(1) + '</span><span class="arrow">⟶</span>' +
      '<span class="n-t"><span class="mk mk-t"></span>' + (tv*100).toFixed(1) + '</span></div>' +
      '<div class="delta ' + (good ? 'up' : 'down') + '">' + (delta >= 0 ? '▲ +' : '▼ ') +
      Math.abs(delta * 100).toFixed(1) + ' pts</div></div>';
  });
  html += '</div>';

  // 分组柱状图（link recall by scenario / bucket）：
  // 细柱圆顶方底、色值走 token、值只标微调列（故事列），基线值在 tooltip 与表格
  const W = 560, H = 252, padL = 38, padB = 46, padT = 20;
  const plotH = H - padT - padB;
  let chart = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%">';
  [0, .5, 1].forEach(gy => {
    const y = padT + plotH * (1 - gy);
    chart += '<line x1="' + padL + '" y1="' + y + '" x2="' + (W-8) + '" y2="' + y +
      '" stroke="var(--' + (gy === 0 ? 'line2' : 'line') + ')" stroke-width="1"/>' +
      '<text class="ax-t" x="' + (padL-6) + '" y="' + (y+3.5) + '" text-anchor="end">' + (gy*100) + '</text>';
  });
  const bw = 16, gapIn = 4;
  const groupW = (W - padL - 8) / scs.length;
  scs.forEach((s, i) => {
    const cx = padL + groupW * i + groupW / 2;
    const lbl = esc(labelOf(s.scenario));
    [[s.baseline.link_recall, 0], [s.tuned.link_recall, 1]].forEach(pr => {
      const v = pr[0], j = pr[1];
      if (v === null || v === undefined) return;
      const h = Math.max(plotH * v, 1.5);   // 0 值也留一道发丝痕（"在测但为0"）
      const x = cx - bw - gapIn/2 + j * (bw + gapIn);
      chart += '<path class="bar-mark ' + (j ? 'mb-t' : 'mb-b') + '" d="' +
        barPath(x, H - padB - h, bw, h, 4) +
        '" data-tip="<span class=m>' + lbl + ' · ' + esc(j ? t('lg.tuned') : t('lg.baseline')) +
        '</span><br><span class=v>' + (v*100).toFixed(1) + '</span>"/>';
      if (j) chart += '<text class="cap-t" x="' + (x + bw/2) + '" y="' + (H - padB - h - 5) +
        '" text-anchor="middle">' + (v*100).toFixed(0) + '</text>';
    });
    chart += '<text class="ax-cat" x="' + cx + '" y="' + (H - padB + 16) + '" text-anchor="middle">' +
      lbl.split(' ')[0] + '</text>' +
      '<text class="ax-sub" x="' + cx + '" y="' + (H - padB + 29) + '" text-anchor="middle">n=' + s.n + '</text>';
  });
  chart += '</svg>';

  let table = '<table class="mtable"><tr><th>' + t('sb.col.sc') + '</th><th>' + t('sb.col.n') + '</th><th>' +
    t('sb.col.r5') + '</th><th>' + t('sb.col.lr') + '</th><th>' + t('sb.col.fl') + '</th></tr>';
  scs.forEach(s => {
    const b = s.baseline, tu = s.tuned;
    const cell = (x) => (x === null || x === undefined) ? '—' : (x*100).toFixed(0);
    const lrB = b.link_recall, lrT = tu.link_recall;
    table += '<tr><td>' + esc(labelOf(s.scenario)) + '</td><td class="num">' + s.n + '</td>' +
      '<td class="num">' + cell(b['recall@5']) + ' ⟶ ' + cell(tu['recall@5']) + '</td>' +
      '<td class="num"><span' + (lrB !== null && lrT > lrB ? ' class="best"' : '') + '>' + cell(lrB) +
      ' ⟶ ' + cell(lrT) + '</span></td>' +
      '<td class="num">' + (b.false_link_rate !== null && b.false_link_rate !== undefined
        ? '<span class="worst">' + cell(b.false_link_rate) + '</span> ⟶ <span class="best">' + cell(tu.false_link_rate) + '</span>'
        : '—') + '</td></tr>';
  });
  table += '</table><div class="protocol">' + t(protoKey) + '</div>';

  html += '<div class="sb-grid"><div class="card"><div class="chart-head"><span class="eyebrow">' +
    t('sb.chart') + '</span>' + chartLegend() + '</div>' +
    '<div class="chartwrap">' + chart + '</div></div>' +
    '<div class="card"><div class="chart-head"><span class="eyebrow">' + t('sb.table') + '</span></div>' +
    table + '</div></div>';
  const host = document.getElementById('scoreMain');
  host.innerHTML = html;
  bindBarTips(host);
}

/* ============== 开源基准轨（真实模型 × Febrl3，实测数字） ============== */
function renderOpenBench(){
  const ms = DATA.open_bench.models;
  // 柱状图：NDCG@10 前(violet)/后(cyan)；轴 90–100（图题已声明截断）
  // 圆顶方底细柱（≤24px）、色值走 token、值只标微调后列，前值在 tooltip 与表格
  const W = 560, H = 240, padL = 38, padB = 40, padT = 18, Y0 = 0.9, Y1 = 1.0;
  const yv = v => padT + (H - padT - padB) * (1 - (v - Y0) / (Y1 - Y0));
  let chart = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%">';
  [0.9, 0.95, 1.0].forEach(gy => {
    chart += '<line x1="' + padL + '" y1="' + yv(gy) + '" x2="' + (W-8) + '" y2="' + yv(gy) +
      '" stroke="var(--' + (gy === 0.9 ? 'line2' : 'line') + ')" stroke-width="1"/>' +
      '<text class="ax-t" x="' + (padL-6) + '" y="' + (yv(gy)+3.5) + '" text-anchor="end">' + (gy*100) + '</text>';
  });
  const bw = 20, gapIn = 4, groupW = (W - padL - 8) / ms.length;
  ms.forEach((m, i) => {
    const cx = padL + groupW * i + groupW / 2;
    [[m.before.ndcg10, 0], [m.after.ndcg10, 1]].forEach(pair => {
      const v = pair[0], j = pair[1];
      const x = cx - bw - gapIn/2 + j * (bw + gapIn), top = yv(v);
      const tip = '<span class=m>' + esc(m.key) + ' · ' + esc(j ? t('lg.tuned') : t('lg.baseline')) +
                  '</span><br><span class=v>NDCG@10 ' + (v*100).toFixed(2) + '</span>';
      chart += '<path class="bar-mark ' + (j ? 'ob-t' : 'ob-b') + '" d="' +
        barPath(x, top, bw, H - padB - top, 4) + '" data-tip="' + tip + '"/>';
      if (j) chart += '<text class="cap-t" x="' + (x + bw/2) + '" y="' + (top - 5) +
        '" text-anchor="middle">' + (v*100).toFixed(1) + '</text>';
    });
    chart += '<text class="ax-cat" x="' + cx + '" y="' + (H - padB + 16) + '" text-anchor="middle">' + esc(m.key) + '</text>';
  });
  chart += '</svg>';

  const mcols = [['ndcg10','NDCG@10'], ['ndcg5','NDCG@5'], ['recall5','Recall@5'],
                 ['recall10','Recall@10'], ['map10','MAP@10']];
  let table = '<table class="mtable"><tr><th>' + t('ob.col.model') + '</th>';
  mcols.forEach(c => table += '<th>' + c[1] + '</th>');
  table += '<th>' + t('ob.col.wtl') + '</th></tr>';
  ms.forEach(m => {
    table += '<tr><td><span class="mono" style="font-size:11px">' + esc(m.base_model) + '</span><br>' +
      '<span style="color:var(--cyan-b);font-size:11px">⟶ ' + esc(m.tuned_model) + '</span></td>';
    mcols.forEach((c, ci) => {
      const d = m.after[c[0]] - m.before[c[0]];
      table += '<td class="num"><span class="ob-num-b">' + m.before[c[0]].toFixed(3) + '</span> ⟶ <span class="ob-num-t">' +
        m.after[c[0]].toFixed(3) + '</span>' +
        (ci === 0 ? ' <span style="color:var(--cyan);font-size:10.5px">(' + (d>=0?'+':'') + (d*100).toFixed(1) + ')</span>' : '') +
        '</td>';
    });
    const w = m.win_tie_loss;
    table += '<td class="num" style="font-size:11px">' + w[0] + '<span style="color:var(--t3)">/</span>' + w[1] +
      '<span style="color:var(--t3)">/</span>' + w[2] + '</td></tr>';
  });
  table += '</table>';

  const html = '<div class="ob-wrap">' +
    '<div class="ob-h2">' + t('ob.h') + '<span class="pill">' + t('ob.badge') + '</span></div>' +
    '<p class="ob-sub">' + t('ob.sub') + '</p>' +
    '<div class="sb-grid"><div class="card"><div class="chart-head"><span class="eyebrow">' +
    t('ob.chart') + '</span>' + chartLegend() + '</div>' +
    '<div class="chartwrap">' + chart + '</div></div>' +
    '<div class="card"><div class="chart-head"><span class="eyebrow">' +
    t('ob.table') + '</span></div>' + table + '</div></div>' +
    '<div class="protocol">' + t('ob.proto') + '</div></div>';
  const host = document.getElementById('openBench');
  host.innerHTML = html;
  bindBarTips(host);
}

/* ============================ Connect（MCP 接入） ============================ */
let connSeq = 0;
function connCode(code){
  /* 代码/提问块：等宽展示 + 复制按钮（离线可用：clipboard 失败回退 execCommand） */
  const id = 'cb-' + (++connSeq);
  return '<div class="codeblock"><button class="copybtn" data-copy="' + id + '">' +
    t('conn.copy') + '</button><pre><code id="' + id + '">' + esc(code) + '</code></pre></div>';
}
function renderConnect(){
  const el = document.getElementById('connectMain');
  const cmd = 'claude mcp add entity-er -- python3 scripts/er_mcp_server.py';
  const cfg = '{\n  "mcpServers": {\n    "entity-er": {\n' +
    '      "command": "python3",\n      "args": ["scripts/er_mcp_server.py"]\n    }\n  }\n}';
  const th = (typeof OC !== 'undefined' && OC) ? OC.theta.toFixed(3) : '0.921';

  let html = '<div class="conn-steps">';
  ['conn.s1', 'conn.s2', 'conn.s3'].forEach(function(k){
    html += '<div class="card conn-step"><div class="sh">' + t(k + '.h') + '</div>' +
      '<div class="st">' + t(k + '.t') + '</div></div>';
  });
  html += '</div>';

  html += '<div style="margin:2px 0 14px"><button id="connPlay" class="pl-preset" style="color:var(--cyan-b);border-color:rgba(72,197,208,.4)">' +
    t('conn.toplay') + '</button></div>';

  const install = '<div class="card"><div class="conn-h2">' + t('conn.install.h') + '</div>' +
    '<div class="conn-h3" style="margin-top:0">' + t('conn.install.cc') + '</div>' + connCode(cmd) +
    '<p class="conn-note">' + t('conn.install.repo') + '</p>' +
    '<div class="conn-h3">' + t('conn.install.any') + '</div>' + connCode(cfg) + '</div>';

  let table = '<table class="mtable"><tr><th>' + t('conn.tools.name') + '</th><th>' +
    t('conn.tools.args') + '</th><th>' + t('conn.tools.does') + '</th></tr>';
  [['er_score_pair', 'a, b', 'conn.t1.d'], ['er_find_records', 'q?, limit?', 'conn.t2.d'],
   ['er_about', '—', 'conn.t3.d']].forEach(function(row){
    table += '<tr><td><span class="mono" style="color:var(--cyan-b)">' + row[0] + '</span></td>' +
      '<td class="mono" style="font-size:11px">' + row[1] + '</td><td>' + t(row[2]) + '</td></tr>';
  });
  table += '</table>';
  const tools = '<div class="card"><div class="conn-h2">' + t('conn.tools.h') + '</div>' + table + '</div>';

  html += '<div class="conn-grid">' + install + tools + '</div>';

  html += '<div class="card" style="margin-bottom:16px"><div class="conn-h2">' + t('conn.ex.h') + '</div>' +
    '<div class="conn-ex">' + connCode(t('conn.ex1')) + connCode(t('conn.ex2')) +
    connCode(t('conn.ex3')) + connCode(t('conn.ex4')) + '</div></div>';

  html += '<div class="card" style="margin-bottom:4px"><div class="conn-h2">' + t('conn.mode.h') + '</div>' +
    '<div class="conn-grid" style="margin-bottom:0">' +
    '<div><div class="sh" style="font-size:13px;font-weight:650;color:var(--cyan-b)">' + t('conn.mode.off.h') + '</div>' +
    '<p class="conn-note">' + t('conn.mode.off.t') + '</p></div>' +
    '<div><div class="sh" style="font-size:13px;font-weight:650;color:var(--violet-b)">' + t('conn.mode.on.h') + '</div>' +
    '<p class="conn-note">' + t('conn.mode.on.t') + '</p></div></div></div>';

  html += '<div class="protocol">' + t('conn.proto').split('{TH}').join(th) + '</div>';
  el.innerHTML = html;
}
function connCopy(btn){
  const code = document.getElementById(btn.dataset.copy);
  const txt = code ? code.textContent : '';
  const done = function(ok){
    btn.textContent = ok ? t('conn.copied') : '✗';
    btn.classList.toggle('ok', ok);
    setTimeout(function(){ btn.textContent = t('conn.copy'); btn.classList.remove('ok'); }, 1600);
  };
  const fallback = function(){
    const ta = document.createElement('textarea');
    ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta); done(ok);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(function(){ done(true); }, fallback);
  } else { fallback(); }
}

/* ============================ Play（试玩台：页面内亲手打分） ============================ */
let playA = null, playB = null, playRevealed = false, playSaid = null;
let playTally = {n: 0, ok: 0};
const playQ = {a: '', b: ''};
let playC = null;   // 解码缓存：{order,pos,u16,n,lo}
function playData(){
  /* OC.play：rid 排序上三角行主序的 u16 量化分数（构建期已断言与全精度判定一致） */
  if (!OC || !OC.play) return null;
  if (playC) return playC;
  const bin = atob(OC.play.b64), u = new Uint16Array(bin.length >> 1);
  for (let i = 0; i < u.length; i++) u[i] = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
  const pos = {};
  OC.play.order.forEach(function(r, i){ pos[r] = i; });
  playC = {order: OC.play.order, pos: pos, u: u, n: OC.play.n, lo: OC.play.lo};
  return playC;
}
function playScore(a, b){
  const P = playData();
  if (!P || P.pos[a] === undefined || P.pos[b] === undefined || a === b) return null;
  let i = P.pos[a], j = P.pos[b];
  if (i > j) { const t = i; i = j; j = t; }
  return P.u[i * (2 * P.n - i - 1) / 2 + (j - i - 1)] / 10000;
}
function levRatio(a, b){
  /* 与后端 _field_state 同口径：fuzzy/conflict 分界 0.7 */
  if (a === b) return 1;
  const la = a.length, lb = b.length;
  let prev = [];
  for (let j = 0; j <= lb; j++) prev[j] = j;
  for (let i = 1; i <= la; i++) {
    const cur = [i];
    for (let j = 1; j <= lb; j++)
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0));
    prev = cur;
  }
  return 1 - prev[lb] / Math.max(la, lb);
}
function plFieldState(va, vb){
  if (!va || !vb) return {state: 'missing', sim: null};
  if (va === vb) return {state: 'exact', sim: 1};
  const sim = Math.round(levRatio(va, vb) * 100) / 100;
  return {state: sim >= 0.7 ? 'fuzzy' : 'conflict', sim: sim};
}
function playSet(a, b){
  playA = a; playB = b; playRevealed = false; playSaid = null;
  renderPlayList('a'); renderPlayList('b'); renderPlayResult();
}
function playOpen(a, b){
  playSet(a, b);
  document.querySelector('#nav button[data-view="play"]').click();
}
function renderPlay(){
  const el = document.getElementById('playMain');
  document.getElementById('playSub').textContent = t('play.sub');
  if (!OC || !OC.play) { el.innerHTML = '<div class="card">' + t('play.none') + '</div>'; return; }
  let html = '<div class="card" style="margin-bottom:14px"><div class="conn-h2">' + t('play.preset.h') +
    '</div><div class="pl-presets">';
  DATA.pairs.filter(function(p){ return ['R1','R2','R3'].indexOf(p.pair_id) >= 0; }).forEach(function(p){
    html += '<button class="pl-preset" data-a="' + p.a + '" data-b="' + p.b + '">' +
      '<span class="mono" style="color:var(--cyan-b)">' + p.pair_id + '</span> ' + esc(bi(p.title)) + '</button>';
  });
  html += '<button class="pl-preset" data-rand="true">' + t('play.preset.t') + '</button>' +
    '<button class="pl-preset" data-rand="near">' + t('play.preset.n') + '</button>' +
    '<button class="pl-preset" data-rand="any">' + t('play.preset.r') + '</button></div></div>';
  html += '<div class="play-grid">' +
    '<div class="card pl-pick"><div class="conn-h2" style="margin-top:0">' + t('play.pick.h') + '</div>' +
    '<div class="pl-side"><div class="pl-lab mono">A</div>' +
    '<input class="pl-search" data-side="a" placeholder="' + t('play.search') + '" value="' + esc(playQ.a) + '">' +
    '<div class="plist" id="plist-a"></div></div>' +
    '<div class="pl-side"><div class="pl-lab mono">B</div>' +
    '<input class="pl-search" data-side="b" placeholder="' + t('play.search') + '" value="' + esc(playQ.b) + '">' +
    '<div class="plist" id="plist-b"></div></div></div>' +
    '<div id="playResult"></div></div>';
  el.innerHTML = html;
  el.querySelectorAll('.pl-search').forEach(function(inp){
    inp.addEventListener('input', function(){ playQ[inp.dataset.side] = inp.value; renderPlayList(inp.dataset.side); });
  });
  el.querySelectorAll('.pl-preset').forEach(function(b){
    b.addEventListener('click', function(){
      const r = b.dataset.rand;
      if (!r) { playSet(b.dataset.a, b.dataset.b); return; }
      const P = playData(), order = P.order;
      const rnd = function(n){ return Math.floor(Math.random() * n); };
      if (r === 'true') {
        const byEnt = {};
        order.forEach(function(rid){ const e = OC.records[rid].entity; (byEnt[e] = byEnt[e] || []).push(rid); });
        const truth = [];
        Object.keys(byEnt).sort().forEach(function(e){
          const m = byEnt[e];
          for (let i = 0; i < m.length; i++) for (let j = i + 1; j < m.length; j++) truth.push([m[i], m[j]]);
        });
        const pick = truth[rnd(truth.length)];
        playSet(pick[0], pick[1]);
      } else if (r === 'near') {
        const a = order[rnd(order.length)];
        let best = null, bs = -1;
        order.forEach(function(x){
          if (x === a || OC.records[x].entity === OC.records[a].entity) return;
          const s = playScore(a, x);
          if (s > bs) { bs = s; best = x; }
        });
        playSet(a, best);
      } else {
        const a = order[rnd(order.length)];
        let b2 = a;
        while (b2 === a) b2 = order[rnd(order.length)];
        playSet(a, b2);
      }
    });
  });
  renderPlayList('a'); renderPlayList('b'); renderPlayResult();
}
function renderPlayList(side){
  const host = document.getElementById('plist-' + side);
  if (!host) return;
  const q = (playQ[side] || '').trim().toLowerCase();
  const sel = side === 'a' ? playA : playB;
  let rows;
  if (q) {
    rows = OC.play.order.filter(function(r){
      return OC.records[r].text.toLowerCase().indexOf(q) >= 0 || r.toLowerCase().indexOf(q) >= 0;
    });
  } else {
    const seenE = {};   // 无搜索词：每实体首条的确定性抽样（与 er_find_records 同款）
    rows = OC.play.order.filter(function(r){
      const e = OC.records[r].entity;
      if (seenE[e]) return false;
      seenE[e] = true; return true;
    });
  }
  const total = rows.length;
  rows = rows.slice(0, 10);
  let html = rows.map(function(r){
    const f = OC.records[r].fields;
    return '<button class="pl-row' + (r === sel ? ' active' : '') + '" data-rid="' + r + '" data-side="' + side + '">' +
      '<span class="pl-rrid mono">' + r + '</span>' +
      '<span class="pl-rname">' + esc(f.name || r) + '</span>' +
      '<span class="pl-rmeta mono">' + [f.dob, f.ssn ? 'SSN ' + f.ssn : null].filter(Boolean).join(' · ') + '</span>' +
      '</button>';
  }).join('');
  if (total > rows.length) html += '<div class="pl-more">' + (total - rows.length) + '+ …</div>';
  if (!rows.length) html = '<div class="pl-more">' + t('play.nomatch') + '</div>';
  host.innerHTML = html;
  host.querySelectorAll('.pl-row').forEach(function(b){
    b.addEventListener('click', function(){
      if (b.dataset.side === 'a') playA = b.dataset.rid; else playB = b.dataset.rid;
      playRevealed = false; playSaid = null;
      renderPlayList('a'); renderPlayList('b'); renderPlayResult();
    });
  });
}
function plRecCard(rec, rid){
  return '<div class="card gline qcard"><div class="rec-name">' + esc(rec.fields.name || rid) +
    ' <span class="chip" style="margin-left:6px">ent ' + rec.entity + '</span></div>' +
    '<div class="fields">' + openRecMeta(rec) + '</div>' +
    '<div class="mono" style="opacity:.55;font-size:10.5px;margin-top:6px">' + rid + '</div></div>';
}
function plFieldTable(ra, rb){
  const KEYS = [['name','play.f.name'],['dob','play.f.dob'],['address','play.f.address'],
                ['locality','play.f.locality'],['ssn','play.f.ssn']];
  let html = '<div class="card gline ftable" style="margin-top:12px">' +
    '<div class="frow"><span class="flabel"></span>' +
    '<span class="flabel" style="text-align:left">' + esc(ra.fields.name || playA) + '</span><span></span>' +
    '<span class="flabel" style="text-align:left">' + esc(rb.fields.name || playB) + '</span></div>';
  KEYS.forEach(function(k){
    const va = ra.fields[k[0]], vb = rb.fields[k[0]];
    const st = plFieldState(va, vb);
    const cell = '<span class="sym">' + ST_SYM[st.state] + '</span> ' +
      (st.sim === null || st.state === 'exact' || st.state === 'missing' ? t('st.' + st.state) : st.sim);
    html += '<div class="frow"><span class="flabel">' + t(k[1]) + '</span>' +
      '<span class="fval' + (va ? '' : ' na') + '">' + esc(va || '—') + '</span>' +
      '<span class="fstate st-' + st.state + '">' + cell + '</span>' +
      '<span class="fval' + (vb ? '' : ' na') + '">' + esc(vb || '—') + '</span></div>';
  });
  return html + '</div>';
}
function renderPlayResult(){
  const host = document.getElementById('playResult');
  if (!host) return;
  if (!playA || !playB) { host.innerHTML = '<div class="card pl-empty">' + t('play.empty') + '</div>'; return; }
  if (playA === playB) { host.innerHTML = '<div class="card pl-empty">' + t('play.same') + '</div>'; return; }
  const ra = OC.records[playA], rb = OC.records[playB];
  const P = playData(), theta = OC.theta;
  let html = '<div class="xgrid">' + plRecCard(ra, playA) + plRecCard(rb, playB) + '</div>';
  html += plFieldTable(ra, rb);
  if (!playRevealed) {
    html += '<div class="card pl-guess" style="margin-top:12px"><div class="sh">' + t('play.guess.h') + '</div>' +
      '<div class="pl-gbtns">' +
      '<button class="pl-g link" data-say="link">' + t('play.guess.link') + '</button>' +
      '<button class="pl-g refuse" data-say="refuse">' + t('play.guess.refuse') + '</button>' +
      '<button class="pl-g skip" data-say="skip">' + t('play.guess.skip') + '</button>' +
      (playTally.n ? '<span class="chip cy" style="align-self:center">' +
        t('play.tally').split('{OK}').join(playTally.ok).split('{N}').join(playTally.n) + '</span>' : '') +
      '</div></div>';
  } else {
    const s = playScore(playA, playB);
    const linked = s >= theta, margin = s - theta;
    const same = ra.entity === rb.entity;
    const correct = linked === same;
    const pct = Math.max(0, Math.min(100, (s - P.lo) / (1 - P.lo) * 100));
    const thPct = Math.max(0, Math.min(100, (theta - P.lo) / (1 - P.lo) * 100));
    html += '<div class="card" style="margin-top:12px">' +
      '<div class="pl-score-row"><span class="pl-big mono">' + s.toFixed(4) + '</span>' +
      '<span class="pl-verdict-chip ' + (correct ? 'ok' : 'bad') + '">' +
      (linked ? t('play.verdict.linked') : t('play.verdict.refused')) + '</span>' +
      '<span class="pl-margin mono">' + (margin >= 0 ? '+' : '') + margin.toFixed(4) + ' ' +
      t(margin >= 0 ? 'play.above' : 'play.below') + '</span></div>' +
      '<div class="pgauge"><div class="pg-fill" style="width:' + pct + '%;background:var(--' +
      (correct ? 'cyan' : 'red') + ')"></div>' +
      '<div class="pg-tick" style="left:' + thPct + '%"><span>θ ' + theta.toFixed(4) + '</span></div></div>' +
      '<div class="pl-truth">' + (same ? t('play.truth.same') : t('play.truth.diff')) + ' — <span style="color:var(--' +
      (correct ? 'cyan-b' : 'red-b') + ');font-weight:650">' + (correct ? '✓ ' + t('play.truth.ok') : '✗ ' + t('play.truth.bad')) +
      '</span></div>' +
      (playSaid ? '<div class="pl-said">' + t('play.you') + '：' +
        (playSaid === 'link' ? t('play.guess.link') : t('play.guess.refuse')) +
        ' · <span class="chip cy">' + t('play.tally').split('{OK}').join(playTally.ok).split('{N}').join(playTally.n) + '</span></div>' : '') +
      '<div class="pl-mode">' + t('play.mode') + '</div></div>';
  }
  host.innerHTML = html;
  host.querySelectorAll('.pl-g').forEach(function(b){
    b.addEventListener('click', function(){
      if (b.dataset.say !== 'skip') {
        playSaid = b.dataset.say;
        const modelSaid = (playScore(playA, playB) >= OC.theta) ? 'link' : 'refuse';
        playTally.n += 1;
        if (playSaid === modelSaid) playTally.ok += 1;
      }
      playRevealed = true;
      renderPlayResult();
    });
  });
}

/* ============================ init ============================ */
function init(){
  // 网络工具栏（在 netbox 上层）
  const box = document.getElementById('netbox');
  const bar = document.createElement('div');
  bar.className = 'netbar';
  bar.innerHTML = '<button class="resolve-btn" id="resolveBtn"></button><span class="netstate" id="netState"></span>';
  box.appendChild(bar);
  buildNet();
  document.getElementById('resolveBtn').addEventListener('click', netToggle);
  DATA.rels = {"family": {"en":"Family","zh":"亲属"}, "co_address": {"en":"Shared address","zh":"同址"},
               "co_travel": {"en":"Co-travel","zh":"同行"}, "shared_phone": {"en":"Shared phone","zh":"共用号码"}};
  // 复制按钮事件委托挂在容器上（重渲染只换 innerHTML，不动容器本身）
  document.getElementById('connectMain').addEventListener('click', function(e){
    const b = e.target.closest('.copybtn');
    if (b) connCopy(b);
    const pl = e.target.closest('#connPlay');
    if (pl) document.querySelector('#nav button[data-view="play"]').click();
  });
  setLang(LANG);
  // ?view= 直达视图 + ?track=open 直达 Febrl3 轨 + ?focus= 直开档案卡（可分享链接）
  const qs = new URLSearchParams(location.search);
  const v = qs.get('view');
  if (v && document.getElementById('view-' + v)) {
    document.querySelector('#nav button[data-view="' + v + '"]').click();
  }
  if (qs.get('track') === 'open' && OC) switchNetTrack('open');
  else if (qs.get('track') === 'synth') switchNetTrack('synth');
  const f = qs.get('focus');
  if (f && netNodes[f]) netSide(f);
  // ?q= 直达 Arena 指定查询（Q*=合成 / F*=Febrl3，自动切轨）、?case= 直达 Explain 指定案例
  const qsel = qs.get('q');
  if (qsel) {
    const want = qsel.charAt(0) === 'F' ? 'open' : 'synth';
    if (want !== TRACK && (want === 'synth' || OC)) switchNetTrack(want);
    if (arenaAdapter().queries.some(x => x.query_id === qsel)) { arenaSel = qsel; renderArena(); }
  }
  const csel = qs.get('case');
  if (csel) {
    const want = csel.charAt(0) === 'R' ? 'open' : 'synth';
    if (want !== TRACK && (want === 'synth' || OC)) switchNetTrack(want);
    if (trackPairs().some(x => x.pair_id === csel)) { expSel = csel; renderExplain(); }
  }
  // ?pair=ridA,ridB 直达试玩台指定对（可分享链接；不带 view 时自动切到试玩台）
  const ppair = qs.get('pair');
  if (ppair && OC && OC.play) {
    const two = ppair.split(',');
    if (two.length === 2 && two[0] !== two[1] && OC.records[two[0]] && OC.records[two[1]]) {
      playA = two[0]; playB = two[1]; playRevealed = true;
      if (!v) document.querySelector('#nav button[data-view="play"]').click();
      renderPlayResult();
    }
  }
  // ?resolve=1 直达"归并后"状态（跳过动画，供截图/分享；三态开源轨直达微调态）
  if (qs.get('resolve')) { netResolved = true; netT = netMax; netApply(netMax); netSyncUI(netMax); }
  // ?stage=0|1|2 直达开源轨三态（原始/基线/微调——截图与现场剧本直达基线态）
  const nst = qs.get('stage');
  if (nst !== null && TRACK === 'open' && +nst >= 0 && +nst <= netMax) {
    netT = +nst; netApply(+nst); netSyncUI(+nst);
  }
}
init();
"""

def render_page(bundle: dict, css: str) -> str:
    data_js = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        TEMPLATE.replace("__CSS__", css)
        .replace("__DATA__", data_js)
        .replace("__JS__", JS)
    )
