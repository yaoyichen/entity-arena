"""演示页主题：INF 同族深空视觉。

色板原则（继承 retrieval-forge ADR 0005）：
- 靠明度差建立层级，不靠色相抢注意力；
- 全场只有两个"角色色"：cyan=微调模型/正确，violet=baseline；
- 暖红 #d97862 是"批注红"不是"警报红"，只给错误断言；
- 深底上明暗是反的：重要的更亮，背景层退暗。
"""

TOKENS = {
    "bg": "#0c1117",
    "panel": "#131a23",
    "panel2": "#1a2330",
    "line": "rgba(125,148,166,0.16)",
    "t1": "#d9e2ea",
    "t2": "#a9b8c4",
    "t3": "#8496a5",
    "cyan": "#39b8d0",
    "cyan-b": "#54cade",
    "violet": "#9d93e8",
    "violet-b": "#b3aaef",
    "red": "#d97862",
    "steel": "#55616d",
}

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
:root {
  --bg:#0c1117; --panel:#131a23; --panel2:#1a2330;
  --line:rgba(125,148,166,.16); --line2:rgba(125,148,166,.28);
  --t1:#d9e2ea; --t2:#a9b8c4; --t3:#8496a5;
  --cyan:#39b8d0; --cyan-b:#54cade; --violet:#9d93e8; --violet-b:#b3aaef;
  --red:#d97862; --steel:#55616d;
  --mono: ui-monospace,'SF Mono',Menlo,Consolas,monospace;
}
html,body { background:var(--bg); color:var(--t1);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
  font-size:14px; line-height:1.75; }
body { min-height:100vh;
  background-image:
    radial-gradient(1100px 520px at 12% -8%, rgba(57,184,208,.075), transparent 60%),
    radial-gradient(900px 480px at 88% -12%, rgba(157,147,232,.06), transparent 60%),
    repeating-linear-gradient(0deg, rgba(255,255,255,.013) 0 1px, transparent 1px 44px),
    repeating-linear-gradient(90deg, rgba(255,255,255,.013) 0 1px, transparent 1px 44px);
}

/* ---------- 顶栏 ---------- */
header { position:sticky; top:0; z-index:50; backdrop-filter:blur(12px);
  background:rgba(12,17,23,.82); border-bottom:1px solid var(--line); }
.hbar { max-width:1280px; margin:0 auto; padding:0 22px; height:58px;
  display:flex; align-items:center; gap:18px; }
.logo { display:flex; align-items:baseline; gap:10px; white-space:nowrap; }
.logo .mark { color:var(--cyan); font-weight:700; letter-spacing:.14em; font-size:15px; }
.logo .zh { color:var(--t3); font-size:12.5px; }
.pill { font-size:10.5px; letter-spacing:.12em; padding:2px 9px; border-radius:99px;
  border:1px solid rgba(57,184,208,.4); color:var(--cyan); white-space:nowrap; }
nav { display:flex; gap:4px; margin-left:auto; }
nav button { background:none; border:1px solid transparent; color:var(--t2); cursor:pointer;
  padding:7px 14px; border-radius:8px; font-size:13px; font-family:inherit; transition:all .18s; }
nav button:hover { color:var(--t1); background:rgba(125,148,166,.08); }
nav button.active { color:var(--cyan-b); background:rgba(57,184,208,.1);
  border-color:rgba(57,184,208,.35); }
.lang { background:none; border:1px solid var(--line2); color:var(--t2); cursor:pointer;
  padding:5px 12px; border-radius:8px; font-size:12px; font-family:inherit; }
.lang:hover { color:var(--t1); border-color:var(--t3); }

main { max-width:1280px; margin:0 auto; padding:26px 22px 60px; }
.view { display:none; }
.view.active { display:block; animation:fadein .25s ease; }
@keyframes fadein { from{opacity:0; transform:translateY(4px);} to{opacity:1; transform:none;} }

.vhead { margin-bottom:18px; }
.vhead h1 { font-size:20px; font-weight:650; letter-spacing:.01em; }
.vhead p { color:var(--t3); font-size:13px; margin-top:2px; max-width:860px; }
.vhead .legendline { display:flex; gap:16px; flex-wrap:wrap; margin-top:8px; font-size:12px; color:var(--t3); }
.legendline .k { display:inline-flex; align-items:center; gap:6px; }
.dot { width:8px; height:8px; border-radius:50%; display:inline-block; }

