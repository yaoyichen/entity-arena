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
    <span class="pill" data-i18n="badge.synthetic"></span>
    <nav id="nav">
      <button data-view="arena" class="active" data-i18n="nav.arena"></button>
      <button data-view="network" data-i18n="nav.network"></button>
      <button data-view="explain" data-i18n="nav.explain"></button>
      <button data-view="scoreboard" data-i18n="nav.scoreboard"></button>
    </nav>
    <button class="lang" id="langBtn">中文</button>
  </div>
</header>
<main>
  <section id="view-arena" class="view active">
    <div class="vhead">
      <h1 data-i18n="arena.h"></h1>
      <p data-i18n="arena.sub"></p>
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
      <p data-i18n="exp.sub"></p>
    </div>
    <div class="pairsel" id="pairsel"></div>
    <div id="explainMain"></div>
  </section>

  <section id="view-scoreboard" class="view">
    <div class="vhead">
      <h1 data-i18n="sb.h"></h1>
      <p data-i18n="sb.sub"></p>
    </div>
    <div id="scoreMain"></div>
    <div id="openBench"></div>
  </section>
</main>
<footer>
  <div class="fin">
    <span class="pill" id="seed-pill"></span>
    <span data-i18n="footer.note"></span>
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
  'nav.arena':     {en:'Arena', zh:'对比台'},
  'nav.network':   {en:'Network', zh:'关系网络'},
  'nav.explain':   {en:'Explain', zh:'匹配解释'},
  'nav.scoreboard':{en:'Scoreboard', zh:'指标板'},
  'lg.tuned':      {en:'Fine-tuned', zh:'微调模型'},
  'lg.baseline':   {en:'Baseline', zh:'基线模型'},
  'lg.false':      {en:'False link', zh:'误连'},
  'thr.at':        {en:'assert threshold', zh:'断言阈值'},

  'arena.h':       {en:'One query, two models', zh:'同一查询，两个模型'},
  'arena.sub':     {en:'Pick a scripted query. Left: off-the-shelf baseline. Right: fine-tuned. Records of the same true identity should be LINKED; look-alikes must NOT be.',
                   zh:'选择一个剧本查询。左栏：开箱基线模型；右栏：微调后模型。同一真实身份的记录应被连接（LINKED）；相似的"影子人"绝不能连上。'},
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

  'exp.h':         {en:'Why did it match — or refuse to', zh:'为什么连上了——或者为什么拒绝'},
  'exp.sub':       {en:'Field-by-field evidence for four decisive pairs. The threshold bar marks θ = 0.55; scores above it are asserted as links.',
                   zh:'四对决定性记录的逐字段证据。阈值线标在 θ = 0.55，过线即断言为同一实体。'},
  'exp.truth':     {en:'Ground truth', zh:'真实标签'},
  'exp.match':     {en:'TRUE MATCH', zh:'真·同一人'},
  'exp.nonmatch':  {en:'TRUE NON-MATCH', zh:'真·不同人'},
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
  'footer.note':   {en:'Scripted track: records, relations and scores are synthetic, generated deterministically from seed. Open-benchmark track: real measurements of fine-tuned models on Febrl3. Single-file build — opens offline.',
                   zh:'剧本轨：记录、关系与分数均为种子确定性生成的合成数据；开源基准轨：微调模型在 Febrl3 上的真实实测。单文件构建，可离线打开。'},
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
  return '<div class="card qcard"><div class="rec-name">' + esc(r.name) +
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
  document.getElementById('thr-note').textContent = 'θ = ' + TAU + ' · ' + t('thr.at');
  document.getElementById('seed-pill').textContent = t('badge.synthetic') + ' · seed ' + DATA.meta.seed;
  document.getElementById('buildInfo').textContent = DATA.meta.generated_at +
    ' · ' + DATA.meta.n_records + ' rec / ' + DATA.meta.n_entities + ' ent';
  renderArena(); renderNetLegend(); netLabels(); renderExplain(); renderScoreboard(); renderOpenBench();
}

