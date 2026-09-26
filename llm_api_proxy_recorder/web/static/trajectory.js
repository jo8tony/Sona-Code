"use strict";
/* 轨迹页面：会话列表 + 会话轨迹账本（Turn 分组、增量消息、思考/工具调用、
   Inspector 详情、TTFT/Decoding 时间轴、搜索、折叠）。对齐 deepseek-harness 轨迹视图。 */

/* ============================================================ 消息工具 */
function trjTextOf(content) {
  if (content === null || content === undefined) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((p) => (typeof p === "string" ? p : (p && typeof p === "object" && typeof p.text === "string") ? p.text : ""))
      .join(" ")
      .trim();
  }
  try { return JSON.stringify(content); } catch (_) { return String(content); }
}

function trjMsgPreview(m) {
  if (!m || typeof m !== "object") return "";
  if (m.role === "tool") return "工具结果 · " + (m.name || m.tool_call_id || "?");
  const nImg = trjImageParts(m.content).length;
  if (Array.isArray(m.tool_calls) && m.tool_calls.length) {
    const names = m.tool_calls.map((tc) => (tc.function && tc.function.name) || "?").join(", ");
    const base = trjTextOf(m.content);
    return (base ? base + " · " : "") + "调用工具 " + names;
  }
  const text = trjTextOf(m.content);
  if (text) return text + (nImg ? " 📎×" + nImg : "");
  if (nImg) return "📎 图片×" + nImg;
  if (m.reasoning_content) return "（仅思考过程）";
  return "";
}

/* 提取多模态 content 分段中的图片 URL（image_url 类型） */
function trjImageParts(content) {
  if (!Array.isArray(content)) return [];
  const out = [];
  content.forEach((p) => {
    if (p && typeof p === "object" && p.type === "image_url"
      && p.image_url && typeof p.image_url.url === "string") out.push(p.image_url.url);
  });
  return out;
}

/* 图片缩略图网格：点击新窗口打开；加载失败显示占位 */
function trjImgGrid(urls) {
  const grid = el("div", { class: "trj-img-grid" });
  urls.forEach((u) => {
    const img = el("img", { src: u, alt: "消息图片", loading: "lazy" });
    const cell = el("a", { class: "trj-img-cell", href: u, target: "_blank", rel: "noopener noreferrer" }, img);
    img.addEventListener("error", () => cell.replaceChildren(el("span", { class: "trj-img-broken", text: "图片加载失败" })));
    grid.append(cell);
  });
  return grid;
}

const TRJ_ROLE = {
  system: { label: "SYSTEM", cls: "b-dim" },
  user: { label: "USER", cls: "b-ok" },
  assistant: { label: "ASSISTANT", cls: "b-role" },
  tool: { label: "TOOL", cls: "b-warn" },
};

function trjRoleChip(role) {
  const r = TRJ_ROLE[role] || { label: String(role || "?").toUpperCase(), cls: "b-dim" };
  return el("span", { class: "badge " + r.cls, text: r.label });
}

/* ============================================================ 会话列表 */
let trjListState = null;
let trjInspTabApi = null; // 当前 Inspector 的 Tab 组件（供 Summary 内跳转）

async function renderTrajectoryList(view) {
  if (!trjListState) {
    trjListState = { date: "all", q: "", applied: { date: "all", q: "" } };
  }
  const st = trjListState;

  const dateSel = el("select");
  dateSel.append(el("option", { value: "all", text: "全部日期" }));
  const today = lastNDates(14)[0];
  lastNDates(14).forEach((d) => dateSel.append(el("option", { value: d, text: d + (d === today ? "（今天）" : "") })));
  dateSel.value = st.date;

  const qInput = el("input", { type: "text", placeholder: "搜索会话内容 / 模型", value: st.q, class: "w-220" });
  qInput.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });

  function apply() {
    st.date = dateSel.value;
    st.q = qInput.value.trim();
    refresh(false);
  }

  const tableBox = el("div");
  view.replaceChildren(
    el("div", { class: "card filter-bar" },
      el("div", { class: "filter-item" }, el("span", { class: "f-label", text: "日期" }), dateSel),
      el("div", { class: "filter-item" }, el("span", { class: "f-label", text: "关键字" }), qInput),
      el("button", { class: "btn", text: "应用", onclick: apply })),
    tableBox
  );

  async function refresh(silent) {
    const params = new URLSearchParams({ date: st.date || "all" });
    if (st.q) params.set("q", st.q);
    let data;
    try {
      data = await api("trajectory/sessions?" + params.toString(), { silent });
    } catch (e) {
      if (!silent) tableBox.replaceChildren(errorCard("加载会话列表失败：" + e.message, () => refresh(false)));
      return;
    }
    const sessions = data.sessions || [];
    if (!sessions.length) {
      tableBox.replaceChildren(el("section", { class: "card" }, emptyBox("暂无会话", "代理转发对话请求后，同一对话的多次调用会聚合为会话轨迹")));
      return;
    }
    const tb = el("tbody");
    sessions.forEach((s) => {
      const href = "#/trajectory/" + encodeURIComponent(s.session_key);
      tb.append(el("tr", { class: "row-link", onclick: () => { location.hash = href; } },
        el("td", { class: "path-cell", title: s.preview || "", text: s.solo ? "（单次调用）" : (s.preview || "—") }),
        el("td", { class: "mono", title: s.model || "", text: s.model || "—" }),
        el("td", { class: "mono num", text: fmtNum(s.calls) }),
        el("td", { class: "mono num", title: fmtNum(s.prompt_tokens), text: fmtTokens(s.prompt_tokens) }),
        el("td", { class: "mono num", title: fmtNum(s.completion_tokens), text: fmtTokens(s.completion_tokens) }),
        el("td", { class: "mono dim", title: s.last_started_at || "", text: fmtTime(s.last_started_at) }),
        el("td", null, statusCell({ status: s.last_status, status_code: null })),
        el("td", null, el("a", { class: "btn btn-xs", href, text: "轨迹" }))));
    });
    tableBox.replaceChildren(el("section", { class: "card" },
      el("div", { class: "card-head-row" },
        el("h2", { text: "会话（" + fmtNum(data.total || sessions.length) + "）" }),
        el("span", { class: "empty-hint", text: "按 模型 + system + 首条用户消息 自动聚合" })),
      el("div", { class: "tbl-wrap" }, el("table", { class: "tbl" },
        el("thead", null, el("tr", null,
          el("th", { text: "首条消息" }), el("th", { text: "模型" }), el("th", { text: "轮数", class: "num" }),
          el("th", { text: "输入 tok", class: "num" }), el("th", { text: "输出 tok", class: "num" }),
          el("th", { text: "最后活动" }), el("th", { text: "状态" }), el("th", { text: "操作" }))),
        tb))));
  }

  await refresh(false);
}

