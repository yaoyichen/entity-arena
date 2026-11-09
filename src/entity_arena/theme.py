"""演示页主题：INF 同族深空视觉（精修版，方向 A）。

色板原则（继承 retrieval-forge ADR 0005）：
- 靠明度差建立层级，不靠色相抢注意力；
- 全场只有两个"角色色"：cyan=微调模型/正确，violet=baseline；
- 暖红 #cf6d58 是"批注红"不是"警报红"，只给错误断言；
- 深底上明暗是反的：重要的更亮，背景层退暗。

角色三色经 dataviz 六查校验（色觉模拟 ΔE / 常规视觉下限 / 亮度带 /
色度下限 / 表面对比度），由 tests/test_theme_palette.py 锁定——
改色值必须先过校验再合入（本机无 node，校验器为 skill 脚本的 Python 移植）。

排版 token（本次精修的核心）：
- 字号 6 档：11（眉题/说明）/ 12（次要）/ 13（正文）/ 14（强调）/ 16（区块）/ 20（视图题），
  大数字 26–28 单列；轴/表格数字用 tabular-nums，大数字用比例数字；
- 间距 4px 网格；圆角 3 档：--r-s 4 / --r-m 8 / --r-l 12（胶囊 999）；
- 控件一族：统一 8px 圆角、统一内距档位、:focus-visible 可见焦点。
"""

TOKENS = {
    "bg": "#0c1117",
    "panel": "#131a23",
    "panel2": "#1a2330",
    "line": "rgba(125,148,166,0.16)",
    "t1": "#dbe4ec",
    "t2": "#a9b8c4",
    "t3": "#7f93a3",
    # 角色三色（六查全过：vs bg/panel 对比 ≥4.6:1，相邻 CVD ΔE ≥8，常规 ΔE ≥15）
    "cyan": "#10a6ad",
    "cyan-b": "#48c5d0",   # 文字亮阶（panel 上 8.5:1）
    "cyan-l": "#8ee2ea",   # 网络标签亮阶（panel 上 11.9:1）
    "violet": "#787bd2",
    "violet-b": "#a3a5ec",  # 文字亮阶（panel 上 7.6:1）
    "red": "#cf6d58",
    "red-b": "#e69886",     # 文字亮阶（panel 上 7.7:1）
    "steel": "#55616d",
}

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
:root {
  --bg:#0c1117; --panel:#131a23; --panel2:#1a2330;
  --line:rgba(125,148,166,.16); --line2:rgba(125,148,166,.28);
  --t1:#dbe4ec; --t2:#a9b8c4; --t3:#7f93a3;
  --cyan:#10a6ad; --cyan-b:#48c5d0; --cyan-l:#8ee2ea;
  --violet:#787bd2; --violet-b:#a3a5ec;
  --red:#cf6d58; --red-b:#e69886;
  --steel:#55616d; --edge:#7aa7c9;
  /* INF 平台家族签名（对齐控制台 styles.css）：135° 青→紫渐变。
     只做界面铬件（logo/导航/切换器/卡左线），绝不做数据编码——
     数据标记永远用上面过六查的实色角色色。 */
  --sig-a:#22d3ee; --sig-b:#a78bfa;
  --sig:linear-gradient(135deg,var(--sig-a),var(--sig-b));
  --r-s:4px; --r-m:8px; --r-l:16px;
  --shadow-1:0 1px 2px rgba(0,0,0,.35);
  --shadow-2:0 12px 32px -10px rgba(0,0,0,.5);
  --mono: ui-monospace,'SF Mono',Menlo,Consolas,monospace;
}
html,body { background:var(--bg); color:var(--t1);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
  font-size:13.5px; line-height:1.6; }
body { min-height:100vh;
  background-image:
    radial-gradient(1100px 520px at 12% -8%, rgba(16,166,173,.07), transparent 60%),
    radial-gradient(900px 480px at 88% -12%, rgba(120,123,210,.06), transparent 60%),
    repeating-linear-gradient(0deg, rgba(255,255,255,.013) 0 1px, transparent 1px 44px),
    repeating-linear-gradient(90deg, rgba(255,255,255,.013) 0 1px, transparent 1px 44px);
}
button { font-family:inherit; }
button:focus-visible, a:focus-visible { outline:2px solid var(--cyan-b); outline-offset:2px; border-radius:var(--r-s); }

