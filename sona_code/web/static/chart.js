"use strict";
/* 按日趋势图：手写 SVG（调用量柱状·左轴 + token 折线·右轴），tooltip 用 title */

function _niceMax(v) {
  if (v <= 1) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const f of [10, 5, 2, 1]) {
    if (v <= f * p) return f * p;
  }
  return 10 * p;
}

function _fmtTick(v) {
  if (v >= 1e6) return (v / 1e6).toFixed(v % 1e6 ? 1 : 0).replace(/\.0$/, "") + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(v % 1e3 ? 1 : 0).replace(/\.0$/, "") + "K";
  return String(Math.round(v));
}

function _svg(tag, attrs) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
  return n;
}

function drawDailyChart(container, byDay) {
  const days = byDay || [];
  const W = 900, H = 280;
  const pad = { l: 48, r: 62, t: 14, b: 32 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const n = Math.max(days.length, 1);
  const step = iw / n;
  const tokens = days.map((d) => (d.prompt_tokens || 0) + (d.completion_tokens || 0));
  const maxC = _niceMax(Math.max(1, ...days.map((d) => d.calls || 0)));
  const maxT = _niceMax(Math.max(1, ...tokens));
  const yC = (c) => pad.t + ih - (ih * c) / maxC;
  const yT = (t) => pad.t + ih - (ih * t) / maxT;

  const svg = _svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img", "aria-label": "近 14 天调用量与 token 趋势" });

  // 横向网格 + 左右轴刻度
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (ih * i) / 4;
    svg.append(_svg("line", { x1: pad.l, x2: pad.l + iw, y1: y, y2: y, class: i === 0 ? "c-axis" : "c-grid" }));
    const tl = _svg("text", { x: pad.l - 8, y: y + 4, "text-anchor": "end", class: "c-tick" });
    tl.textContent = _fmtTick((maxC * (4 - i)) / 4);
    const tr = _svg("text", { x: pad.l + iw + 8, y: y + 4, "text-anchor": "start", class: "c-tick c-tick-r" });
    tr.textContent = _fmtTick((maxT * (4 - i)) / 4);
    svg.append(tl, tr);
  }

  // 柱状（调用量）+ 日期轴
  days.forEach((d, i) => {
    const c = d.calls || 0;
    const x = pad.l + step * i + step * 0.2;
    const w = Math.max(step * 0.6, 2);
    const rect = _svg("rect", {
      x, y: yC(c), width: w,
      height: Math.max(pad.t + ih - yC(c), c > 0 ? 1 : 0),
      class: "c-bar" + (d.errors ? " has-err" : ""),
    });
    const t = _svg("title");
    t.textContent = `${d.date}\n调用 ${c} 次 · 错误 ${d.errors || 0}\n输入 ${d.prompt_tokens || 0} tok · 输出 ${d.completion_tokens || 0} tok`;
    rect.append(t);
    svg.append(rect);
    const lbl = _svg("text", { x: pad.l + step * i + step / 2, y: H - 10, "text-anchor": "middle", class: "c-tick" });
    lbl.textContent = String(d.date || "").slice(5);
    svg.append(lbl);
  });

  // token 折线（右轴）
  if (days.length) {
    const pts = days.map((d, i) => [pad.l + step * i + step / 2, yT((d.prompt_tokens || 0) + (d.completion_tokens || 0))]);
    const path = _svg("path", { d: "M" + pts.map((p) => p.join(",")).join(" L"), class: "c-line" });
    svg.append(path);
    pts.forEach(([px, py], i) => {
      const c = _svg("circle", { cx: px, cy: py, r: 3, class: "c-pt" });
      const t = _svg("title");
      t.textContent = `${days[i].date}\ntoken 合计 ${tokens[i]}`;
      c.append(t);
      svg.append(c);
    });
  }

  container.append(svg);
  // 图例
  const legend = document.createElement("div");
  legend.className = "legend";
  const mk = (cls, text) => {
    const s = document.createElement("span");
    s.className = "sw " + cls;
    const span = document.createElement("span");
    span.textContent = text;
    const wrap = document.createElement("span");
    wrap.append(s, span);
    return wrap;
  };
  legend.append(mk("bar", "调用量（左轴）"), mk("line", "token 合计（右轴）"), mk("err", "当日有错误"));
  container.append(legend);
}