/* ============================================================ 会话轨迹 */
async function renderTrajectorySession(view, key, options = {}) {
  const retry = options.retry || (() => route());
  let data;
  try {
    data = options.data || await api("trajectory/sessions/" + encodeURIComponent(key));
    if (options.alive && !options.alive()) return;
  } catch (e) {
    if (options.alive && !options.alive()) return;
    view.replaceChildren(errorCard(e.status === 404 ? "会话不存在" : "加载轨迹失败：" + e.message, retry));
    return;
  }
  const turns = data.turns || [];
  if (!turns.length) {
    view.replaceChildren(errorCard("该会话暂无已定稿的调用记录（后台解析可能尚未完成，稍后刷新试试）", retry));
    return;
  }

  const collapsed = new Set();
  let selected = null; // { kind: "msg"|"resp"|"toolcall", turn, payload, cellKey }
  let needle = "";
  const lastTabByKind = {}; // Tab 历史：{ resp: "usage", msg: ..., toolcall: ... }，切换条目时恢复

  // ---- 顶部：返回 + 概要
  const cum = data.cumulative_usage || {};
  view.replaceChildren(
    el("div", { class: "detail-top" },
      ...(options.embedded ? [] : [el("a", { class: "btn btn-ghost", href: "#/trajectory", text: "← 返回会话列表" })]),
      el("span", { class: "mono dim", text: data.session_key })),
    el("div", { class: "sum-bar" },
      sumItem("模型", data.model || "—"),
      sumItem("轮数", fmtNum(turns.length)),
      sumItem("累计输入", fmtTokens(cum.prompt_tokens)),
      sumItem("累计输出", fmtTokens(cum.completion_tokens)),
      sumItem("累计合计", fmtTokens(cum.total_tokens)),
      sumItem("开始", fmtTime(data.first_started_at)),
      sumItem("最后活动", fmtTime(data.last_started_at)))
  );

  // ---- 时间轴（TTFT 等待 + 生成解码 分段；支持 等宽/按时长 两种投影）
  const timelineCard = el("section", { class: "card" },
    el("div", { class: "card-head-row" },
      el("h2", { text: "时间轴" }),
      el("span", { class: "empty-hint", text: "浅色 = 等待（TTFT）· 深色 = 生成解码" }),
      el("span", { class: "filter-spacer" })));
  const tlModeBtn = el("button", { class: "btn btn-xs", type: "button", text: "切换为等宽", title: "在 等宽 / 按时长 两种投影间切换" });
  const tlHint = timelineCard.querySelector(".empty-hint");
  timelineCard.querySelector(".card-head-row").append(tlModeBtn);
  const tlRows = el("div", { class: "trj-timeline" });
  timelineCard.append(tlRows);

  let tlMode = "duration"; // duration（按实际时长）| sequence（等宽）
  function drawTimeline() {
    tlRows.replaceChildren();
    const maxDur = Math.max(1, ...turns.map((t) => Number(t.duration_ms) || 0));
    turns.forEach((t) => {
      const dur = Number(t.duration_ms) || 0;
      const ttft = Math.min(Math.max(Number(t.ttft_ms) || 0, 0), dur);
      const decode = Math.max(0, dur - ttft);
      const wTotal = tlMode === "duration" ? (dur / maxDur) * 100 : 100;
      const wTtft = dur > 0 ? (ttft / dur) * 100 : 0;
      const tps = t.tokens_per_sec != null ? t.tokens_per_sec.toFixed(1) + " tok/s" : "—";
      const bar = el("div", { class: "trj-tl-bar", style: "width:" + wTotal + "%" },
        el("span", { class: "trj-tl-wait", style: "width:" + wTtft + "%" }),
        el("span", { class: "trj-tl-decode", style: "width:" + (100 - wTtft) + "%" }));
      bar.title = `Turn ${t.turn_no} · Total ${fmtMs(dur)} · TTFT ${fmtMs(ttft)} · Decoding ${fmtMs(decode)} · ${tps}`;
      tlRows.append(el("div", {
        class: "trj-tl-row",
        onclick: () => {
          const sec = $("#trj-turn-" + t.turn_no);
          if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
          if (t.response_message && typeof t.response_message === "object") {
            selectCell({ kind: "resp", turn: t, payload: t.response_message, cellKey: "resp-" + t.turn_no });
          }
        },
      },
        el("span", { class: "trj-tl-label mono", text: "#" + t.turn_no }),
        el("div", { class: "trj-tl-track" }, bar),
        el("span", { class: "trj-tl-dur mono dim", text: fmtMs(dur) })));
    });
  }
  tlModeBtn.addEventListener("click", () => {
    tlMode = tlMode === "duration" ? "sequence" : "duration";
    tlModeBtn.textContent = tlMode === "duration" ? "切换为等宽" : "切换为按时长";
    tlHint.textContent = tlMode === "duration"
      ? "浅色 = 等待（TTFT）· 深色 = 生成解码，宽度按实际时长"
      : "等宽投影（每轮等长，看调用节奏），分段仍按 TTFT 比例";
    drawTimeline();
  });
  drawTimeline();

  // ---- Token 用量柱状图（每轮 输入/输出 双柱）
  const usageCard = el("section", { class: "card" },
    el("div", { class: "card-head-row" },
      el("h2", { text: "Token 用量" }),
      el("span", { class: "empty-hint", text: "浅色 = 输入 · 深色 = 输出，点击跳转轮次" })));
  const ucGrid = el("div", { class: "trj-usage-chart" });
  const maxTok = Math.max(1, ...turns.map((t) => {
    const u = t.usage || {};
    return Math.max(Number(u.prompt_tokens) || 0, Number(u.completion_tokens) || 0);
  }));
  turns.forEach((t) => {
    const u = t.usage || {};
    const pin = Number(u.prompt_tokens) || 0;
    const pout = Number(u.completion_tokens) || 0;
    ucGrid.append(el("div", {
      class: "trj-uc-col",
      title: "Turn " + t.turn_no + " · 输入 " + fmtNum(pin) + " · 输出 " + fmtNum(pout),
      onclick: () => { const sec = $("#trj-turn-" + t.turn_no); if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" }); },
    },
      el("div", { class: "trj-uc-bars" },
        el("span", { class: "trj-uc-in", style: "height:" + Math.max(3, (pin / maxTok) * 100) + "%" }),
        el("span", { class: "trj-uc-out", style: "height:" + Math.max(3, (pout / maxTok) * 100) + "%" })),
      el("span", { class: "trj-uc-label mono", text: "#" + t.turn_no })));
  });
  usageCard.append(ucGrid);

  // ---- 工具栏
  const searchInput = el("input", { type: "text", placeholder: "在轨迹中搜索（过滤 Turn）", class: "w-220" });
  searchInput.addEventListener("input", () => {
    needle = searchInput.value.trim().toLowerCase();
    redrawLedger();
  });
  const toolbar = el("div", { class: "card filter-bar" },
    el("div", { class: "filter-item" }, el("span", { class: "f-label", text: "搜索" }), searchInput),
    el("button", { class: "btn", text: "全部展开", onclick: () => { collapsed.clear(); redrawLedger(); } }),
    el("button", { class: "btn", text: "全部折叠", onclick: () => { turns.forEach((t) => collapsed.add(t.turn_no)); redrawLedger(); } }));

  // ---- 布局：账本（左） + Inspector（右）
  const ledgerBox = el("div", { class: "trj-ledger" });
  const inspectorBox = el("aside", { class: "trj-inspector card" });
  const layout = el("div", { class: "trj-layout" }, ledgerBox, inspectorBox);
  view.append(timelineCard, usageCard, toolbar, layout);

  // ---- 选中某个账本条目（展开所在折叠 Turn 并可选滚动定位）
  function selectCell(sel, scroll) {
    selected = sel;
    if (sel && sel.turn) collapsed.delete(sel.turn.turn_no);
    redrawLedger();
    if (scroll) {
      const node = ledgerBox.querySelector('.trj-cell[data-key="' + sel.cellKey + '"]')
        || $("#trj-turn-" + sel.turn.turn_no);
      if (node) node.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  // ---- Inspector 渲染
  function inspectorDefault() {
    inspectorBox.replaceChildren(
      el("h2", { text: "会话概览" }),
      el("div", { class: "meta-line", text: "点击左侧账本中的任意条目查看详情" }),
      el("div", { class: "msg-line" },
        el("span", { class: "chip", text: "轮数 " + turns.length }),
        el("span", { class: "chip", text: "输入 " + fmtTokens(cum.prompt_tokens) }),
        el("span", { class: "chip", text: "输出 " + fmtTokens(cum.completion_tokens) })));
  }

  /* ---- Inspector 面板（Tab 化，对齐 deepseek-harness Event Details） */
  const inspSec = (...kids) => el("div", { class: "trj-insp-sec" }, ...kids);

  // 错误 / 解析警告 banner
  const turnBanners = (t) => [
    t.history_incomplete ? el("div", { class: "banner banner-warn" }, "关联的 Responses 历史未完整捕获；仅展示已有记录。") : null,
    t.error ? el("div", { class: "banner banner-err", style: "margin-top:10px" },
      el("b", { text: "错误：" }), (t.error.type || "") + " " + (t.error.message || "")) : null,
    t.parse_error ? el("div", { class: "banner banner-warn", style: "margin-top:10px" },
      el("b", { text: "解析警告：" }), t.parse_error) : null,
  ];

  // 时序面板（开始时间可点击：本地时间 ↔ Unix 时间戳）
  function timingPanel(t) {
    const timeVal = el("span", {
      class: "trj-clickable mono",
      text: fmtTime(t.started_at),
      title: "点击切换 Unix 时间戳",
    });
    let unixMode = false;
    timeVal.addEventListener("click", () => {
      unixMode = !unixMode;
      const ts = Date.parse(t.started_at);
      timeVal.textContent = unixMode && Number.isFinite(ts)
        ? String(Math.floor(ts / 1000)) : fmtTime(t.started_at);
    });
    return inspSec(
      el("h3", { text: "时序" }),
      el("div", { class: "trj-kv" },
        el("span", { class: "trj-kv-k", text: "开始时间" }), timeVal),
      trjKv("首字节", fmtMs(t.first_byte_ms)),
      trjKv("TTFT", fmtMs(t.ttft_ms)),
      trjKv("解码耗时", t.decode_ms != null ? t.decode_ms.toFixed(1) + " ms" : "—"),
      trjKv("生成（输出）", t.decode_ms != null ? fmtMs(t.decode_ms) : "—"),
      trjKv("总耗时", fmtMs(t.duration_ms)),
      trjKv("吞吐", t.tokens_per_sec != null ? t.tokens_per_sec.toFixed(1) + " tok/s" : "—"));
  }

  // Token 用量面板（本次 / 会话累计）
  function usagePanel(t) {
    const u = t.usage || {};
    const cu = t.cumulative_usage || {};
    const contentTok = Number.isFinite(u.completion_tokens) && Number.isFinite(u.reasoning_tokens)
      ? fmtNum(u.completion_tokens - u.reasoning_tokens) : null;
    return inspSec(
      el("h3", { text: "本次请求" }),
      trjKv("输入", fmtNum(u.prompt_tokens)),
      u.cached_tokens != null ? trjKv("缓存命中", fmtNum(u.cached_tokens)) : null,
      u.reasoning_tokens != null ? trjKv("思考", fmtNum(u.reasoning_tokens)) : null,
      trjKv("输出", fmtNum(u.completion_tokens)),
      contentTok != null ? trjKv("正文（输出−思考）", contentTok) : null,
      trjKv("合计", fmtNum(u.total_tokens)),
      el("h3", { text: "会话累计" }),
      trjKv("输入", fmtNum(cu.prompt_tokens)),
      trjKv("输出", fmtNum(cu.completion_tokens)),
      trjKv("合计", fmtNum(cu.total_tokens)));
  }

  // resp（响应 = 请求级）Tab 集：10 个
  function respTabs(turn) {
    const m = turn.response_message || {};
    const idx = turns.indexOf(turn);
    const prevT = idx > 0 ? turns[idx - 1] : null;
    return [
      {
        id: "summary", label: "Summary",
        render: () => {
          const u = turn.usage || {};
          const tcCount = (Array.isArray(m.tool_calls) && m.tool_calls.length) || 0;
          const tokChips = el("div", { class: "msg-line" },
            el("span", { class: "chip", text: "in " + fmtTokens(u.prompt_tokens) }),
            u.cached_tokens != null ? el("span", { class: "chip chip-dim", text: "cached " + fmtTokens(u.cached_tokens) }) : null,
            u.reasoning_tokens != null ? el("span", { class: "chip chip-dim", text: "think " + fmtTokens(u.reasoning_tokens) }) : null,
            el("span", { class: "chip", text: "out " + fmtTokens(u.completion_tokens) }),
            el("span", { class: "chip chip-dim", text: "total " + fmtTokens(u.total_tokens) }));
          return inspSec(
            el("h3", { text: "请求" }),
            trjKv("状态", (turn.status || "—") + (turn.status_code != null ? " · HTTP " + turn.status_code : "")),
            trjKv("模型", turn.model || "—"),
            trjKv("上游", turn.upstream_name || "—"),
            trjKv("路径", (turn.method || "—") + " " + (turn.path || "—")),
            trjKv("流式", turn.stream ? "是" : "否"),
            trjKv("上下文消息数", fmtNum(turn.messages_count || 0)),
            turn.finish_reason ? trjKv("finish_reason", turn.finish_reason) : null,
            tcCount ? trjKvJump("工具调用", tcCount + " 个", "tools") : null,
            ...turnBanners(turn),
            el("div", { class: "card-head-row" }, el("h3", { text: "Token（本次）" }), trjJumpLink("usage")),
            tokChips,
            el("div", { class: "card-head-row" }, el("h3", { text: "快捷入口" })),
            el("div", { class: "msg-line" },
              trjJumpChip("Usage", "usage"), trjJumpChip("Timing", "timing"),
              trjJumpChip("Tools", "tools"), trjJumpChip("Options", "options"),
              trjJumpChip("Source", "source"), trjJumpChip("Headers", "headers")));
        },
      },
      {
        id: "preview", label: "Preview",
        render: () => {
          const contentText = trjTextOf(m.content);
          return inspSec(
            el("h3", { text: "响应内容" }),
            el("div", { class: "msg-line" },
              trjRoleChip("assistant"),
              turn.finish_reason ? el("span", { class: "chip chip-dim", text: "finish: " + turn.finish_reason }) : null,
              turn.stream ? el("span", { class: "chip chip-dim", text: "流式" }) : null),
            m.reasoning_content
              ? el("details", { class: "raw-chunks" },
                  el("summary", { text: "思考过程（" + m.reasoning_content.length + " 字，默认折叠）" }),
                  el("pre", { class: "pre-block pre-reason", text: m.reasoning_content }))
              : null,
            contentText !== ""
              ? el("div", null,
                  el("div", { class: "card-head-row" }, el("h4", { text: "渲染视图" }), trjCopyBtn(contentText)),
                  trjMarkdown(contentText),
                  el("details", { class: "raw-chunks" },
                    el("summary", { text: "原始文本（Markdown 源码）" }),
                    el("pre", { class: "pre-block pre-content", text: contentText })))
              : null,
            Array.isArray(m.tool_calls) && m.tool_calls.length ? trjToolCallList(m.tool_calls) : null,
            contentText === "" && !(Array.isArray(m.tool_calls) && m.tool_calls.length) && !m.reasoning_content
              ? el("div", { class: "empty-hint", text: "（无组装内容）" }) : null,
            ...turnBanners(turn));
        },
      },
      {
        id: "sysprompt", label: "System Prompt",
        render: () => {
          const cur = turn.system || "";
          const box = inspSec();
          if (!cur) {
            box.append(el("div", { class: "empty-hint", text: "本轮请求无 system 消息" }));
            return box;
          }
          box.append(el("div", { class: "card-head-row" },
            el("h3", { text: "System Prompt（" + fmtNum(cur.length) + " 字）" }),
            trjCopyBtn(cur)));
          if (prevT && prevT.system !== undefined && prevT.system !== cur) {
            box.append(
              el("div", { class: "msg-line" },
                el("span", { class: "badge b-warn", text: "相对上一轮已变更" }),
                el("span", { class: "empty-hint", text: "行级 diff：− 删除 / + 新增" })),
              trjDiffView(prevT.system || "", cur));
          }
          box.append(
            el("details", { class: "raw-chunks", open: "" },
              el("summary", { text: "纯文本" }),
              el("pre", { class: "pre-block pre-content", text: cur })),
            el("details", { class: "raw-chunks" },
              el("summary", { text: "渲染视图" }),
              trjMarkdown(cur)));
          return box;
        },
      },
      {
        id: "tools", label: "Tools",
        render: () => {
          const tools = turn.tools;
          const box = inspSec();
          if (!Array.isArray(tools) || !tools.length) {
            box.append(el("div", { class: "empty-hint", text: "本轮请求未声明工具（tools 为空）" }));
            return box;
          }
          box.append(el("h3", { text: "工具目录（" + tools.length + "）" }));
          // 与上一轮工具名集合对比：新增 / 移除
          if (prevT) {
            const nameOf = (x) => (x && x.function && x.function.name) || (x && x.name) || "";
            const curNames = new Set(tools.map(nameOf).filter(Boolean));
            const prevNames = new Set((Array.isArray(prevT.tools) ? prevT.tools : []).map(nameOf).filter(Boolean));
            const added = [...curNames].filter((n) => !prevNames.has(n));
            const removed = [...prevNames].filter((n) => !curNames.has(n));
            if (added.length || removed.length) {
              const delta = el("div", { class: "trj-tool-delta" },
                el("span", { class: "empty-hint", text: "相对上一轮：" }));
              added.forEach((n) => delta.append(el("span", { class: "badge b-ok", text: "+ " + n })));
              removed.forEach((n) => delta.append(el("span", { class: "badge b-err", text: "− " + n })));
              box.append(delta);
            }
          }
          tools.forEach((t) => {
            const fn = (t && t.function && typeof t.function === "object") ? t.function : (t || {});
            const det = el("details", { class: "raw-chunks" },
              el("summary", null, el("span", { class: "mono", text: fn.name || t.name || "?" })));
            if (fn.description) det.append(_trjBlock("描述", fn.description));
            det.append(
              el("h4", { text: "parameters" }),
              fn.parameters != null ? jsonViewer(fn.parameters) : el("div", { class: "empty-hint", text: "（无）" }));
            box.append(det);
          });
          return box;
        },
      },
      {
        id: "options", label: "Options",
        render: () => {
          const p = turn.params;
          const box = inspSec();
          if (!p || typeof p !== "object" || !Object.keys(p).length) {
            box.append(el("div", { class: "empty-hint", text: "本轮请求无额外参数（model / stream / messages / tools 之外）" }));
            return box;
          }
          box.append(el("h3", { text: "请求参数" }));
          Object.entries(p).forEach(([k, v]) => {
            if (v === null || ["string", "number", "boolean"].includes(typeof v)) box.append(trjKv(k, String(v)));
          });
          box.append(el("h3", { text: "完整 Raw" }), jsonViewer(p));
          return box;
        },
      },
      { id: "usage", label: "Usage", render: () => usagePanel(turn) },
      { id: "timing", label: "Timing", render: () => timingPanel(turn) },
      {
        id: "source", label: "Source",
        render: () => {
          const h = turn.request_headers || {};
          const pick = (...keys) => {
            for (const k of keys) {
              if (h[k] != null && h[k] !== "") return h[k];
              if (h[k.toLowerCase()] != null && h[k.toLowerCase()] !== "") return h[k.toLowerCase()];
            }
            return "";
          };
          const ua = pick("User-Agent");
          const host = pick("Host");
          const origin = pick("Origin");
          return inspSec(
            el("h3", { text: "客户端" }),
            trjKv("User-Agent", ua || "—"),
            host ? trjKv("Host", host) : null,
            origin ? trjKv("Origin", origin) : null,
            el("h3", { text: "服务端" }),
            trjKv("上游", turn.upstream_name || "—"),
            trjKv("路径", (turn.method || "—") + " " + (turn.path || "—")),
            trjKv("HTTP 状态", turn.status_code != null ? String(turn.status_code) : "—"),
            trjKv("流式", turn.stream ? "是" : "否"),
            turn.finish_reason ? trjKv("finish_reason", turn.finish_reason) : null,
            el("h3", { text: "会话归属" }),
            trjKv("session_key", data.session_key || "—"),
            trjKv("上下文消息数", fmtNum(turn.messages_count || 0)),
            trjKv("轮次", "Turn " + turn.turn_no));
        },
      },
      {
        id: "headers", label: "Headers",
        render: () => inspSec(
          el("h3", { text: "请求头（已脱敏）" }), headersTable(turn.request_headers),
          el("h3", { text: "响应头" }), headersTable(turn.response_headers, "（未记录响应头，可在设置中开启 record_response_headers）")),
      },
      {
        id: "chunks", label: "Chunks",
        render: async () => {
          const box = inspSec(el("h3", { text: "SSE 分块（" + fmtNum(turn.chunk_count || 0) + "）" }));
          const rec = await ensureRecord(turn);
          const chunks = rec && rec.response && rec.response.raw_chunks;
          if (!Array.isArray(chunks) || !chunks.length) {
            box.append(el("div", { class: "empty-hint", text: "未记录原始分块（可在设置中开启 record_raw_chunks）" }));
            return box;
          }
          box.append(rawChunksDetails(chunks));
          return box;
        },
      },
      {
        id: "raw", label: "Raw",
        render: async () => {
          const rec = await ensureRecord(turn);
          const req = (rec && rec.request) || {};
          const resp = (rec && rec.response) || {};
          let reqText = "";
          try { reqText = typeof req.body === "string" ? req.body : JSON.stringify(req.body); } catch (_) { /* 忽略 */ }
          let respText = "";
          try { respText = JSON.stringify(resp.content); } catch (_) { /* 忽略 */ }
          return el("div", null,
            inspSec(
              el("div", { class: "card-head-row" }, el("h3", { text: "原始请求体" }), trjCopyBtn(reqText)),
              bodyViewer(req.body, req.body_truncated)),
            inspSec(
              el("div", { class: "card-head-row" }, el("h3", { text: "原始响应内容" }), trjCopyBtn(respText)),
              resp.content === null || resp.content === undefined
                ? el("div", { class: "empty-hint", text: "（无内容）" })
                : (typeof resp.content === "string"
                    ? el("pre", { class: "pre-block", text: resp.content })
                    : jsonViewer(resp.content))));
        },
      },
    ];
  }

  // msg（user / system / tool 消息）Tab 集
  function msgTabs(turn, m) {
    let rawText = "";
    try { rawText = JSON.stringify(m, null, 2); } catch (_) { /* 忽略 */ }
    return [
      {
        id: "summary", label: "Summary",
        render: () => inspSec(
          el("h3", { text: "消息" }),
          trjKv("角色", m.role || "—"),
          m.name ? trjKv("name", m.name) : null,
          m.tool_call_id ? trjKv("tool_call_id", m.tool_call_id) : null,
          trjKv("内容长度", fmtNum(trjTextOf(m.content).length) + " 字"),
          Array.isArray(m.tool_calls) && m.tool_calls.length ? trjKv("工具调用", m.tool_calls.length + " 个") : null,
          m.reasoning_content ? trjKv("思考长度", fmtNum(m.reasoning_content.length) + " 字") : null,
          trjKv("所属", "Turn " + turn.turn_no)),
      },
      {
        id: "preview", label: "Preview",
        render: () => {
          const text = trjTextOf(m.content);
          const imgs = trjImageParts(m.content);
          return inspSec(
            el("h3", { text: "内容" }),
            el("div", { class: "msg-line" }, trjRoleChip(m.role), m.name ? el("span", { class: "chip chip-dim", text: m.name }) : null),
            m.reasoning_content ? _trjBlock("思考过程", m.reasoning_content, "pre-reason") : null,
            text
              ? (m.role === "system" || m.role === "user" || m.role === "assistant"
                  ? el("div", null,
                      trjMarkdown(text),
                      el("details", { class: "raw-chunks" },
                        el("summary", { text: "原始文本（Markdown 源码）" }),
                        el("pre", { class: "pre-block pre-content", text: text })))
                  : _trjBlock("内容", text, "pre-content"))
              : null,
            imgs.length ? el("div", { class: "trj-insp-sec" },
              el("h4", { text: "图片（" + imgs.length + "）" }), trjImgGrid(imgs)) : null,
            Array.isArray(m.tool_calls) ? trjToolCallList(m.tool_calls) : null);
        },
      },
      {
        id: "raw", label: "Raw",
        render: () => inspSec(
          el("div", { class: "card-head-row" }, el("h3", { text: "消息 JSON" }), trjCopyBtn(rawText)),
          jsonViewer(m)),
      },
    ];
  }

  // toolcall Tab 集（Result 跨 Turn 关联工具结果消息）
  function toolCallTabs(turn, tc) {
    const fn = tc.function || {};
    let args;
    try { args = JSON.parse(fn.arguments); } catch (_) { args = fn.arguments; }
    let argsText = "";
    try { argsText = typeof fn.arguments === "string" ? fn.arguments : JSON.stringify(fn.arguments); } catch (_) { /* 忽略 */ }
    return [
      {
        id: "summary", label: "Summary",
        render: () => inspSec(
          el("h3", { text: "工具调用" }),
          trjKv("类型", tc.type || "function"),
          trjKv("函数", fn.name || "—"),
          tc.id ? trjKv("id", tc.id) : null,
          trjKv("所属", "Turn " + turn.turn_no)),
      },
      {
        id: "payload", label: "Payload",
        render: () => inspSec(
          el("div", { class: "card-head-row" }, el("h3", { text: "参数" }), trjCopyBtn(argsText)),
          typeof args === "string" ? el("pre", { class: "pre-block", text: args }) : jsonViewer(args)),
      },
      {
        id: "result", label: "Result",
        render: () => {
          const found = tc.id ? findToolResult(turns, turn.turn_no, tc.id) : null;
          if (!found) {
            return inspSec(el("div", { class: "empty-hint", text: "未找到对应工具结果（后续轮次请求中无 tool_call_id 匹配的 role=tool 消息）" }));
          }
          const tm = found.message;
          const locateBtn = el("button", { class: "btn btn-xs", type: "button", text: "定位到消息 →", title: "在账本中选中并滚动到该工具结果消息" });
          locateBtn.addEventListener("click", () => {
            const idx = (found.turn.new_messages || []).indexOf(tm);
            const cellKey = idx >= 0 ? "msg-" + found.turn.turn_no + "-" + idx : null;
            selectCell({ kind: "msg", turn: found.turn, payload: tm, cellKey: cellKey || "msg-" + found.turn.turn_no + "-x" }, true);
          });
          return inspSec(
            el("div", { class: "card-head-row" },
              el("h3", { text: "工具结果 · Turn " + found.turn.turn_no }),
              el("span", null, trjCopyBtn(trjTextOf(tm.content)), locateBtn)),
            el("div", { class: "msg-line" }, trjRoleChip("tool"),
              tm.name ? el("span", { class: "chip chip-dim", text: tm.name }) : null,
              tm.tool_call_id ? el("span", { class: "mono dim", text: tm.tool_call_id }) : null),
            tm.content != null ? _trjBlock("结果内容", trjTextOf(tm.content), "pre-content") : null,
            el("h3", { text: "Raw" }), jsonViewer(tm));
        },
      },
      { id: "timing", label: "Timing", render: () => timingPanel(turn) },
    ];
  }

  function renderInspector() {
    if (!selected) { inspectorDefault(); return; }
    const { kind, turn, payload } = selected;
    let title, tabs;
    if (kind === "msg") {
      title = "消息 · Turn " + turn.turn_no;
      tabs = msgTabs(turn, payload);
    } else if (kind === "resp") {
      title = "响应 · Turn " + turn.turn_no;
      tabs = respTabs(turn);
    } else {
      title = "工具调用 · Turn " + turn.turn_no;
      tabs = toolCallTabs(turn, payload);
    }
    const lastId = lastTabByKind[kind]; // 先捕获（构造时默认激活会覆盖历史）
    const tabApi = trjTabs(tabs, (id) => { lastTabByKind[kind] = id; });
    trjInspTabApi = tabApi;
    inspectorBox.replaceChildren(
      el("h2", { text: title }),
      el("div", { class: "meta-line" },
        el("a", { href: "#/calls/" + encodeURIComponent(turn.call_id), text: "查看完整调用记录 →" })),
      tabApi.root);
    // Tab 历史：恢复该类型最近访问的 Tab（若在新 Tab 集中存在）
    if (lastId && lastId !== tabs[0].id && tabs.some((t) => t.id === lastId)) tabApi.activate(lastId);
  }

  // ---- 账本渲染
  function cellNode(kind, turn, payload, badge, preview, extra, cellKey) {
    const node = el("div", {
      class: "trj-cell" + (selected && selected.cellKey === cellKey ? " sel" : ""),
      onclick: () => { selected = { kind, turn, payload, cellKey }; redrawSelection(); },
    },
      el("span", { class: "trj-cell-badge" }, badge),
      el("span", { class: "trj-cell-text", title: preview }, trjHighlight(preview, needle)));
    node.dataset.key = cellKey;
    if (extra) node.append(extra);
    return node;
  }

  function turnSection(t) {
    const u = t.usage || {};
    const cu = t.cumulative_usage || {};
    const respMsg = t.response_message;
    // 与上一轮比较：system / tools 是否变更（Turn 头 chip + System Prompt diff 用）
    const prevT = t.turn_no > 1 ? turns[t.turn_no - 2] : null;
    const sysChanged = !!prevT && t.system !== prevT.system;
    let toolsChanged = false;
    if (prevT) {
      try { toolsChanged = JSON.stringify(t.tools || null) !== JSON.stringify(prevT.tools || null); }
      catch (_) { toolsChanged = true; }
    }
    const sec = el("section", {
      class: "card trj-turn" + (collapsed.has(t.turn_no) ? " collapsed" : ""),
      id: "trj-turn-" + t.turn_no,
    });
    // Turn 头（点击折叠/展开）
    const head = el("div", {
      class: "trj-turn-head",
      onclick: (e) => {
        if (e.target.closest("a")) return;
        if (collapsed.has(t.turn_no)) collapsed.delete(t.turn_no); else collapsed.add(t.turn_no);
        sec.classList.toggle("collapsed");
      },
    },
      el("span", { class: "trj-turn-no mono", text: "#" + t.turn_no }),
      statusCell(t, true),
      el("span", { class: "mono dim", text: t.model || "—" }),
      el("span", { class: "chip chip-dim", text: fmtMs(t.duration_ms) }),
      t.ttft_ms != null ? el("span", { class: "chip chip-dim", text: "TTFT " + fmtMs(t.ttft_ms) }) : null,
      t.tokens_per_sec != null ? el("span", { class: "chip chip-dim", text: t.tokens_per_sec.toFixed(1) + " tok/s" }) : null,
      el("span", { class: "chip", text: `in ${fmtTokens(u.prompt_tokens)} · out ${fmtTokens(u.completion_tokens)}` }),
      el("span", { class: "chip chip-dim", text: `累计 ${fmtTokens(cu.total_tokens)}` }),
      t.finish_reason ? el("span", { class: "chip chip-dim", text: t.finish_reason }) : null,
      sysChanged ? el("span", { class: "badge b-warn", title: "本轮 system 消息相对上一轮已变更", text: "sys 变更" }) : null,
      toolsChanged ? el("span", { class: "badge b-warn", title: "本轮 tools 声明相对上一轮已变更", text: "tools 变更" }) : null,
      el("span", { class: "filter-spacer" }),
      el("span", { class: "mono dim", title: t.started_at || "", text: fmtTime(t.started_at) }),
      el("a", { class: "btn btn-xs", href: "#/calls/" + encodeURIComponent(t.call_id), text: "调用" }));
    // 账本条目
    const cells = el("div", { class: "trj-cells" });
    (t.new_messages || []).forEach((m, i) => {
      cells.append(cellNode("msg", t, m, trjRoleChip(m.role),
        trjMsgPreview(m) || "（空）",
        m.name ? el("span", { class: "chip chip-dim trj-cell-extra", text: m.name }) : null,
        "msg-" + t.turn_no + "-" + i));
    });
    if (!t.new_messages || !t.new_messages.length) {
      cells.append(el("div", { class: "trj-cell dim" }, el("span", { class: "trj-cell-badge" }, el("span", { class: "badge b-dim", text: "CONTEXT" })), el("span", { class: "trj-cell-text dim", text: "（与上一轮请求一致，无新增消息）" })));
    }
    if (t.error && t.error.type === "upstream_unreachable") {
      cells.append(el("div", { class: "trj-cell err" }, el("span", { class: "trj-cell-text", text: "上游不可达：" + (t.error.message || "") })));
    }
    if (respMsg && typeof respMsg === "object") {
      if (respMsg.reasoning_content) {
        cells.append(cellNode("resp", t, respMsg,
          el("span", { class: "badge b-think", text: "THINKING" }),
          `思考过程 · ${respMsg.reasoning_content.length} 字（点击展开详情）`,
          null,
          "think-" + t.turn_no));
      }
      if (respMsg.tool_calls) {
        respMsg.tool_calls.forEach((tc, j) => {
          const fn = (tc && tc.function) || {};
          cells.append(cellNode("toolcall", t, tc,
            el("span", { class: "chip chip-tool", text: "TOOL CALL" }),
            (fn.name || "?") + " " + trjTextOf(fn.arguments).slice(0, 120),
            null,
            "tc-" + t.turn_no + "-" + j));
        });
      }
      const contentText = trjTextOf(respMsg.content);
      if (contentText || !respMsg.tool_calls) {
        cells.append(cellNode("resp", t, respMsg,
          trjRoleChip("assistant"),
          contentText || (respMsg.reasoning_content ? "（仅思考过程，无正文）" : "（无内容）"),
          null,
          "resp-" + t.turn_no));
      }
    } else if (!t.error) {
      cells.append(el("div", { class: "trj-cell dim" }, el("span", { class: "trj-cell-text dim", text: "（响应未解析或为空）" })));
    }
    sec.append(head, cells);
    return sec;
  }

  function redrawLedger() {
    const box = el("div");
    turns.forEach((t) => {
      if (needle) {
        let hay = "";
        try {
          const toolNames = (Array.isArray(t.tools) ? t.tools : [])
            .map((x) => (x && x.function && x.function.name) || (x && x.name) || "");
          hay = JSON.stringify([t.new_messages, t.response_message, t.system, toolNames, t.model, t.finish_reason]).toLowerCase();
        } catch (_) { hay = ""; }
        if (!hay.includes(needle)) return;
      }
      box.append(turnSection(t));
    });
    if (!box.childNodes.length) {
      box.append(el("section", { class: "card" }, emptyBox("没有匹配的 Turn", "调整搜索关键字试试（消息内容 / system / 工具名 / 模型 / finish_reason）")));
    }
    ledgerBox.replaceChildren(box);
    renderInspector();
  }

  function redrawSelection() {
    ledgerBox.querySelectorAll(".trj-cell.sel").forEach((n) => n.classList.remove("sel"));
    if (selected) {
      // 重新标记选中（简单做法：整体重绘选中所在 turn 的样式）
      redrawLedger();
      return;
    }
    renderInspector();
  }

  redrawLedger();
}

/* ============================================================ 小组件 */
function _trjBlock(title, text, cls) {
  return el("div", { class: "msg-block" },
    el("span", { class: "lbl", text: title }),
    el("pre", { class: "pre-block " + (cls || ""), text: String(text) }));
}

function trjKv(k, v) {
  return el("div", { class: "trj-kv" },
    el("span", { class: "trj-kv-k", text: k }),
    el("span", { class: "trj-kv-v mono", text: v }));
}

function trjToolCallList(toolCalls) {
  const box = el("div", { class: "trj-insp-sec" });
  box.append(el("h3", { text: "工具调用（" + toolCalls.length + "）" }));
  toolCalls.forEach((tc, i) => {
    const fn = (tc && tc.function) || {};
    let args;
    try { args = JSON.parse(fn.arguments); } catch (_) { args = fn.arguments; }
    box.append(el("div", { class: "toolcall" },
      el("div", { class: "msg-line" },
        el("span", { class: "chip chip-tool", text: "#" + i + " " + (tc.type || "function") }),
        el("span", { class: "mono", text: fn.name || "" }),
        tc.id ? el("span", { class: "mono dim", text: tc.id }) : null),
      typeof args === "string" ? el("pre", { class: "pre-block", text: args }) : jsonViewer(args)));
  });
  return box;
}

/* ============================================================ Inspector Tab 组件 */
/* tabs = [{id, label, render()}]；render 返回 Node（同步）或 Promise<Node>（懒加载）。
   返回 { root, activate(id) }；onActivate(id) 在 Tab 切换时回调（用于记录 Tab 历史）。 */
function trjTabs(tabs, onActivate) {
  const bar = el("div", { class: "trj-tabs", role: "tablist" });
  const panel = el("div", { class: "trj-tab-panel" });
  let active = null;

  function activate(id) {
    const def = tabs.find((t) => t.id === id);
    if (!def || active === id) return;
    active = id;
    tabs.forEach((t) => {
      const on = t.id === id;
      t._btn.classList.toggle("on", on);
      t._btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (onActivate) onActivate(id);
    panel.replaceChildren(el("div"));
    const done = (node) => panel.replaceChildren(node || el("div"));
    const fail = (e) => panel.replaceChildren(
      el("div", { class: "banner banner-err", text: "加载失败：" + (e && e.message ? e.message : e) }));
    try {
      const r = def.render();
      if (r && typeof r.then === "function") {
        panel.append(el("div", { class: "empty-hint", text: "加载中…" }));
        r.then(done, fail);
      } else {
        done(r);
      }
    } catch (e) {
      fail(e);
    }
  }

  tabs.forEach((t) => {
    t._btn = el("button", { class: "trj-tab", type: "button", text: t.label, role: "tab" });
    t._btn.setAttribute("aria-selected", "false");
    t._btn.addEventListener("click", () => activate(t.id));
    bar.append(t._btn);
  });
  activate(tabs[0].id);
  return { root: el("div", { class: "trj-tabs-wrap" }, bar, panel), activate };
}

/* 复制按钮：优先剪贴板 API，失败降级 execCommand */
function trjCopyBtn(text, label) {
  const btn = el("button", { class: "btn btn-xs trj-copy-btn", type: "button", text: label || "复制" });
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (_) { /* 降级 */ }
    if (!ok) {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand("copy");
        ta.remove();
      } catch (_) { /* 忽略 */ }
    }
    btn.textContent = ok ? "已复制" : "复制失败";
    setTimeout(() => { btn.textContent = label || "复制"; }, 1200);
  });
  return btn;
}

/* 行级 diff 视图：公共前后缀裁剪，中间行标记删除（−）/新增（+），上下各带 2 行 */
function trjDiffView(oldText, newText) {
  const a = String(oldText == null ? "" : oldText).split("\n");
  const b = String(newText == null ? "" : newText).split("\n");
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const dels = a.slice(pre, a.length - suf);
  const adds = b.slice(pre, b.length - suf);

  const box = el("div", { class: "trj-diff" });
  const line = (cls, mark, text) => box.append(el("div", { class: "trj-diff-line" + (cls ? " " + cls : "") },
    el("span", { class: "trj-diff-mark", text: mark }),
    el("span", { class: "trj-diff-text", text: text })));

  const C = 2; // 上下文行数
  if (pre - C > 0) line("", "…", "");
  a.slice(Math.max(0, pre - C), pre).forEach((t) => line("", " ", t));
  dels.forEach((t) => line("del", "−", t));
  adds.forEach((t) => line("add", "+", t));
  const sufStart = b.length - suf;
  b.slice(sufStart, sufStart + C).forEach((t) => line("", " ", t));
  if (sufStart + C < b.length) line("", "…", "");
  if (!dels.length && !adds.length) line("", " ", "（内容一致）");
  return box;
}

/* 懒加载完整调用记录（Chunks / Raw Tab 用），结果缓存在 turn._record */
function ensureRecord(turn) {
  if (!turn._record) {
    const p = api("calls/" + encodeURIComponent(turn.call_id), { silent: true }).then(
      (rec) => { turn._record = rec; return rec; },
      (e) => { turn._record = null; throw e; }
    );
    p.catch(() => {}); // 已由 Tab 渲染层接住，这里仅消除未处理拒绝告警
    turn._record = p;
  }
  return Promise.resolve(turn._record);
}

/* 向后查找工具结果：后续 Turn 的 new_messages 中 role=tool 且 tool_call_id 匹配 */
function findToolResult(turns, fromTurnNo, toolCallId) {
  for (const t of turns) {
    if (t.turn_no <= fromTurnNo) continue;
    for (const m of (t.new_messages || [])) {
      if (m && m.role === "tool" && m.tool_call_id === toolCallId) return { turn: t, message: m };
    }
  }
  return null;
}

/* ============================================================ Markdown 渲染（轻量子集） */
/* DOM 构建（不使用 innerHTML），文本一律经 createTextNode，天然免疫 XSS。
   支持：fenced 代码块、标题、无序/有序列表、引用、水平线、
   行内代码、粗体、斜体、删除线、链接（仅 http/https/mailto）。 */

// 行内元素：`code` **bold** *italic* ~~del~~ [text](url)
function trjInlineMd(text) {
  const frag = document.createDocumentFragment();
  const re = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(~~[^~\n]+~~)|(\[[^\]\n]+\]\([^)\s]+\))/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) frag.append(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      frag.append(el("code", { class: "trj-md-code" }, tok.slice(1, -1)));
    } else if (tok.startsWith("**")) {
      frag.append(el("strong", null, tok.slice(2, -2)));
    } else if (tok.startsWith("~~")) {
      frag.append(el("del", null, tok.slice(2, -2)));
    } else if (tok.startsWith("*")) {
      frag.append(el("em", null, tok.slice(1, -1)));
    } else {
      const mm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok);
      if (mm) {
        const href = /^(https?:|mailto:)/i.test(mm[2]) ? mm[2] : "#";
        frag.append(el("a", { href, target: "_blank", rel: "noopener noreferrer" }, mm[1]));
      } else {
        frag.append(tok);
      }
    }
    last = m.index + tok.length;
  }
  if (last < text.length) frag.append(text.slice(last));
  return frag;
}