/* ---------- 顶栏 ---------- */
header { position:sticky; top:0; z-index:50; backdrop-filter:blur(12px);
  background:rgba(12,17,23,.82); border-bottom:1px solid var(--line); }
.hbar { max-width:1280px; margin:0 auto; padding:0 24px; height:56px;
  display:flex; align-items:center; gap:16px; }
.logo { display:flex; align-items:baseline; gap:10px; white-space:nowrap; }
.logo .mark { font-weight:700; letter-spacing:.14em; font-size:14px;
  background:var(--sig); -webkit-background-clip:text; background-clip:text;
  color:var(--cyan-b); -webkit-text-fill-color:transparent; }
.logo .zh { color:var(--t3); font-size:12px; }
.pill { font-size:11px; letter-spacing:.1em; padding:2px 10px; border-radius:999px;
  border:1px solid rgba(72,197,208,.38); color:var(--cyan-b); white-space:nowrap; }
nav { display:flex; gap:4px; margin-left:auto; }
nav button { position:relative; background:none; border:1px solid transparent; color:var(--t2); cursor:pointer;
  padding:6px 14px; border-radius:var(--r-m); font-size:13px; transition:all .18s; }
nav button:hover { color:var(--t1); background:rgba(125,148,166,.08); }
nav button.active { color:var(--t1); font-weight:600; background:rgba(255,255,255,.045); }
nav button.active::after { content:""; position:absolute; left:12px; right:12px; bottom:1px;
  height:2px; border-radius:2px; background:var(--sig); }
.lang { background:none; border:1px solid var(--line2); color:var(--t2); cursor:pointer;
  padding:5px 12px; border-radius:var(--r-m); font-size:12px; }
.lang:hover { color:var(--t1); border-color:var(--t3); }

main { max-width:1280px; margin:0 auto; padding:24px 24px 60px; }
.view { display:none; }
.view.active { display:block; animation:fadein .25s ease; }
@keyframes fadein { from{opacity:0; transform:translateY(4px);} to{opacity:1; transform:none;} }

.vhead { margin-bottom:20px; }
.vhead h1 { font-size:20px; font-weight:650; letter-spacing:.01em; line-height:1.3; }
.vhead p { color:var(--t3); font-size:13px; margin-top:4px; max-width:860px; line-height:1.55; }
.vhead .legendline { display:flex; gap:16px; flex-wrap:wrap; margin-top:8px; font-size:12px; color:var(--t3); }
.legendline .k { display:inline-flex; align-items:center; gap:6px; }
.dot { width:8px; height:8px; border-radius:2px; display:inline-block; }

/* 眉题：区块上方的小号大写标签 */
.eyebrow { font-size:11px; font-weight:600; letter-spacing:.08em; text-transform:uppercase;
  color:var(--t3); }

/* ---------- 通用卡片/芯片 ---------- */
.card { background:var(--panel); border:1px solid var(--line); border-radius:var(--r-l);
  padding:16px; box-shadow:var(--shadow-1);
  background-image:linear-gradient(180deg, rgba(255,255,255,.028), rgba(255,255,255,0) 38%); }
/* 平台家族签名：卡片左侧渐变细线（console result 卡同款），挂 .gline 启用 */
.gline { position:relative; }
.gline::before { content:""; position:absolute; left:-1px; top:12px; bottom:12px; width:2.5px;
  border-radius:0 2px 2px 0; background:var(--sig); opacity:.8; }
.chip { display:inline-block; font-size:11px; padding:1px 8px; border-radius:999px;
  border:1px solid var(--line2); color:var(--t3); letter-spacing:.04em; }
.chip.cy { color:var(--cyan-b); border-color:rgba(72,197,208,.4); }
.chip.vi { color:var(--violet-b); border-color:rgba(163,165,236,.4); }
.chip.rd { color:var(--red-b); border-color:rgba(230,152,134,.45); }
.mono { font-family:var(--mono); font-size:12px; }

/* ---------- Arena ---------- */
.arena-grid { display:grid; grid-template-columns:300px 1fr; gap:16px; align-items:start; }
.qlist { display:flex; flex-direction:column; gap:8px; }
.qlist button { text-align:left; background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r-m); padding:10px 12px; cursor:pointer; color:var(--t2);
  transition:border-color .18s; box-shadow:var(--shadow-1); }