/* ============================ Arena ============================ */
let arenaSel = DATA.queries[0].query_id;
function renderArena(){
  const ql = document.getElementById('qlist');
  ql.innerHTML = '';
  DATA.queries.forEach(q => {
    const b = document.createElement('button');
    b.className = q.query_id === arenaSel ? 'active' : '';
    b.innerHTML = '<div class="qtitle">' + esc(bi(q.title)) + '</div>' +
      '<div class="qmeta"><span class="chip ' + (q.scenario==='similar_entity'?'rd':'cy') + '">' +
      esc(scenName(q.scenario)) + '</span><span class="mono">' + q.record_id + '</span></div>';
    b.addEventListener('click', () => { arenaSel = q.query_id; renderArena(); });
    ql.appendChild(b);
  });
  const q = DATA.queries.find(x => x.query_id === arenaSel);
  const r = REC[q.record_id];
  const ent = ENT[r.entity_id];
  const relevant = ent.members.filter(m => m !== r.record_id);

  let html = recCardHTML(r, ' <span class="chip cy" style="margin-left:8px">' + esc(scenName(q.scenario)) + '</span>') +
    '<div class="story">' + esc(bi(q.story)) + '</div><div style="height:14px"></div><div class="duel">';
  ['baseline','tuned'].forEach(model => { html += duelCol(model, r, relevant); });
  html += '</div>';
  document.getElementById('arenaMain').innerHTML = html;
}
function duelCol(model, qrec, relevant){
  const isBase = model === 'baseline';
  const accent = isBase ? 'violet' : 'cyan';
  const res = DATA.results[model][qrec.record_id];
  const topIds = res.map(h => h.r);
  let asserted = 0;
  res.forEach(h => { if (relevant.includes(h.r) && h.s >= TAU) asserted++; });
  const missed = relevant.filter(rid => !topIds.includes(rid));

  let html = '<div><div class="col-head">' +
    '<span class="name" style="color:var(--' + accent + ')">' + (isBase ? t('lg.baseline') : t('lg.tuned')).toUpperCase() + '</span>' +
    '<span class="sub">' + (isBase ? 'off-the-shelf' : 'fine-tuned') + '</span>' +
    '<span class="bar-accent accent-' + (isBase ? 'vi' : 'cy') + '"></span></div>';

  res.forEach((h, i) => {
    const r = REC[h.r];
    let cls = 'dormant', chip = 'v-unrel', label = t('v.unrelated');
    if (relevant.includes(h.r)) {
      if (h.s >= TAU) { cls = 'linked'; chip = 'v-linked'; label = t('v.linked'); }
      else { cls = 'candidate'; chip = 'v-candidate'; label = t('v.candidate'); }
    } else if (h.s >= TAU) {
      // 过线但非真身 = 断言了错误连接（无关路人或相似影子人都算误连）
      cls = 'false-link'; chip = 'v-false'; label = t('v.false');
    }
    html += '<div class="hit ' + cls + '"><div class="row1">' +
      '<span class="rank">#' + (i+1) + '</span><span class="hname">' + esc(r.name) + '</span>' +
      '<span class="vchip ' + chip + '">' + label + '</span></div>' +
      '<div class="meta">' + recMetaHTML(r) + '</div>' +
      '<div class="score"><div class="srow"><span class="num">' + f2(h.s) + '</span>' +
      '<div class="sbar"><div class="fill" style="width:' + Math.round(h.s*100) + '%;background:var(--' + accent + ')"></div>' +
      '<div class="tick" style="left:' + Math.round(TAU*100) + '%"></div></div></div></div></div>';
  });

  html += '<div class="col-foot"><span>' + t('arena.asserted') + ' <b style="color:var(--' + accent + ')">' +
    asserted + '</b> ' + t('arena.of') + ' ' + relevant.length + ' ' + t('arena.true-links') + '</span>';
  if (missed.length) {
    html += '<span class="miss">' + t('arena.missed') + ': ' +
      missed.map(rid => '<span class="misschip">' + rid + ' ' + esc(REC[rid].name) + '</span>').join('') + '</span>';
  }
  html += '</div></div>';
  return html;
}

/* ============================ Network ============================ */
const NET = DATA.network;
let netT = 0, netResolved = false, netAnim = null;
const netNodes = {}, netPos = {};
let svg = null, gSrc, gEB, gEA, gHalo, gNode, gELabel;

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