function trjMarkdown(text) {
  const root = el("div", { class: "trj-md" });
  const lines = String(text == null ? "" : text).split("\n");
  let para = null;
  const flushPara = () => {
    if (para) {
      const p = el("p");
      para.forEach((frag) => p.append(frag));
      root.append(p);
      para = null;
    }
  };

  let i = 0;
  let m2;
  while (i < lines.length) {
    const line = lines[i];
    // fenced 代码块
    if (/^\s*```/.test(line)) {
      flushPara();
      i++;
      const buf = [];
      while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++; // 跳过闭合 ```
      root.append(el("pre", { class: "trj-md-pre" }, el("code", null, buf.join("\n"))));
      continue;
    }
    // 空行
    if (!line.trim()) { flushPara(); i++; continue; }
    // 水平线
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushPara();
      root.append(el("hr", { class: "trj-md-hr" }));
      i++;
      continue;
    }
    // 标题（降两级以适配面板排版：# → h3）
    m2 = /^\s*(#{1,6})\s+(.*)$/.exec(line);
    if (m2) {
      flushPara();
      const lv = Math.min(6, m2[1].length + 2);
      root.append(el("h" + lv, { class: "trj-md-h" }, trjInlineMd(m2[2])));
      i++;
      continue;
    }
    // 引用块
    if (/^\s*>\s?/.test(line)) {
      flushPara();
      const bq = el("blockquote", { class: "trj-md-quote" });
      while (i < lines.length && (m2 = /^\s*>\s?(.*)$/.exec(lines[i])) !== null) {
        bq.append(el("p", null, trjInlineMd(m2[1])));
        i++;
      }
      root.append(bq);
      continue;
    }
    // 无序列表
    if (/^\s*[-*+]\s+/.test(line)) {
      flushPara();
      const ul = el("ul", { class: "trj-md-list" });
      while (i < lines.length && (m2 = /^\s*[-*+]\s+(.*)$/.exec(lines[i])) !== null) {
        ul.append(el("li", null, trjInlineMd(m2[1])));
        i++;
      }
      root.append(ul);
      continue;
    }
    // 有序列表
    if (/^\s*\d+[.)]\s+/.test(line)) {
      flushPara();
      const ol = el("ol", { class: "trj-md-list" });
      while (i < lines.length && (m2 = /^\s*\d+[.)]\s+(.*)$/.exec(lines[i])) !== null) {
        ol.append(el("li", null, trjInlineMd(m2[1])));
        i++;
      }
      root.append(ol);
      continue;
    }
    // 普通段落行
    if (!para) para = [];
    para.push(trjInlineMd(line));
    i++;
  }
  flushPara();
  return root;
}