.qlist button:hover { border-color:var(--line2); color:var(--t1); }
.qlist button.active { border-color:rgba(72,197,208,.5); background:rgba(16,166,173,.08); color:var(--t1); }
.qlist .qtitle { font-size:13px; line-height:1.45; }
.qlist .qmeta { font-size:12px; color:var(--t3); margin-top:4px; display:flex; gap:8px; align-items:center; }

.qcard { margin-bottom:16px; }
.qcard .rec-name { font-size:16px; font-weight:650; }
.qcard .fields { display:flex; flex-wrap:wrap; gap:4px 24px; margin-top:8px; color:var(--t2); font-size:12.5px; }
.qcard .fields b { color:var(--t1); font-weight:550; }
.story { margin-top:8px; font-size:12.5px; color:var(--t3); line-height:1.55;
  border-left:2px solid rgba(72,197,208,.35); padding-left:12px; }

.duel { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.col-head { display:flex; align-items:center; gap:12px; margin-bottom:12px; }
.col-head .name { font-size:13px; font-weight:650; letter-spacing:.08em; }
.col-head .sub { font-size:12px; color:var(--t3); }
.col-head .bar-accent { height:2px; flex:1; border-radius:2px; opacity:.5; }
.accent-cy { background:linear-gradient(90deg, var(--cyan), transparent); }
.accent-vi { background:linear-gradient(90deg, var(--violet), transparent); }

.hit { background:var(--panel); border:1px solid var(--line); border-radius:var(--r-m);
  padding:12px; margin-bottom:8px; position:relative; box-shadow:var(--shadow-1); }
.hit .row1 { display:flex; align-items:baseline; gap:8px; }
.hit .rank { color:var(--t3); font-family:var(--mono); font-size:11px; }
.hit .hname { font-weight:600; font-size:14px; }
.hit .vchip { margin-left:auto; font-size:11px; padding:1px 8px; border-radius:999px; letter-spacing:.04em; white-space:nowrap; }
.hit .meta { display:flex; flex-wrap:wrap; gap:4px 16px; color:var(--t3); font-size:12px; margin-top:2px; line-height:1.5; }
.hit .meta b { color:var(--t2); font-weight:500; }
.hit .score { margin-top:8px; }
.hit.linked { border-color:rgba(72,197,208,.4); }
.hit.false-link { border-color:rgba(207,109,88,.55); }
.hit.candidate { opacity:.92; }
.hit.dormant { opacity:.55; }

.v-linked { color:var(--cyan-b); border:1px solid rgba(72,197,208,.45); }
.v-candidate { color:var(--t3); border:1px solid var(--line2); }
.v-false { color:var(--red-b); border:1px solid rgba(230,152,134,.5); }
.v-unrel { color:var(--t3); border:1px solid var(--line); }

.sbar { position:relative; height:6px; border-radius:3px; background:rgba(125,148,166,.12); overflow:visible; }
.sbar .fill { position:absolute; inset:0 auto 0 0; border-radius:3px 2px 2px 3px; }
.sbar .tick { position:absolute; top:-3px; bottom:-3px; width:1.5px; background:rgba(219,228,236,.45); }
.srow { display:flex; align-items:center; gap:8px; }
.srow .num { font-family:var(--mono); font-size:12px; width:38px; text-align:right; }
.srow .sbar { flex:1; }

.col-foot { display:flex; align-items:center; gap:12px; margin-top:4px; font-size:12px; color:var(--t3); flex-wrap:wrap; }
.col-foot .miss { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.misschip { font-family:var(--mono); font-size:11px; color:var(--t3);
  border:1px dashed var(--line2); border-radius:var(--r-s); padding:0 8px; }

/* ---------- Network ---------- */
.netwrap { display:grid; grid-template-columns:1fr 270px; gap:16px; align-items:start; }
.netbox { position:relative; background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r-l); overflow:hidden; box-shadow:var(--shadow-1);
  background-image:linear-gradient(180deg, rgba(255,255,255,.028), rgba(255,255,255,0) 38%); }
.netbox svg { display:block; width:100%; height:auto; }
.netbar { position:absolute; top:12px; left:12px; right:12px; display:flex; gap:12px; align-items:center;
  pointer-events:none; }
.resolve-btn { pointer-events:auto; background:rgba(19,26,35,.88); backdrop-filter:blur(8px);
  border:1px solid rgba(72,197,208,.5); color:var(--cyan-b); padding:8px 18px; border-radius:var(--r-m);
  cursor:pointer; font-size:13px; font-weight:600; letter-spacing:.03em;
  transition:all .2s; }
.resolve-btn:hover { background:rgba(16,166,173,.16); }
.resolve-btn.off { border-color:var(--line2); color:var(--t2); }
.netstate { margin-left:auto; background:rgba(19,26,35,.88); backdrop-filter:blur(8px);
  border:1px solid var(--line); border-radius:var(--r-m); padding:4px 12px; font-size:12px; color:var(--t3); }
.trackbar { display:flex; gap:8px; margin:0 0 12px; }
.trackbar button { background:rgba(19,26,35,.88); border:1px solid var(--line); color:var(--t2);
  padding:6px 16px; border-radius:var(--r-m); cursor:pointer; font-size:12.5px;
  font-weight:600; letter-spacing:.03em; transition:all .2s; }
.trackbar button:hover { border-color:var(--line2); color:var(--t1); }
.trackbar button.active { color:#081119; border-color:transparent; font-weight:650;
  background:var(--sig); }
/* header 内的全局数据集切换器：更紧凑 */
.trackbar.hdr { margin:0; }
.trackbar.hdr button { font-size:11px; padding:3px 11px; border-radius:999px; }
#netTrackSub { display:flex; flex-wrap:wrap; gap:8px 18px; margin:-4px 0 12px; font-size:12px; color:var(--t2); }
#netTrackSub:empty { display:none; }
#netTrackSub .ob-sub { margin:0; font-size:12px; color:var(--t3); }
#netTrackSub b { font-weight:650; }
.num-bad { color:var(--red-b); }
.lg-bad-halo { display:inline-block; width:14px; height:14px; border-radius:50%; vertical-align:-2px;
  margin-right:6px; border:1.3px solid rgba(207,109,88,.9); background:rgba(207,109,88,.08); }

.node { fill:#64717e; stroke:#0c1117; stroke-width:1.5; cursor:pointer; transition:fill .15s; }
.node:hover { fill:#8a99a8; }
.node.insame { fill:#3f6a7d; }
.edge { stroke:var(--edge); stroke-opacity:.78; stroke-width:1.6; fill:none; }
/* 漏连真对：红虚线桥 + 中点 ⊘ 断口标记（"该连而没连"的缺失画成断线；红=错误专用）。
   权重低于误连红线一档：漏连是"缺失"的批注层，实测轨 8 条、合成轨 25 条，
   同权重会盖过标签与光环（误连 2.4/.95 vs 这里 1.4/.68） */
.missline { stroke:var(--red); stroke-opacity:.68; stroke-width:1.4; stroke-dasharray:5 4; fill:none; }
.missmark circle { fill:var(--panel); stroke:var(--red); stroke-width:1.4; }
.missmark line { stroke:var(--red); stroke-width:1.4; }
.halo { fill:rgba(16,166,173,.08); stroke:rgba(16,166,173,.9); stroke-width:1.3; }
.halo.bad { fill:rgba(207,109,88,.07); stroke:rgba(207,109,88,.9); }
.elabel { fill:var(--cyan-l); font-size:12.5px; text-anchor:middle; font-weight:600;
  paint-order:stroke; stroke:#0c1117; stroke-width:3px; stroke-linejoin:round; }
.elabel.badlabel { fill:var(--red-b); }
.dim { opacity:.13 !important; transition:opacity .2s; }
.nolabel { fill:var(--t3); font-size:10px; text-anchor:middle; font-family:var(--mono);
  paint-order:stroke; stroke:#0c1117; stroke-width:2.5px; stroke-linejoin:round; }

.sidecard .rec-name { font-size:16px; font-weight:650; }
.sidecard .entname { margin-top:12px; padding-top:12px; border-top:1px dashed var(--line2); font-size:12.5px; color:var(--t2); }
.sidecard .entname b { color:var(--cyan-b); font-size:14px; }
.sidecard .members { margin-top:8px; display:flex; flex-direction:column; gap:4px; font-size:12px; color:var(--t3); }
.sidecard .rel { margin-top:12px; font-size:12px; color:var(--t2); }
.sidecard .rel b { color:var(--t1); }
.legend-grid { display:flex; flex-wrap:wrap; gap:8px 18px; font-size:12px; color:var(--t3); margin-top:12px; }

/* ---------- Explain ---------- */
.pairsel { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }
.pairsel button { background:var(--panel); border:1px solid var(--line); color:var(--t2);
  border-radius:var(--r-m); padding:6px 12px; cursor:pointer; font-size:12.5px; }
.pairsel button.active { border-color:rgba(72,197,208,.5); color:var(--t1); background:rgba(16,166,173,.08); }

.xgrid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.ftable { margin-top:16px; }
.frow { display:grid; grid-template-columns:88px 1fr 76px 1fr; gap:12px; align-items:center;
  padding:8px 8px; border-bottom:1px solid var(--line); }
.frow:last-child { border-bottom:none; }
.frow .flabel { color:var(--t3); font-size:12px; }
.frow .fval { font-size:13px; color:var(--t1); word-break:break-all; }
.frow .fval.na { color:var(--t3); }
.fstate { text-align:center; font-size:12px; letter-spacing:.03em; }
.st-exact { color:var(--cyan-b); } .st-fuzzy { color:var(--cyan-l); opacity:.85; }
.st-conflict { color:var(--red-b); } .st-missing { color:var(--t3); }
.st-exact .sym, .st-fuzzy .sym, .st-conflict .sym, .st-missing .sym { font-size:13px; }

.verdict-zone { margin-top:16px; }
.vrow { display:grid; grid-template-columns:120px 1fr 150px; gap:16px; align-items:center;
  padding:12px 4px; border-bottom:1px solid var(--line); }
.vrow:last-child { border-bottom:none; }
.vrow .vmodel { font-size:12.5px; letter-spacing:.06em; font-weight:600; }
.vrow .vnote { font-size:12px; color:var(--t3); }
.truth-chip { font-size:11px; letter-spacing:.08em; padding:3px 10px; border-radius:999px; }
.tc-match { color:var(--cyan-b); border:1px solid rgba(72,197,208,.45); }
.tc-non { color:var(--red-b); border:1px solid rgba(230,152,134,.5); }

/* ---------- Scoreboard ---------- */
.tiles { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:20px; }
.tile { background:var(--panel); border:1px solid var(--line); border-radius:var(--r-l);
  padding:16px 18px; box-shadow:var(--shadow-1);
  background-image:linear-gradient(180deg, rgba(255,255,255,.028), rgba(255,255,255,0) 38%); }
.tile .tname { font-size:11px; font-weight:600; color:var(--t3); letter-spacing:.08em;
  text-transform:uppercase; }
.tile .nums { display:flex; align-items:baseline; gap:12px; margin-top:8px; }
/* 数字穿文字墨色，身份由色点承载（dataviz：text wears text tokens） */
.tile .n-b { color:var(--t2); font-size:16px; font-weight:600; }
.tile .n-t { color:var(--t1); font-size:28px; font-weight:650; letter-spacing:-.01em; }
.tile .arrow { color:var(--t3); font-size:14px; }
.mk { display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:7px;
  vertical-align:1px; }
.mk-b { background:var(--violet); } .mk-t { background:var(--cyan); }
.tile .delta { font-size:12px; margin-top:6px; font-variant-numeric:tabular-nums; }
.delta.up { color:var(--cyan-b); } .delta.down { color:var(--red-b); }

.sb-grid { display:grid; grid-template-columns:1.15fr 1fr; gap:16px; align-items:start; }
.chart-head { display:flex; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap; }
.chart-head .mb-legend { display:flex; gap:14px; font-size:12px; color:var(--t3); margin-left:auto; }
.chart-head .mb-legend .k { display:inline-flex; align-items:center; gap:6px; }
.chartwrap { position:relative; }
/* 柱状图标记：fill 走 token；数据端圆角由 path 实现（基线端方） */
.mb-t { fill:var(--cyan); } .mb-b { fill:var(--violet); }
.ob-t { fill:var(--cyan); } .ob-b { fill:var(--violet); }
rect.bar-mark { cursor:pointer; transition:opacity .15s; }
rect.bar-mark:hover { opacity:.82; }
text.ax-t { fill:var(--t3); font-size:10.5px; font-variant-numeric:tabular-nums; }
text.ax-cat { fill:var(--t2); font-size:11px; }
text.ax-sub { fill:var(--t3); font-size:10px; font-variant-numeric:tabular-nums; }
text.cap-t { fill:var(--t2); font-size:10.5px; font-variant-numeric:tabular-nums; }
.ctip { position:fixed; z-index:90; pointer-events:none; background:var(--panel2);
  border:1px solid var(--line2); border-radius:var(--r-m); padding:8px 12px;
  font-size:12px; color:var(--t1); box-shadow:var(--shadow-2); max-width:240px; }
.ctip .m { font-weight:600; }
.ctip .v { font-variant-numeric:tabular-nums; color:var(--t2); }

table.mtable { width:100%; border-collapse:collapse; font-size:12.5px; font-variant-numeric:tabular-nums; }
.mtable th { text-align:left; color:var(--t3); font-weight:550; padding:8px 10px;
  border-bottom:1px solid var(--line2); font-size:11px; letter-spacing:.05em; text-transform:uppercase; }
.mtable td { padding:8px 10px; border-bottom:1px solid var(--line); color:var(--t2); }
.mtable td.num { font-family:var(--mono); font-size:11.5px; white-space:nowrap; }
.mtable tr:last-child td { border-bottom:none; }
.mtable .best { color:var(--cyan-b); font-weight:650; }
.mtable .worst { color:var(--red-b); }
.protocol { margin-top:16px; font-size:12px; color:var(--t3); line-height:1.7;
  border-top:1px dashed var(--line2); padding-top:12px; }

/* ---------- 开源基准轨（真实模型实测，Febrl3） ---------- */
.ob-wrap { margin-top:32px; padding-top:24px; border-top:1px solid var(--line); }
.ob-h2 { font-size:16px; font-weight:650; color:var(--t1); margin:0 0 4px;
  display:flex; align-items:center; gap:12px; }
.ob-sub { font-size:12.5px; color:var(--t2); margin:0 0 16px; max-width:880px; line-height:1.65; }
.ob-num-b { color:var(--violet-b); font-variant-numeric:tabular-nums; }
.ob-num-t { color:var(--cyan-b); font-variant-numeric:tabular-nums; }

/* ---------- Connect（MCP 接入） ---------- */
.conn-steps { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:16px; }
.conn-step .sh { font-size:13px; font-weight:650; color:var(--cyan-b); letter-spacing:.02em; }
.conn-step .st { font-size:12.5px; color:var(--t2); margin-top:4px; line-height:1.6; }
.conn-grid { display:grid; grid-template-columns:1.05fr .95fr; gap:16px; align-items:start; margin-bottom:16px; }
.conn-h2 { font-size:13.5px; font-weight:650; margin-bottom:12px; }
.conn-h3 { font-size:11px; color:var(--t3); margin:12px 0 4px; letter-spacing:.08em; text-transform:uppercase; }
.conn-note { font-size:12px; color:var(--t3); margin-top:8px; line-height:1.6; }
.codeblock { position:relative; margin:8px 0 2px; }
.codeblock pre { background:var(--panel2); border:1px solid var(--line); border-radius:var(--r-m);
  padding:12px 44px 12px 12px; overflow-x:auto; font-family:var(--mono); font-size:11.5px;
  line-height:1.65; color:var(--t2); white-space:pre; }
.copybtn { position:absolute; top:8px; right:8px; background:rgba(12,17,23,.9);
  border:1px solid var(--line2); color:var(--t3); font-size:11px; padding:2px 10px;
  border-radius:var(--r-s); cursor:pointer; transition:all .15s; }
.copybtn:hover { color:var(--cyan-b); border-color:rgba(72,197,208,.4); }
.copybtn.ok { color:var(--cyan-b); border-color:rgba(72,197,208,.5); }
.conn-ex { display:flex; flex-direction:column; gap:8px; }

/* ---------- 试玩台（playground） ---------- */
.pl-presets { display:flex; flex-wrap:wrap; gap:8px; }
.pl-preset { font:inherit; font-size:12px; color:var(--t2); background:var(--panel2);
  border:1px solid var(--line); border-radius:999px; padding:5px 12px; cursor:pointer;
  transition:all .15s; }
.pl-preset:hover { color:var(--t1); border-color:var(--line2); }
.play-grid { display:grid; grid-template-columns:.92fr 1.08fr; gap:16px; align-items:start; }
.pl-pick .pl-side { margin-top:14px; }
.pl-pick .pl-side:first-of-type { margin-top:2px; }
.pl-lab { font-size:11px; color:var(--cyan-b); letter-spacing:.1em; margin-bottom:5px; }
.pl-search { width:100%; font:inherit; font-size:12.5px; color:var(--t1); background:var(--bg);
  border:1px solid var(--line); border-radius:var(--r-m); padding:7px 10px; outline:none; }
.pl-search:focus-visible { border-color:var(--cyan); }
.plist { margin-top:6px; display:flex; flex-direction:column; gap:2px; max-height:238px; overflow:auto; }
.pl-row { display:block; width:100%; text-align:left; font:inherit; background:transparent;
  border:1px solid transparent; border-radius:var(--r-m); padding:6px 9px; cursor:pointer; }
.pl-row:hover { background:var(--panel2); }
.pl-row.active { border-color:rgba(72,197,208,.5); background:var(--panel2); }
.pl-rname { display:block; font-size:12.5px; color:var(--t1); }
.pl-rmeta { display:block; font-size:10.5px; color:var(--t3); margin-top:2px; }
.pl-rrid { float:right; font-size:10px; color:var(--t3); padding-top:2px; }
.pl-row.active .pl-rrid { color:var(--cyan-b); }
.pl-more { font-size:11px; color:var(--t3); padding:4px 9px; }
.pl-empty { font-size:13px; color:var(--t3); line-height:1.7; }
.pl-guess .sh { font-size:14px; font-weight:650; color:var(--t1); }
.pl-gbtns { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }
.pl-g { font:inherit; font-size:12.5px; border-radius:var(--r-m); cursor:pointer;
  padding:7px 16px; background:var(--panel2); border:1px solid var(--line2); color:var(--t1);
  transition:all .15s; }
.pl-g.link:hover { border-color:var(--cyan-b); color:var(--cyan-b); }
.pl-g.refuse:hover { border-color:var(--violet-b); color:var(--violet-b); }
.pl-g.skip { color:var(--t3); }
.pl-g.skip:hover { color:var(--t2); }
.pl-score-row { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
.pl-big { font-size:26px; color:var(--t1); font-variant-numeric:tabular-nums; }
.pl-verdict-chip { font-size:12.5px; font-weight:650; border-radius:999px; padding:3px 12px; }
.pl-verdict-chip.ok { color:var(--cyan-b); border:1px solid rgba(72,197,208,.45); }
.pl-verdict-chip.bad { color:var(--red-b); border:1px solid rgba(230,152,134,.5); }
.pl-margin { font-size:12px; color:var(--t3); }
.pgauge { position:relative; height:10px; margin:26px 0 6px; background:var(--panel2);
  border-radius:5px; }
.pg-fill { position:absolute; left:0; top:0; bottom:0; border-radius:5px; }
.pg-tick { position:absolute; top:-5px; bottom:-5px; width:2px; background:var(--t2); }
.pg-tick span { position:absolute; top:-15px; left:50%; transform:translateX(-50%);
  font-size:10px; color:var(--t3); white-space:nowrap; font-family:var(--mono); }
.pl-truth { margin-top:12px; font-size:13px; color:var(--t2); }
.pl-said { margin-top:6px; font-size:12.5px; color:var(--t2); }
.pl-mode { margin-top:10px; font-size:11px; color:var(--t3); line-height:1.6; }
button.chip { font:inherit; cursor:pointer; background:transparent; }

/* ---------- 页脚 ---------- */
footer { border-top:1px solid var(--line); margin-top:32px; padding:16px 0 28px; }
footer .fin { max-width:1280px; margin:0 auto; padding:0 24px; font-size:12px; color:var(--t3);
  display:flex; gap:16px; flex-wrap:wrap; align-items:center; }

@media (max-width: 960px) {
  .arena-grid { grid-template-columns:1fr; }
  .duel, .xgrid, .sb-grid { grid-template-columns:1fr; }
  .tiles { grid-template-columns:1fr; }
  .netwrap { grid-template-columns:1fr; }
  .conn-steps, .conn-grid, .play-grid { grid-template-columns:1fr; }
}
"""