/* ---------- 通用卡片/芯片 ---------- */
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
.chip { display:inline-block; font-size:10.5px; padding:1px 8px; border-radius:99px;
  border:1px solid var(--line2); color:var(--t3); letter-spacing:.04em; }
.chip.cy { color:var(--cyan); border-color:rgba(57,184,208,.45); }
.chip.vi { color:var(--violet); border-color:rgba(157,147,232,.45); }
.chip.rd { color:var(--red); border-color:rgba(217,120,98,.5); }
.mono { font-family:var(--mono); font-size:12px; }

/* ---------- Arena ---------- */
.arena-grid { display:grid; grid-template-columns:300px 1fr; gap:16px; align-items:start; }
.qlist { display:flex; flex-direction:column; gap:8px; }
.qlist button { text-align:left; background:var(--panel); border:1px solid var(--line);
  border-radius:10px; padding:10px 12px; cursor:pointer; color:var(--t2); font-family:inherit;
  transition:border-color .18s; }
.qlist button:hover { border-color:var(--line2); color:var(--t1); }
.qlist button.active { border-color:rgba(57,184,208,.5); background:rgba(57,184,208,.06); color:var(--t1); }
.qlist .qtitle { font-size:13px; line-height:1.45; }
.qlist .qmeta { font-size:11px; color:var(--t3); margin-top:3px; display:flex; gap:6px; align-items:center; }

.qcard { margin-bottom:14px; }
.qcard .rec-name { font-size:17px; font-weight:650; }
.qcard .fields { display:flex; flex-wrap:wrap; gap:6px 22px; margin-top:6px; color:var(--t2); font-size:12.5px; }
.qcard .fields b { color:var(--t1); font-weight:550; }
.story { margin-top:8px; font-size:12.5px; color:var(--t3); border-left:2px solid rgba(57,184,208,.35);
  padding-left:10px; }