/* ============================================================ 搜索命中高亮 */
/* 将 text 中命中 needle（小写）的片段包 <mark>，返回 DocumentFragment */
function trjHighlight(text, needle) {
  const s = String(text == null ? "" : text);
  const frag = document.createDocumentFragment();
  if (!needle) { frag.append(s); return frag; }
  const lower = s.toLowerCase();
  let idx = 0;
  let pos;
  while ((pos = lower.indexOf(needle, idx)) !== -1) {
    if (pos > idx) frag.append(s.slice(idx, pos));
    frag.append(el("mark", { class: "trj-mark" }, s.slice(pos, pos + needle.length)));
    idx = pos + needle.length;
  }
  frag.append(s.slice(idx));
  return frag;
}

/* ============================================================ Inspector 内跳转 */
/* 跳转链接（激活当前 Inspector Tab 组件的指定 Tab） */
function trjJumpLink(tabId, label) {
  const btn = el("button", { class: "trj-jump-link", type: "button", text: label || "详情 →", title: "跳转到 " + tabId });
  btn.addEventListener("click", () => { if (trjInspTabApi) trjInspTabApi.activate(tabId); });
  return btn;
}

/* 跳转 chip（快捷入口用） */
function trjJumpChip(label, tabId) {
  const chip = el("button", { class: "chip chip-link", type: "button", text: label, title: "跳转到 " + label });
  chip.addEventListener("click", () => { if (trjInspTabApi) trjInspTabApi.activate(tabId); });
  return chip;
}

/* 可点击 KV 行（值尾部带 →，点击跳指定 Tab） */
function trjKvJump(k, v, tabId) {
  const row = el("div", { class: "trj-kv trj-clickable", title: "点击跳转 " + tabId },
    el("span", { class: "trj-kv-k", text: k }),
    el("span", { class: "trj-kv-v mono", text: v + " →" }));
  row.addEventListener("click", () => { if (trjInspTabApi) trjInspTabApi.activate(tabId); });
  return row;
}
