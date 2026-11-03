#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_kg_viz.py — 把图谱 JSON 渲染成单文件交互式 HTML

为什么内嵌数据而不是 fetch：
    `file://` 协议下 fetch 会被 CORS 拦，双击打不开。这个文件是要「双击即用」的，
    所以 JSON 直接内嵌进 HTML。代价是文件大一点（约 60KB），换来零依赖。

交互：
    拖节点 / 拖画布平移 / 滚轮缩放 / 点击看邻居 / 搜索高亮 / 密度滑块
"""

import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

TPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识库实体图谱 · __TITLE_SUB__</title>
<style>
  :root{
    --bg:#0b0f14; --panel:#131a22; --line:#22303f; --fg:#e6edf3;
    --fg-dim:#8b98a5; --accent:#4cc9f0; --link:#2a3f52;
    --c0:#4cc9f0; --c1:#f72585; --c2:#ffd166; --c3:#06d6a0;
    --c4:#b388ff; --c5:#ff8c42; --c6:#3a86ff; --c7:#e07a5f;
  }
  @media (prefers-color-scheme: light){
    :root{ --bg:#f7f9fb; --panel:#ffffff; --line:#dbe3ec; --fg:#14202b;
           --fg-dim:#5c6b7a; --accent:#0077b6; --link:#b9c6d4;
           --c0:#0077b6; --c1:#d00000; --c2:#e09f00; --c3:#0a8754;
           --c4:#7048e8; --c5:#e8590c; --c6:#1d4ed8; --c7:#a8420b; }
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
    overflow:hidden}
  #wrap{display:flex;height:100%}
  #side{width:290px;flex:0 0 290px;background:var(--panel);border-right:1px solid var(--line);
    padding:18px 16px;overflow-y:auto}
  h1{font-size:15px;margin:0 0 2px;font-weight:650;letter-spacing:.2px}
  .sub{color:var(--fg-dim);font-size:12px;margin-bottom:16px}
  .kpi{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px}
  .kpi div{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
  .kpi b{display:block;font-size:19px;line-height:1.2;font-variant-numeric:tabular-nums}
  .kpi span{font-size:11px;color:var(--fg-dim)}
  .sec{font-size:11px;color:var(--fg-dim);text-transform:uppercase;
    letter-spacing:.8px;margin:16px 0 8px}
  input#q{width:100%;padding:7px 10px;border-radius:7px;border:1px solid var(--line);
    background:var(--bg);color:var(--fg);font-size:13px;outline:none}
  input#q:focus{border-color:var(--accent)}
  .legend{display:flex;flex-wrap:wrap;gap:6px 12px;font-size:12px}
  .legend i{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px;
    vertical-align:middle}
  .note{font-size:12px;color:var(--fg-dim);border-left:2px solid var(--line);
    padding-left:10px;margin-top:14px;line-height:1.65}
  .note b{color:var(--fg)}
  .row{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--fg-dim);margin-top:10px}
  input[type=range]{flex:1;accent-color:var(--accent)}
  #stage{flex:1;position:relative}
  canvas{display:block;width:100%;height:100%;cursor:grab}
  canvas:active{cursor:grabbing}
  #tip{position:absolute;pointer-events:none;background:var(--panel);
    border:1px solid var(--line);border-radius:8px;padding:8px 11px;font-size:12px;
    box-shadow:0 8px 24px rgba(0,0,0,.28);opacity:0;transition:opacity .12s;
    max-width:250px;line-height:1.5;z-index:10}
  #tip b{font-size:13px}
  #tip .d{color:var(--fg-dim);font-size:11px;margin-top:3px}
  #hint{position:absolute;left:16px;bottom:14px;font-size:11px;color:var(--fg-dim);
    background:var(--panel);border:1px solid var(--line);border-radius:20px;
    padding:5px 13px;opacity:.9}
  #top{position:absolute;right:16px;top:14px;display:flex;gap:6px}
  #top button{background:var(--panel);border:1px solid var(--line);color:var(--fg);
    border-radius:7px;padding:6px 11px;font-size:12px;cursor:pointer}
  #top button:hover{border-color:var(--accent);color:var(--accent)}
</style>
</head>
<body>
<div id="wrap">
  <aside id="side">
    <h1>知识库实体图谱</h1>
    <div class="sub">__TITLE_SUB__</div>
    <div class="kpi">
      <div><b id="k-n">0</b><span>实体节点</span></div>
      <div><b id="k-e">0</b><span>共现关系</span></div>
      <div><b id="k-d">0</b><span>文档来源</span></div>
      <div><b id="k-m">0</b><span>实体提及</span></div>
    </div>
    <div class="sec">搜索实体</div>
    <input id="q" placeholder="输入关键词，如 Agent / 集群 / 检索">
    <div class="sec">关系密度</div>
    <div class="row"><span>稀疏</span><input id="dens" type="range" min="1" max="20" value="12"><span>稠密</span></div>
    <div class="sec">主题岛</div>
    <div class="legend" id="legend"></div>
    <div class="note" id="note"></div>
  </aside>
  <div id="stage">
    <canvas id="cv"></canvas>
    <div id="tip"></div>
    <div id="top">
      <button id="btn-fit">适应窗口</button>
      <button id="btn-reset">重置视图</button>
    </div>
    <div id="hint">拖节点微调 · 拖空白平移 · 滚轮缩放 · 单击看邻居</div>
  </div>
</div>
<script>
const G = __DATA__;
const META = __META__;
const PALETTE = ['--c0','--c1','--c2','--c3','--c4','--c5','--c6','--c7'];
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ---------- 建图 ---------- */
const nodes = G.nodes.map((n,i)=>({...n, x:0, y:0, vx:0, vy:0, r:0}));
const idx = new Map(nodes.map((n,i)=>[n.id,i]));
let links = G.links.map(l=>({s:idx.get(l.source), t:idx.get(l.target), w:l.w}))
                   .filter(l=>l.s!==undefined && l.t!==undefined);
const nbrs = nodes.map(()=>new Set());
links.forEach(l=>{ nbrs[l.s].add(l.t); nbrs[l.t].add(l.s); });

/* 半径：按度数开方，视觉上不失控 */
const maxDeg = Math.max(1, ...nodes.map(n=>n.deg));
nodes.forEach(n=>{ n.r = 4 + 13*Math.sqrt(n.deg/maxDeg); });

/* ---------- 初始化：按主题岛分扇区，避免所有岛叠在中心 ---------- */
const comps = [...new Set(nodes.map(n=>n.comp))].sort((a,b)=>a-b);
const W = ()=>cv.clientWidth, H = ()=>cv.clientHeight;
function seed(){
  const cx=W()/2, cy=H()/2, R=Math.min(W(),H())*0.33;
  const byComp = new Map(comps.map(c=>[c,[]]));
  nodes.forEach(n=>byComp.get(n.comp).push(n));
  comps.forEach((c,ci)=>{
    const a0 = ci/comps.length*Math.PI*2;
    const cr = comps.length>1 ? R*0.62 : 0;
    const ccx = cx + Math.cos(a0)*cr, ccy = cy + Math.sin(a0)*cr;
    const arr = byComp.get(c);
    arr.forEach((n,i)=>{
      const a = i/Math.max(1,arr.length)*Math.PI*2;
      const rr = Math.min(W(),H())*0.10 + (i%7)*4;
      n.x = ccx + Math.cos(a)*rr;
      n.y = ccy + Math.sin(a)*rr;
      n.vx = n.vy = 0;
    });
  });
}

/* ---------- 力学仿真 ---------- */
let alpha = 1;
function tick(){
  const cx=W()/2, cy=H()/2;
  // 斥力（O(n²)，110 个节点完全够）
  for(let i=0;i<nodes.length;i++){
    const a=nodes[i];
    for(let j=i+1;j<nodes.length;j++){
      const b=nodes[j];
      let dx=b.x-a.x, dy=b.y-a.y;
      let d2=dx*dx+dy*dy;
      if(d2<1) { dx=(Math.random()-0.5); dy=(Math.random()-0.5); d2=1; }
      if(d2>42000) continue;
      const d=Math.sqrt(d2);
      const f=(a.r+b.r+18)*a.r*b.r*0.9/d2;
      const ux=dx/d*f, uy=dy/d*f;
      a.vx-=ux; a.vy-=uy; b.vx+=ux; b.vy+=uy;
    }
  }
  // 弹簧
  links.forEach(l=>{
    const a=nodes[l.s], b=nodes[l.t];
    const dx=b.x-a.x, dy=b.y-a.y;
    const d=Math.hypot(dx,dy)||1;
    const rest=44+ (a.r+b.r);
    const k=0.008*Math.min(2, 1+l.w/24);
    const f=(d-rest)*k;
    const ux=dx/d*f, uy=dy/d*f;
    a.vx+=ux; a.vy+=uy; b.vx-=ux; b.vy-=uy;
  });
  // 向心 + 阻尼
  nodes.forEach(n=>{
    n.vx += (cx-n.x)*0.0030;
    n.vy += (cy-n.y)*0.0030;
    if(!n.fixed){
      n.vx*=0.82; n.vy*=0.82;
      n.x+=n.vx*alpha; n.y+=n.vy*alpha;
    }
  });
  alpha = Math.max(0.16, alpha*0.994);
}

/* ---------- 视图变换 ---------- */
let view = {k:1, tx:0, ty:0};
const toScreen = (x,y)=>({x:x*view.k+view.tx, y:y*view.k+view.ty});

/* ---------- 绘制 ---------- */
const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
let hover=-1, picked=-1, q='';
function dpr(){ const d=window.devicePixelRatio||1;
  cv.width=cv.clientWidth*d; cv.height=cv.clientHeight*d;
  ctx.setTransform(d,0,0,d,0,0); }

function colorOf(n){
  const i = comps.indexOf(n.comp);
  // 只有一个主题岛时，按度数渐变上色（枢纽亮、边缘淡），
  // 否则整张图一个色，看不出结构层次。
  if(comps.length === 1){
    const t = Math.max(0, Math.min(1, n.deg / Math.max(1, maxDeg)));
    const hue = 196 + t*96;          // 青蓝 → 紫 → 品红
    return `hsl(${hue.toFixed(0)} 78% ${(64 - t*14).toFixed(0)}%)`;
  }
  return cssv(PALETTE[i>=0? i%PALETTE.length : 0]);
}

function draw(){
  const w=cv.clientWidth, h=cv.clientHeight;
  ctx.clearRect(0,0,w,h);

  // 背景点阵：给画面一点质感，也让平移缩放有参照
  const gs=34, ox=((view.tx%gs)+gs)%gs, oy=((view.ty%gs)+gs)%gs;
  ctx.fillStyle = cssv('--line');
  ctx.globalAlpha = 0.32;
  for(let x=ox; x<w; x+=gs) for(let y=oy; y<h; y+=gs){
    ctx.fillRect(x,y,1.2,1.2);
  }
  ctx.globalAlpha = 1;

  const active = picked>=0 ? new Set([picked,...nbrs[picked]]) : null;
  const placed = [];   // 已放置标签的包围盒，用于避让

  // 边
  links.forEach(l=>{
    const a=nodes[l.s], b=nodes[l.t];
    const p=toScreen(a.x,a.y), q2=toScreen(b.x,b.y);
    let on = true;
    if(active) on = (l.s===picked || l.t===picked);
    const a1 = active ? (on?0.75:0.06) : Math.min(0.5, 0.10+l.w/160);
    ctx.strokeStyle = cssv('--link');
    ctx.globalAlpha = Number.isFinite(a1)? a1 : 0.2;
    ctx.lineWidth = Math.max(0.6, Math.min(3.4, 0.5+l.w/18))*Math.min(1.6,view.k);
    ctx.beginPath(); ctx.moveTo(p.x,p.y); ctx.lineTo(q2.x,q2.y); ctx.stroke();
  });
  ctx.globalAlpha = 1;

  // 节点
  nodes.forEach((n,i)=>{
    const p=toScreen(n.x,n.y);
    if(p.x<-80||p.y<-80||p.x>w+80||p.y>h+80) return;
    const isQ = q && n.id.toLowerCase().includes(q);
    let dim = active ? !active.has(i) : false;
    const base = colorOf(n);
    ctx.globalAlpha = dim ? 0.16 : 1;

    if(i===picked || isQ){
      ctx.beginPath(); ctx.arc(p.x,p.y,n.r*view.k+5,0,7);
      ctx.fillStyle = base; ctx.globalAlpha = 0.22; ctx.fill();
      ctx.globalAlpha = dim?0.16:1;
    }
    ctx.beginPath(); ctx.arc(p.x,p.y,n.r*view.k,0,7);
    ctx.fillStyle = base; ctx.fill();
    if(i===hover||i===picked||isQ){
      ctx.lineWidth = 2.2; ctx.strokeStyle = cssv('--fg'); ctx.stroke();
    }
    // 只给度数前 8 的显示常驻标签，其余靠 hover / 搜索，
    // 否则 109 个标签互相压住，反而看不清
    const labelCut = nodes[Math.min(7, nodes.length-1)].deg;
    if(n.deg >= labelCut || isQ){
      const fs = Math.max(10, Math.min(15, 9*view.k));
      ctx.font = `500 ${fs}px -apple-system,"Microsoft YaHei",sans-serif`;
      const tw = ctx.measureText(n.id).width;
      const ty = p.y + n.r*view.k + fs*0.9;
      // 标签避让：与已画的标签框重叠就整体下移一行，最多让 3 次
      let ly = ty, tries = 0;
      while(tries < 4 && placed.some(q =>
              Math.abs(q.y-ly) < fs+2 &&
              Math.abs(q.x-p.x) < (q.w+tw)/2 + 6)){
        ly += fs + 3; tries++;
      }
      placed.push({x:p.x, y:ly, w:tw});
      ctx.textAlign='center'; ctx.textBaseline='middle';
      ctx.globalAlpha = dim?0.15:0.95;
      ctx.lineWidth=3.5; ctx.strokeStyle = cssv('--bg');
      ctx.strokeText(n.id, p.x, ly);
      ctx.fillStyle = cssv('--fg');
      ctx.fillText(n.id, p.x, ly);
    }
    ctx.globalAlpha=1;
  });
}

/* ---------- 主循环 ---------- */
let running=true;
function loop(){
  if(running){ for(let i=0;i<2;i++) tick(); }
  draw();
  requestAnimationFrame(loop);
}

/* ---------- 交互 ---------- */
function pick(mx,my){
  let best=-1, bd=1e9;
  nodes.forEach((n,i)=>{
    const p=toScreen(n.x,n.y);
    const d=Math.hypot(p.x-mx,p.y-my);
    const rr=Math.max(n.r*view.k, 9);
    if(d<rr && d<bd){ bd=d; best=i; }
  });
  return best;
}
let dragging=null, panning=false, last={x:0,y:0};
cv.addEventListener('mousedown',e=>{
  const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  const i=pick(mx,my);
  if(i>=0){ dragging=nodes[i]; dragging.fixed=true; }
  else { panning=true; last={x:e.clientX,y:e.clientY}; }
});
cv.addEventListener('mousemove',e=>{
  const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  const tip=document.getElementById('tip');
  if(dragging){
    dragging.x=(mx-view.tx)/view.k; dragging.y=(my-view.ty)/view.k;
    dragging.vx=dragging.vy=0; alpha=Math.max(alpha,0.45);
    return;
  }
  if(panning){
    view.tx += e.clientX-last.x; view.ty += e.clientY-last.y;
    last={x:e.clientX,y:e.clientY}; return;
  }
  const i=pick(mx,my);
  if(i!==hover){
    hover=i;
    cv.style.cursor = i>=0?'pointer':'grab';
    if(i>=0){
      const n=nodes[i];
      const ci = comps.indexOf(n.comp);
      tip.innerHTML = `<b>${n.id}</b><div class="d">${n.deg} 条关联 · 出现于 ${n.df} 篇文档 · 主题岛 ${ci+1}</div>`;
      tip.style.opacity=1;
      tip.style.left=(mx+16)+'px'; tip.style.top=(my+12)+'px';
    } else tip.style.opacity=0;
  } else if(i>=0){
    tip.style.left=(mx+16)+'px'; tip.style.top=(my+12)+'px';
  }
});
window.addEventListener('mouseup',()=>{
  if(dragging){ dragging.fixed=false; dragging=null; }
  panning=false;
});
cv.addEventListener('click',e=>{
  const r=cv.getBoundingClientRect();
  const i=pick(e.clientX-r.left, e.clientY-r.top);
  picked = (i===picked)? -1 : i;
});
cv.addEventListener('wheel',e=>{
  e.preventDefault();
  const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  const k0=view.k;
  const k1=Math.max(0.25, Math.min(5, k0*(e.deltaY<0?1.12:0.89)));
  view.tx = mx - (mx-view.tx)*(k1/k0);
  view.ty = my - (my-view.ty)*(k1/k0);
  view.k = k1;
},{passive:false});

document.getElementById('q').addEventListener('input',e=>{
  q = e.target.value.trim().toLowerCase();
  if(q) picked=-1;
});
document.getElementById('dens').addEventListener('input',e=>{
  const th = 21 - Number(e.target.value);   // 滑块右移 = 边权阈值降低 = 更密
  links = G.links.map(l=>({s:idx.get(l.source), t:idx.get(l.target), w:l.w}))
                 .filter(l=>l.s!==undefined && l.t!==undefined && l.w>=th);
  nbrs.forEach(s=>s.clear());
  links.forEach(l=>{ nbrs[l.s].add(l.t); nbrs[l.t].add(l.s); });
  document.getElementById('k-e').textContent = links.length.toLocaleString();
  alpha=0.7;
});
document.getElementById('btn-fit').onclick=fit;
document.getElementById('btn-reset').onclick=()=>{ view={k:1,tx:0,ty:0}; seed(); alpha=1; };

function fit(){
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  nodes.forEach(n=>{ x0=Math.min(x0,n.x-n.r); y0=Math.min(y0,n.y-n.r);
                     x1=Math.max(x1,n.x+n.r); y1=Math.max(y1,n.y+n.r); });
  const pad=Math.max(28, Math.min(cv.clientWidth,cv.clientHeight)*0.07);
  const k=Math.min((cv.clientWidth-pad*2)/(x1-x0||1), (cv.clientHeight-pad*2)/(y1-y0||1), 3.6);
  view.k=k;
  view.tx=(cv.clientWidth-(x0+x1)*k)/2;
  view.ty=(cv.clientHeight-(y0+y1)*k)/2;
}

/* ---------- 初始化 ---------- */
dpr();
window.addEventListener('resize',()=>{ dpr(); });
const totalDocs = META.docs||0;
document.getElementById('k-n').textContent = nodes.length;
document.getElementById('k-e').textContent = links.length.toLocaleString();
document.getElementById('k-d').textContent = totalDocs;
document.getElementById('k-m').textContent = (META.mentions||0).toLocaleString();

const leg = document.getElementById('legend');
comps.forEach((c,i)=>{
  const size = nodes.filter(n=>n.comp===c).length;
  const d=document.createElement('span');
  d.innerHTML = `<i style="background:${cssv(PALETTE[i%PALETTE.length])}"></i>岛 ${i+1} · ${size} 词`;
  leg.appendChild(d);
});

// 🔴 变量名不能叫 `top`：window.top 是只读内置属性，
// 全局 `const top = ...` 会抛 SyntaxError（Identifier 'top' has already been declared），
// 整个脚本一行都不执行 —— 表现为页面全白、KPI 全 0，且控制台之外完全看不出原因。
const topHubs = nodes.slice(0,3).map(n=>n.id).join(' / ');
document.getElementById('note').innerHTML =
  `抽取方式：<b>领域词典注入 + 分词 + 文档频次过滤</b>（v3）。<br>` +
  `词典从 __DOCS__ 篇文档标题挖出 <b>__DICT__</b> 个专有名词，注入分词器后再抽取，` +
  `使「集群五大系统项目」这类短语不再被切成「集群/五大/系统」。<br>` +
  `核心枢纽：<b>${topHubs}</b>` +
  `<br><span style="color:var(--fg-dim)">共现 ≠ 因果。这张图回答「哪些概念经常一起出现」，` +
  `不回答「谁依赖谁」——关系抽取是下一步。</span>`;

seed();
setTimeout(()=>{ for(let i=0;i<260;i++) tick(); fit(); }, 30);
loop();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sub", default="v3 词典增强抽取 · 多跳评测相关文档")
    ap.add_argument("--dict-size", default="0")
    args = ap.parse_args()

    data = json.load(open(args.json, encoding="utf-8"))
    meta = dict(data.get("meta", {}))

    html = (TPL
            .replace("__DATA__", json.dumps({"nodes": data["nodes"],
                                             "links": data["links"]},
                                            ensure_ascii=False))
            .replace("__META__", json.dumps(meta, ensure_ascii=False))
            .replace("__TITLE_SUB__", args.sub)
            .replace("__DOCS__", str(meta.get("docs", "?")))
            .replace("__DICT__", str(args.dict_size)))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("可视化 → %s（%s 字节）" % (args.out, "{:,}".format(os.path.getsize(args.out))))


if __name__ == "__main__":
    main()