function buildNet(){
  const box = document.getElementById('netbox');
  svg = svgEl('svg', {viewBox: '0 0 ' + NET.canvas.w + ' ' + NET.canvas.h, 'aria-label': 'network'});
  box.appendChild(svg);
  gSrc = svgEl('g', {}); gEB = svgEl('g', {}); gEA = svgEl('g', {});
  gHalo = svgEl('g', {}); gNode = svgEl('g', {}); gELabel = svgEl('g', {});
  svg.append(gSrc, gEB, gEA, gHalo, gNode, gELabel);

  // 来源孤岛标签：取各孤岛的质心 x / 最高点 y，落在孤岛正上方（避免撞节点）
  const bySrc = {};
  NET.nodes_before.forEach(n => {
    const key = REC[n.r].source;
    (bySrc[key] = bySrc[key] || []).push(n);
  });
  Object.keys(bySrc).forEach(key => {
    const ns = bySrc[key];
    const cx = ns.reduce((s, n) => s + n.x, 0) / ns.length;
    const minY = Math.min.apply(null, ns.map(n => n.y));
    const el = svgEl('text', {x: cx, y: minY - 30, 'class': 'elabel', opacity: .5});
    el.textContent = srcName(key).toUpperCase();
    el.dataset.srcLabel = key;
    gSrc.appendChild(el);
  });

  // before 边
  NET.edges_before.forEach(e => {
    const el = svgEl('line', {'class': 'edge', 'stroke-dasharray': dashFor(e.rel)});
    el.dataset.a = e.a; el.dataset.b = e.b; el.dataset.kind = 'before';
    gEB.appendChild(el);
  });
  // after 边（实体中心之间）
  NET.edges_after.forEach(e => {
    const el = svgEl('line', {'class': 'edge', 'stroke-width': 2, 'stroke-opacity': .6,
      'stroke-dasharray': dashFor(e.rels[0])});
    el.dataset.ea = e.a; el.dataset.eb = e.b; el.dataset.kind = 'after';
    gEA.appendChild(el);
  });
  // 实体光环 + 标签
  NET.entities.forEach(en => {
    const R = 24 + 7 * (en.size + 1) + 12;
    const halo = svgEl('circle', {'class': 'halo', r: 4, opacity: 0});
    halo.dataset.eid = en.e; halo.dataset.R = R; halo.dataset.cx = en.x; halo.dataset.cy = en.y;
    gHalo.appendChild(halo);
    const lab = svgEl('text', {'class': 'elabel', x: en.x, y: en.y - R - 8, opacity: 0});
    lab.dataset.eid = en.e; gELabel.appendChild(lab);
  });
  // 记录节点
  const before = {}, after = {};
  NET.nodes_before.forEach(n => before[n.r] = n);
  NET.nodes_after.forEach(n => after[n.r] = n);
  Object.keys(before).forEach(rid => {
    const g = svgEl('g', {});
    const c = svgEl('circle', {'class': 'node', r: 7});
    const ti = svgEl('title', {}); ti.textContent = rid + ' · ' + REC[rid].name;
    g.append(c, ti);
    g.dataset.rid = rid;
    g.addEventListener('mouseenter', () => netFocus(rid, true));
    g.addEventListener('mouseleave', () => netFocus(rid, false));
    g.addEventListener('click', () => netSide(rid));
    gNode.appendChild(g);
    netNodes[rid] = {el: g, circle: c, bx: before[rid].x, by: before[rid].y,
                     ax: after[rid].x, ay: after[rid].y, eid: before[rid].e};
  });
  netApply(0);
  netSide(null);
}
function netApply(u){
  const e = easeIO(u);
  Object.keys(netNodes).forEach(rid => {
    const n = netNodes[rid];
    const x = lerp(n.bx, n.ax, e), y = lerp(n.by, n.ay, e);
    n.el.setAttribute('transform', 'translate(' + x + ',' + y + ')');
    netPos[rid] = [x, y];
  });
  const oB = 1 - smooth(u, .12, .45), oA = smooth(u, .5, .82), oH = smooth(u, .42, .8);
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
  gEA.childNodes.forEach(el => {
    const a = NET.entities.find(x => x.e === el.dataset.ea), b = NET.entities.find(x => x.e === el.dataset.eb);
    el.setAttribute('x1', a.x); el.setAttribute('y1', a.y);
    el.setAttribute('x2', b.x); el.setAttribute('y2', b.y);
    el.setAttribute('opacity', oA);
  });
  gHalo.childNodes.forEach(h => {
    h.setAttribute('cx', h.dataset.cx); h.setAttribute('cy', h.dataset.cy);
    h.setAttribute('r', lerp(6, +h.dataset.R, smooth(u, .35, .9)));
    h.setAttribute('opacity', oH * .95);
  });
  gELabel.childNodes.forEach(l => l.setAttribute('opacity', oH));
}
function netSyncUI(){
  const btn = document.getElementById('resolveBtn');
  if (btn) btn.textContent = netResolved ? t('net.unresolve') : t('net.resolve');
  btn.classList.toggle('off', netResolved);
  const st = document.getElementById('netState');
  st.textContent = netResolved ? t('net.state.after') : t('net.state.before');
}
function netToggle(){
  netResolved = !netResolved;
  netSyncUI();
  const target = netResolved ? 1 : 0, from = netT, t0 = performance.now(), DUR = 900;
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
  const neigh = new Set([rid]);
  ENT[eid].members.forEach(m => neigh.add(m));
  if (netResolved || netT > .5) {
    NET.edges_after.forEach(e => {
      if (e.a === eid) ENT[e.b].members.forEach(m => neigh.add(m));
      if (e.b === eid) ENT[e.a].members.forEach(m => neigh.add(m));
    });
  } else {
    NET.edges_before.forEach(e => { if (e.a === rid) neigh.add(e.b); if (e.b === rid) neigh.add(e.a); });
  }
  Object.keys(netNodes).forEach(k => {
    if (!neigh.has(k)) netNodes[k].el.classList.add('dim');
  });
}
function netSide(rid){
  const el = document.getElementById('netSide');
  if (!rid) { el.innerHTML = '<div style="color:var(--t3);font-size:12.5px">' + t('net.clickhint') + '</div>'; return; }
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
function renderNetLegend(){
  const lg = document.getElementById('netLegend');
  const rels = Object.keys(DATA.rels || {});
  let html = '';
  [['net.lg.record',''], ['net.lg.halo','']].forEach(pair => {
    html += '<span class="k">' + t(pair[0]) + '</span>';
  });
  html += '<span class="k">' + t('net.lg.src') + '</span>';
  for (const rel in (DATA.rels || {})) {
    html += '<span class="k"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" ' +
      'stroke="#7aa7c9" stroke-width="1.6" stroke-dasharray="' + dashFor(rel) + '"/></svg>' +
      esc(LANG === 'zh' ? DATA.rels[rel].zh : DATA.rels[rel].en) + '</span>';
  }
  lg.innerHTML = html;
  netSyncUI();
}
function netLabels(){
  if (!gELabel) return;
  gELabel.childNodes.forEach(l => { l.textContent = entName(l.dataset.eid); });
  gSrc.childNodes.forEach(l => { l.textContent = srcName(l.dataset.srcLabel).toUpperCase(); });
}
function netFit(){ /* 预留：resize 适配；SVG viewBox 自动缩放，暂无需处理 */ }

/* ============================ Explain ============================ */
let expSel = DATA.pairs[0].pair_id;
const ST_SYM = {exact:'✓', fuzzy:'≈', conflict:'✗', missing:'—'};
function renderExplain(){
  const sel = document.getElementById('pairsel');
  sel.innerHTML = '';
  DATA.pairs.forEach(p => {
    const b = document.createElement('button');
    b.className = p.pair_id === expSel ? 'active' : '';
    b.textContent = p.pair_id + ' · ' + bi(p.title);
    b.addEventListener('click', () => { expSel = p.pair_id; renderExplain(); });
    sel.appendChild(b);
  });
  const p = DATA.pairs.find(x => x.pair_id === expSel);
  const a = REC[p.a], b = REC[p.b];

  let html = '<div class="xgrid">' + recCardHTML(a) + recCardHTML(b) + '</div>';
  html += '<div class="card ftable"><div class="frow"><span class="flabel"></span><span class="flabel" style="text-align:left">' +
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

  html += '<div class="card verdict-zone"><div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">' +
    truthChip + '</div>';
  [['baseline', t('lg.baseline').toUpperCase()], ['tuned', t('lg.tuned').toUpperCase()]].forEach(pair => {
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
      '<div class="tick" style="left:' + Math.round(TAU*100) + '%"></div></div></div>' +
      '<span class="vnote" style="color:' + (correct ? 'var(--t2)' : 'var(--red)') + '">' +
      (correct ? '✓ ' : '✗ ') + note + '</span></div>';
  });
  html += '</div>';
  html += '<div class="story" style="margin-top:12px">' + esc(bi(p.note)) + '</div>';
  document.getElementById('explainMain').innerHTML = html;
}

/* ============================ Scoreboard ============================ */
function renderScoreboard(){
  const o = DATA.metrics.overall;
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
    const dTxt = (delta >= 0 ? '+' : '') + (delta * 100).toFixed(1) + ' pts';
    html += '<div class="tile"><div class="tname">' + t(tl[0]) + '</div><div class="nums">' +
      '<span class="n-b">' + (bv*100).toFixed(1) + '</span><span class="arrow">⟶</span>' +
      '<span class="n-t">' + (tv*100).toFixed(1) + '</span></div>' +
      '<div class="delta ' + (good ? 'up' : 'down') + '">' + dTxt + (good ? ' ↓better' : '') + '</div></div>';
  });
  html += '</div>';

  // 分组柱状图（link recall by scenario）
  const scs = DATA.metrics.by_scenario;
  const W = 560, H = 250, padL = 40, padB = 46, padT = 18;
  const yMax = 1.0;
  let chart = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%">';
  [0, .5, 1].forEach(gy => {
    const y = padT + (H - padT - padB) * (1 - gy);
    chart += '<line x1="' + padL + '" y1="' + y + '" x2="' + (W-8) + '" y2="' + y +
      '" stroke="rgba(125,148,166,.18)" stroke-width="1"/>' +
      '<text x="' + (padL-6) + '" y="' + (y+3) + '" fill="#8496a5" font-size="9" text-anchor="end" font-family="ui-monospace">' + (gy*100) + '</text>';
  });
  const bw = 13, gapIn = 4;
  const groupW = (W - padL - 8) / scs.length;
  scs.forEach((s, i) => {
    const cx = padL + groupW * i + groupW / 2;
    const vals = [s.baseline.link_recall, s.tuned.link_recall];
    vals.forEach((v, j) => {
      if (v === null || v === undefined) return;
      const h = (H - padT - padB) * v;
      const x = cx - bw - gapIn/2 + j * (bw + gapIn);
      chart += '<rect x="' + x + '" y="' + (H - padB - h) + '" width="' + bw + '" height="' + h +
        '" rx="2" fill="' + (j ? '#39b8d0' : '#9d93e8') + '" opacity="' + (j ? .95 : .8) + '"/>' +
        '<text x="' + (x + bw/2) + '" y="' + (H - padB - h - 4) + '" fill="' + (j ? '#54cade' : '#b3aaef') +
        '" font-size="8.5" text-anchor="middle" font-family="ui-monospace">' + (v*100).toFixed(0) + '</text>';
    });
    chart += '<text x="' + cx + '" y="' + (H - padB + 14) + '" fill="#a9b8c4" font-size="9" text-anchor="middle">' +
      esc(scenName(s.scenario).split(' ')[0]) + '</text>' +
      '<text x="' + cx + '" y="' + (H - padB + 25) + '" fill="#8496a5" font-size="8" text-anchor="middle">n=' + s.n + '</text>';
  });
  chart += '</svg>';

  let table = '<table class="mtable"><tr><th>' + t('sb.col.sc') + '</th><th>' + t('sb.col.n') + '</th><th>' +
    t('sb.col.r5') + '</th><th>' + t('sb.col.lr') + '</th><th>' + t('sb.col.fl') + '</th></tr>';
  scs.forEach(s => {
    const b = s.baseline, tu = s.tuned;
    const cell = (x) => (x === null || x === undefined) ? '—' : (x*100).toFixed(0);
    const lrB = b.link_recall, lrT = tu.link_recall;
    table += '<tr><td>' + esc(scenName(s.scenario)) + '</td><td class="num">' + s.n + '</td>' +
      '<td class="num">' + cell(b['recall@5']) + ' ⟶ ' + cell(tu['recall@5']) + '</td>' +
      '<td class="num"><span' + (lrB !== null && lrT > lrB ? ' class="best"' : '') + '>' + cell(lrB) +
      ' ⟶ ' + cell(lrT) + '</span></td>' +
      '<td class="num">' + (b.false_link_rate !== null && b.false_link_rate !== undefined
        ? '<span class="worst">' + cell(b.false_link_rate) + '</span> ⟶ <span class="best">' + cell(tu.false_link_rate) + '</span>'
        : '—') + '</td></tr>';
  });
  table += '</table><div class="protocol">' + t('sb.proto') + '</div>';

  html += '<div class="sb-grid"><div class="card"><div style="font-size:12.5px;color:var(--t2);margin-bottom:8px">' +
    t('sb.chart') + '</div>' + chart + '</div>' +
    '<div class="card"><div style="font-size:12.5px;color:var(--t2);margin-bottom:8px">' + t('sb.table') + '</div>' +
    table + '</div></div>';
  document.getElementById('scoreMain').innerHTML = html;
}