.duel { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.col-head { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.col-head .name { font-size:13px; font-weight:650; letter-spacing:.08em; }
.col-head .sub { font-size:11px; color:var(--t3); }
.col-head .bar-accent { height:3px; flex:1; border-radius:2px; opacity:.55; }
.accent-cy { background:linear-gradient(90deg, var(--cyan), transparent); }
.accent-vi { background:linear-gradient(90deg, var(--violet), transparent); }

.hit { background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:10px 13px; margin-bottom:9px; position:relative; }
.hit .row1 { display:flex; align-items:baseline; gap:9px; }
.hit .rank { color:var(--t3); font-family:var(--mono); font-size:11px; }
.hit .hname { font-weight:600; font-size:14.5px; }
.hit .vchip { margin-left:auto; font-size:10.5px; padding:1px 8px; border-radius:99px; letter-spacing:.05em; white-space:nowrap; }
.hit .meta { display:flex; flex-wrap:wrap; gap:4px 16px; color:var(--t3); font-size:11.5px; margin-top:2px; }
.hit .meta b { color:var(--t2); font-weight:500; }
.hit .score { margin-top:7px; }
.hit.linked { border-color:rgba(57,184,208,.4); }
.hit.false-link { border-color:rgba(217,120,98,.55); }
.hit.candidate { opacity:.92; }
.hit.dormant { opacity:.55; }

.v-linked { color:var(--cyan); border:1px solid rgba(57,184,208,.5); }
.v-candidate { color:var(--t3); border:1px solid var(--line2); }
.v-false { color:var(--red); border:1px solid rgba(217,120,98,.55); }
.v-unrel { color:var(--t3); border:1px solid var(--line); }

.sbar { position:relative; height:7px; border-radius:4px; background:rgba(125,148,166,.12); overflow:visible; }
.sbar .fill { position:absolute; inset:0 auto 0 0; border-radius:4px; }
.sbar .tick { position:absolute; top:-3px; bottom:-3px; width:1.5px; background:rgba(217,234,234,.4); }
.srow { display:flex; align-items:center; gap:10px; }
.srow .num { font-family:var(--mono); font-size:12px; width:38px; text-align:right; }
.srow .sbar { flex:1; }

.col-foot { display:flex; align-items:center; gap:10px; margin-top:4px; font-size:12px; color:var(--t3); flex-wrap:wrap; }
.col-foot .miss { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.misschip { font-family:var(--mono); font-size:11px; color:var(--t3);
  border:1px dashed var(--line2); border-radius:6px; padding:0 7px; }

/* ---------- Network ---------- */
.netwrap { display:grid; grid-template-columns:1fr 270px; gap:16px; align-items:start; }
.netbox { position:relative; background:var(--panel); border:1px solid var(--line);
  border-radius:12px; overflow:hidden; }
.netbox svg { display:block; width:100%; height:auto; }
.netbar { position:absolute; top:12px; left:12px; right:12px; display:flex; gap:10px; align-items:center;
  pointer-events:none; }
.resolve-btn { pointer-events:auto; background:rgba(19,26,35,.85); backdrop-filter:blur(8px);
  border:1px solid rgba(57,184,208,.5); color:var(--cyan-b); padding:8px 18px; border-radius:9px;
  cursor:pointer; font-size:13px; font-family:inherit; font-weight:600; letter-spacing:.03em;
  transition:all .2s; }
.resolve-btn:hover { background:rgba(57,184,208,.14); }
.resolve-btn.off { border-color:var(--line2); color:var(--t2); }
.netstate { margin-left:auto; background:rgba(19,26,35,.85); backdrop-filter:blur(8px);
  border:1px solid var(--line); border-radius:8px; padding:5px 12px; font-size:11.5px; color:var(--t3); }
.trackbar { display:flex; gap:8px; margin:0 0 12px; }
.trackbar button { background:rgba(19,26,35,.85); border:1px solid var(--line); color:var(--t2);
  padding:7px 16px; border-radius:9px; cursor:pointer; font-size:12.5px; font-family:inherit;
  font-weight:600; letter-spacing:.03em; transition:all .2s; }
.trackbar button:hover { border-color:var(--line2); color:var(--t1); }
.trackbar button.active { color:var(--cyan-b); border-color:rgba(57,184,208,.5); background:rgba(57,184,208,.1); }
#netTrackSub { display:flex; flex-wrap:wrap; gap:8px 18px; margin:-4px 0 12px; font-size:12px; color:var(--t2); }
#netTrackSub:empty { display:none; }
#netTrackSub .ob-sub { margin:0; font-size:11.5px; color:var(--t3); }
#netTrackSub b { font-weight:650; }
.num-bad { color:var(--red); }
.lg-bad-halo { display:inline-block; width:14px; height:14px; border-radius:50%; vertical-align:-2px;
  margin-right:6px; border:1.3px solid rgba(217,120,98,.9); background:rgba(217,120,98,.08); }

.node { fill:#64717e; stroke:#0c1117; stroke-width:1.5; cursor:pointer; transition:fill .15s; }
.node:hover { fill:#8a99a8; }
.node.insame { fill:#3f6a7d; }
.edge { stroke:#7aa7c9; stroke-opacity:.78; stroke-width:1.6; fill:none; }
.halo { fill:rgba(57,184,208,.08); stroke:rgba(57,184,208,.9); stroke-width:1.3; }
.halo.bad { fill:rgba(217,120,98,.07); stroke:rgba(217,120,98,.9); }
.elabel { fill:#9adfe9; font-size:12.5px; text-anchor:middle; font-weight:600;
  paint-order:stroke; stroke:#0c1117; stroke-width:3px; stroke-linejoin:round; }
.elabel.badlabel { fill:var(--red); }
.dim { opacity:.13 !important; transition:opacity .2s; }
.nolabel { fill:var(--t3); font-size:9.5px; text-anchor:middle; font-family:var(--mono);
  paint-order:stroke; stroke:#0c1117; stroke-width:2.5px; stroke-linejoin:round; }

.sidecard .rec-name { font-size:16px; font-weight:650; }
.sidecard .entname { margin-top:10px; padding-top:10px; border-top:1px dashed var(--line2); font-size:12.5px; color:var(--t2); }
.sidecard .entname b { color:var(--cyan-b); font-size:14px; }
.sidecard .members { margin-top:6px; display:flex; flex-direction:column; gap:3px; font-size:12px; color:var(--t3); }
.sidecard .rel { margin-top:10px; font-size:12px; color:var(--t2); }
.sidecard .rel b { color:var(--t1); }
.legend-grid { display:flex; flex-wrap:wrap; gap:8px 18px; font-size:11.5px; color:var(--t3); margin-top:12px; }

/* ---------- Explain ---------- */
.pairsel { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px; }
.pairsel button { background:var(--panel); border:1px solid var(--line); color:var(--t2);
  border-radius:9px; padding:7px 13px; cursor:pointer; font-family:inherit; font-size:12.5px; }
.pairsel button.active { border-color:rgba(57,184,208,.5); color:var(--t1); background:rgba(57,184,208,.07); }

.xgrid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.ftable { margin-top:14px; }
.frow { display:grid; grid-template-columns:88px 1fr 74px 1fr; gap:10px; align-items:center;
  padding:8px 6px; border-bottom:1px solid var(--line); }
.frow:last-child { border-bottom:none; }
.frow .flabel { color:var(--t3); font-size:12px; }
.frow .fval { font-size:13px; color:var(--t1); word-break:break-all; }
.frow .fval.na { color:var(--t3); }
.fstate { text-align:center; font-size:12px; letter-spacing:.03em; }
.st-exact { color:var(--cyan); } .st-fuzzy { color:var(--cyan-b); opacity:.85; }
.st-conflict { color:var(--red); } .st-missing { color:var(--t3); }
.st-exact .sym, .st-fuzzy .sym, .st-conflict .sym, .st-missing .sym { font-size:13px; }

.verdict-zone { margin-top:16px; }
.vrow { display:grid; grid-template-columns:120px 1fr 150px; gap:14px; align-items:center;
  padding:10px 4px; border-bottom:1px solid var(--line); }
.vrow:last-child { border-bottom:none; }
.vrow .vmodel { font-size:12.5px; letter-spacing:.06em; font-weight:600; }
.vrow .vnote { font-size:11.5px; color:var(--t3); }
.truth-chip { font-size:11px; letter-spacing:.08em; padding:3px 10px; border-radius:99px; }
.tc-match { color:var(--cyan); border:1px solid rgba(57,184,208,.5); }
.tc-non { color:var(--red); border:1px solid rgba(217,120,98,.5); }

/* ---------- Scoreboard ---------- */
.tiles { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:18px; }
.tile { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }
.tile .tname { font-size:12px; color:var(--t3); letter-spacing:.06em; }
.tile .nums { display:flex; align-items:baseline; gap:12px; margin-top:6px; }
.tile .n-b { color:var(--violet-b); font-family:var(--mono); font-size:26px; }
.tile .n-t { color:var(--cyan-b); font-family:var(--mono); font-size:30px; }
.tile .arrow { color:var(--t3); font-size:16px; }
.tile .delta { font-size:11.5px; margin-top:4px; }
.delta.up { color:var(--cyan); } .delta.down { color:var(--red); }

.sb-grid { display:grid; grid-template-columns:1.15fr 1fr; gap:16px; align-items:start; }
table.mtable { width:100%; border-collapse:collapse; font-size:12.5px; }
.mtable th { text-align:left; color:var(--t3); font-weight:550; padding:7px 10px;
  border-bottom:1px solid var(--line2); font-size:11.5px; letter-spacing:.04em; }
.mtable td { padding:7px 10px; border-bottom:1px solid var(--line); color:var(--t2); }
.mtable td.num { font-family:var(--mono); }
.mtable tr:last-child td { border-bottom:none; }
.mtable .best { color:var(--cyan-b); font-weight:650; }
.mtable .worst { color:var(--red); }
.protocol { margin-top:14px; font-size:11.5px; color:var(--t3); line-height:1.8;
  border-top:1px dashed var(--line2); padding-top:10px; }

/* ---------- 开源基准轨（真实模型实测，Febrl3） ---------- */
.ob-wrap { margin-top:34px; padding-top:22px; border-top:1px solid var(--line); }
.ob-h2 { font-size:16px; font-weight:650; color:var(--t1); margin:0 0 4px;
  display:flex; align-items:center; gap:12px; }
.ob-sub { font-size:12.5px; color:var(--t2); margin:0 0 16px; max-width:880px; line-height:1.65; }
.ob-num-b { color:var(--violet-b); font-family:var(--mono); }
.ob-num-t { color:var(--cyan-b); font-family:var(--mono); }

/* ---------- 页脚 ---------- */
footer { border-top:1px solid var(--line); margin-top:34px; padding:16px 0 26px; }
footer .fin { max-width:1280px; margin:0 auto; padding:0 22px; font-size:11.5px; color:var(--t3);
  display:flex; gap:14px; flex-wrap:wrap; align-items:center; }

@media (max-width: 960px) {
  .arena-grid { grid-template-columns:1fr; }
  .duel, .xgrid, .sb-grid { grid-template-columns:1fr; }
  .tiles { grid-template-columns:1fr; }
  .netwrap { grid-template-columns:1fr; }
}
"""