/* ============== 开源基准轨（真实模型 × Febrl3，实测数字） ============== */
function renderOpenBench(){
  const ms = DATA.open_bench.models;
  // 柱状图：NDCG@10 前(violet)/后(cyan)；轴 90–100（图题已声明截断，柱上标精确值）
  const W = 560, H = 240, padL = 40, padB = 40, padT = 16, Y0 = 0.9, Y1 = 1.0;
  const yv = v => padT + (H - padT - padB) * (1 - (v - Y0) / (Y1 - Y0));
  let chart = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%">';
  [0.9, 0.95, 1.0].forEach(gy => {
    chart += '<line x1="' + padL + '" y1="' + yv(gy) + '" x2="' + (W-8) + '" y2="' + yv(gy) +
      '" stroke="rgba(125,148,166,.18)" stroke-width="1"/>' +
      '<text x="' + (padL-6) + '" y="' + (yv(gy)+3) + '" fill="#8496a5" font-size="9" text-anchor="end" font-family="ui-monospace">' + (gy*100) + '</text>';
  });
  const bw = 26, gapIn = 6, groupW = (W - padL - 8) / ms.length;
  ms.forEach((m, i) => {
    const cx = padL + groupW * i + groupW / 2;
    [[m.before.ndcg10, 0], [m.after.ndcg10, 1]].forEach(pair => {
      const v = pair[0], j = pair[1];
      const x = cx - bw - gapIn/2 + j * (bw + gapIn), top = yv(v);
      chart += '<rect x="' + x + '" y="' + top + '" width="' + bw + '" height="' + (H - padB - top) +
        '" rx="2" fill="' + (j ? '#39b8d0' : '#9d93e8') + '" opacity="' + (j ? .95 : .8) + '"/>' +
        '<text x="' + (x + bw/2) + '" y="' + (top - 4) + '" fill="' + (j ? '#54cade' : '#b3aaef') +
        '" font-size="8.5" text-anchor="middle" font-family="ui-monospace">' + (v*100).toFixed(1) + '</text>';
    });
    chart += '<text x="' + cx + '" y="' + (H - padB + 14) + '" fill="#a9b8c4" font-size="9.5" text-anchor="middle" font-family="ui-monospace">' + esc(m.key) + '</text>';
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
    '<div class="sb-grid"><div class="card"><div style="font-size:12.5px;color:var(--t2);margin-bottom:8px">' +
    t('ob.chart') + '</div>' + chart + '</div>' +
    '<div class="card"><div style="font-size:12.5px;color:var(--t2);margin-bottom:8px">' +
    t('ob.table') + '</div>' + table + '</div></div>' +
    '<div class="protocol">' + t('ob.proto') + '</div></div>';
  document.getElementById('openBench').innerHTML = html;
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
  setLang(LANG);
  // ?view= 直达视图 + ?focus= 直开档案卡（可分享链接）
  const qs = new URLSearchParams(location.search);
  const v = qs.get('view');
  if (v && document.getElementById('view-' + v)) {
    document.querySelector('#nav button[data-view="' + v + '"]').click();
  }
  const f = qs.get('focus');
  if (f && netNodes[f]) netSide(f);
  // ?resolve=1 直达"归并后"状态（跳过动画，供截图/分享）
  if (qs.get('resolve')) { netResolved = true; netT = 1; netApply(1); netSyncUI(); }
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
