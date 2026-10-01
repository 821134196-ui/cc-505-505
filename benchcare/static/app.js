/* 座椅养护单页应用：地图 / 列表 / 工单，哈希路由，无外部依赖。 */
"use strict";

const ROLE_LABELS = {
  citizen: "市民",
  crew1: "维保一班",
  crew2: "维保二班",
  inspector: "复验员老周",
};
const CREW_ROLES = ["crew1", "crew2"];

const state = {
  me: localStorage.getItem("benchcare-role") || "citizen",
  benches: [],
  events: [],
  meta: { materials: {}, issues: {} },
  focusBench: null,
  timer: null,
};

const $app = document.getElementById("app");
const $tip = document.getElementById("tooltip");

/* ---------------- API ---------------- */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败 (${res.status})`);
  return data;
}

async function refreshAll() {
  const [benches, events, meta] = await Promise.all([
    api("/api/benches"),
    api("/api/events?open=1"),
    api("/api/meta"),
  ]);
  state.benches = benches;
  state.events = events;
  state.meta = meta;
}

/* ---------------- 工具 ---------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const matIcon = (m) => state.meta.materials[m]?.icon || "🪑";
const matLabel = (m) => state.meta.materials[m]?.label || m;
const issueLabel = (t) => state.meta.issues[t]?.label || t;
const issueMeasure = (t) => state.meta.issues[t]?.measure || "";
const issueCordon = (t) => state.meta.issues[t]?.cordon || "";
const STATUS_LABEL = {
  reported: "待认领", claimed: "已认领", repairing: "维修中",
  awaiting_review: "待复验", verified: "复验通过", closed: "已归档",
};
const PART = { ok: "完好", loose: "松动", missing: "缺失", broken: "损坏" };

function benchOpenEvent(benchId) {
  return state.events.find((e) => e.bench_id === benchId) || null;
}
function benchSafety(b) {
  if (b.removed) return { cls: "removed", text: "已拆除 · 待重装", icon: "🚧" };
  if (b.usable) return { cls: "ok", text: "可使用", icon: "✅" };
  const ev = benchOpenEvent(b.id);
  return { cls: "bad", text: ev ? `暂停使用 · ${issueLabel(ev.issue_type)}` : "暂停使用", icon: "⚠️" };
}
function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3600);
}
function fmtTime(s) {
  return s ? s.replace("T", " ").slice(5, 16) : "—";
}

/* ---------------- 路由 ---------------- */

function parseHash() {
  const [path, qs] = location.hash.slice(2).split("?");
  const parts = path.split("/").filter(Boolean);
  const query = Object.fromEntries(new URLSearchParams(qs || ""));
  return { parts, query };
}

async function router() {
  clearInterval(state.timer);
  document.querySelectorAll("nav.tabs a").forEach((a) => {
    const tab = a.dataset.tab;
    a.classList.toggle("active", location.hash.startsWith(`#/${tab}`) ||
      (tab === "map" && !location.hash));
  });
  const { parts, query } = parseHash();
  try {
    await refreshAll();
    const page = parts[0] || "map";
    if (page === "map") renderMap(query.focus || null);
    else if (page === "list") renderList();
    else if (page === "orders") renderOrders();
    else if (page === "bench" && parts[1]) renderBench(parts[1]);
    else if (page === "event" && parts[1]) renderEvent(parseInt(parts[1], 10));
    else renderMap(null);
    window.scrollTo(0, 0);
  } catch (e) {
    $app.innerHTML = `<div class="card error-box">加载失败：${esc(e.message)}</div>`;
  }
}
window.addEventListener("hashchange", router);

/* ---------------- SVG 地图投影 ---------------- */

const MAP = { W: 860, H: 560, minLat: 30.2474, maxLat: 30.2526,
              minLng: 120.1492, maxLng: 120.1588 };
function project(lat, lng) {
  const x = ((lng - MAP.minLng) / (MAP.maxLng - MAP.minLng)) * MAP.W;
  const y = ((MAP.maxLat - lat) / (MAP.maxLat - MAP.minLat)) * MAP.H;
  return [Math.round(x), Math.round(y)];
}

/* ---------------- 地图页 ---------------- */

function renderMap(focusId) {
  state.focusBench = focusId;
  const markers = state.benches.map((b) => {
    const [x, y] = project(b.lat, b.lng);
    const s = benchSafety(b);
    const color = s.cls === "ok" ? "#1f5130" : s.cls === "removed" ? "#8a8f8c" : "#ef7d1e";
    const ev = benchOpenEvent(b.id);
    const wo = ev ? ev.wo_id : "";
    const cls = focusId === b.id ? "marker focus" : "marker";
    return `<g class="${cls}" data-bench="${b.id}" transform="translate(${x},${y})">
        <circle class="focus-ring" r="16"></circle>
        <circle class="pin" r="11" fill="${color}"></circle>
        <text class="code" y="3.5">${esc(b.id.slice(2))}</text>
        <title>${esc(b.id)} ${esc(matLabel(b.material))} · ${esc(s.text)}${wo ? " · " + esc(wo) : ""}</title>
      </g>`;
  }).join("");

  $app.innerHTML = `
    <div class="layout">
      <div class="card map-wrap">
        <h2>河滨公园 · 市民广场 座椅分布图</h2>
        <svg id="map" viewBox="0 0 ${MAP.W} ${MAP.H}" xmlns="http://www.w3.org/2000/svg" role="img"
             aria-label="座椅地图，绿色可使用，橙色报损封控，灰色已拆除">
          ${mapDecoration()}
          <g id="markers">${markers}</g>
          <g id="locLayer"></g>
        </svg>
        <div class="map-toolbar">
          <button id="btn-loc">📍 获取定位</button>
          <button id="btn-report-map">＋ 报损</button>
        </div>
        <div class="map-legend">
          <span><i class="dot ok"></i>可使用</span>
          <span><i class="dot bad"></i>报损 / 封控停用</span>
          <span><i class="dot removed"></i>已拆除待重装</span>
          <span>🪵 木质　⚙️ 金属　⬛ 石材　🧱 塑钢</span>
        </div>
      </div>
      <div class="card">
        <h2>${focusId ? `定位：${esc(focusId)}` : "座椅列表"} <button class="btn ghost" style="float:right;padding:4px 10px;font-size:12px" id="clear-focus">全部</button></h2>
        <div class="bench-list" id="map-list">${benchListItems(focusId ? state.benches.filter(b => b.id === focusId) : state.benches)}</div>
      </div>
    </div>`;

  document.querySelectorAll(".marker").forEach((m) => {
    m.addEventListener("click", () => location.hash = `#/bench/${m.dataset.bench}`);
    m.addEventListener("mouseenter", (e) => {
      const b = state.benches.find((x) => x.id === m.dataset.bench);
      const s = benchSafety(b);
      $tip.textContent = `${b.id} ${matIcon(b.material)} ${b.location_name}｜${s.text}`;
      $tip.style.display = "block";
      $tip.style.left = `${e.clientX + 12}px`;
      $tip.style.top = `${e.clientY + 12}px`;
    });
    m.addEventListener("mouseleave", () => ($tip.style.display = "none"));
  });
  document.getElementById("btn-loc").onclick = showMockLocation;
  document.getElementById("btn-report-map").onclick = () => {
    location.hash = state.focusBench ? `#/bench/${state.focusBench}` : "#/list";
  };
  const cf = document.getElementById("clear-focus");
  if (cf) cf.onclick = () => (location.hash = "#/map");
}

function mapDecoration() {
  // 公园底色 + 河道 + 步道 + 广场等示意元素
  const P = (lat, lng) => project(lat, lng).join(",");
  return `
  <rect x="0" y="0" width="${MAP.W}" height="${MAP.H}" fill="#e9f2e6"/>
  <polygon points="${P(30.2525, 120.1493)},${P(30.2525, 120.1548)},${P(30.2506, 120.1566)},
                   ${P(30.2490, 120.1587)},${P(30.2475, 120.1587)},${P(30.2475, 120.1520)},
                   ${P(30.2496, 120.1493)}" fill="#d8ead2"/>
  <path d="M ${P(30.2475, 120.1578)} Q ${P(30.2492, 120.1572)} ${P(30.2504, 120.1563)}
           T ${P(30.2525, 120.1553)}" stroke="#9ecbe4" stroke-width="26" fill="none" stroke-linecap="round" opacity=".8"/>
  <text x="${project(30.2482, 120.1579)[0]}" y="${project(30.2482, 120.1579)[1] - 16}"
        font-size="15" fill="#5d93b5" font-weight="700">滨河步道</text>
  <rect x="${project(30.2519, 120.1493)[0]}" y="${project(30.2519, 120.1493)[1]}"
        width="${project(30.2500, 120.1520)[0] - project(30.2519, 120.1493)[0]}"
        height="${project(30.2500, 120.1493)[1] - project(30.2519, 120.1493)[1]}"
        fill="#efe9da" rx="10"/>
  <text x="${project(30.2512, 120.1506)[0]}" y="${project(30.2512, 120.1506)[1]}"
        font-size="15" fill="#9a8a63" font-weight="700" text-anchor="middle">市民广场</text>
  <path d="M ${P(30.2516, 120.1554)} L ${P(30.2502, 120.1543)} L ${P(30.2493, 120.1530)}
           L ${P(30.2484, 120.1512)}" stroke="#ffffff" stroke-width="7" fill="none" stroke-linecap="round"/>
  <text x="${project(30.2510, 120.1536)[0]}" y="${project(30.2510, 120.1536)[1] - 8}"
        font-size="12" fill="#7fa37f">林荫步道</text>
  <text x="${project(30.2514, 120.1556)[0]}" y="${project(30.2514, 120.1556)[1] + 22}"
        font-size="13" fill="#4e7d52" font-weight="700">河滨公园</text>
  <g font-size="11" fill="#9bb29c">
    <text x="${project(30.2512, 120.1555)[0] - 14}" y="${project(30.2512, 120.1555)[1] - 16}">东门</text>
  </g>`;
}

async function showMockLocation() {
  const pos = await api("/api/location");
  const [x, y] = project(pos.lat, pos.lng);
  document.getElementById("locLayer").innerHTML =
    `<g class="loc-dot" transform="translate(${x},${y})">
       <circle r="16" fill="rgba(44,110,155,.18)"></circle>
       <circle class="core" r="7"></circle>
       <circle r="7" fill="none" stroke="#fff" stroke-width="2"></circle>
     </g>`;
  toast(`模拟定位：${pos.desc}（${pos.lat.toFixed(4)}, ${pos.lng.toFixed(4)}）`);
}

function benchListItems(list) {
  if (!list.length) return `<p class="small">暂无座椅</p>`;
  return list.map((b) => {
    const s = benchSafety(b);
    const ev = benchOpenEvent(b.id);
    return `<div class="bench-item" data-go-bench="${b.id}">
      <div class="mat">${matIcon(b.material)}</div>
      <div class="meta">
        <div class="code">${esc(b.id)} <span class="small">${esc(matLabel(b.material))}</span></div>
        <div class="place">📍 ${esc(b.location_name)}</div>
      </div>
      ${ev ? `<span class="badge status ${ev.status}">${esc(STATUS_LABEL[ev.status] || ev.status)}</span>` : ""}
      <span class="badge ${s.cls}">${s.icon} ${esc(s.text)}</span>
    </div>`;
  }).join("");
}

/* ---------------- 列表页 ---------------- */

function renderList() {
  const openCount = state.benches.filter((b) => !b.usable).length;
  $app.innerHTML = `
    <h3 class="page-title">座椅台账（共 ${state.benches.length} 处，${openCount} 处暂停使用）</h3>
    <div class="card">
      <input id="list-search" placeholder="搜索编号 / 位置…" style="max-width:320px">
      <div class="bench-list" style="max-height:none;margin-top:10px" id="full-list">
        ${benchListItems(state.benches)}
      </div>
    </div>`;
  document.getElementById("list-search").oninput = (e) => {
    const q = e.target.value.trim();
    const list = state.benches.filter((b) =>
      !q || b.id.includes(q.toUpperCase()) || b.location_name.includes(q));
    document.getElementById("full-list").innerHTML = benchListItems(list);
  };
}

/* ---------------- 工单页 ---------------- */

async function renderOrders() {
  const wos = await api("/api/workorders");
  $app.innerHTML = `<h3 class="page-title">维修工单（${wos.length}）</h3><div class="card" id="wo-wrap"></div>`;
  if (!wos.length) {
    document.getElementById("wo-wrap").innerHTML = `<p class="small">暂无工单</p>`;
    return;
  }
  document.getElementById("wo-wrap").innerHTML = wos.map((wo) => {
    const b = state.benches.find((x) => x.id === wo.bench_id);
    return `<div class="wo-card" data-go-event="${wo.event_id}">
      <div class="wo-head">
        <span class="wo-no">${esc(wo.id)}</span>
        <span class="badge status ${wo.event_status}">${esc(STATUS_LABEL[wo.event_status] || wo.event_status)}</span>
        ${wo.report_count > 1 ? `<span class="merge-tag">归并报损 ×${wo.report_count}</span>` : ""}
        ${wo.crew ? `<span class="badge status">${esc(wo.crew)}</span>` : ""}
      </div>
      <div class="wo-sub">
        🪑 <a href="#/bench/${esc(wo.bench_id)}" data-jet-bench="${esc(wo.bench_id)}">${esc(wo.bench_id)}</a>
        · ${matIcon(b?.material)} ${esc(issueLabel(wo.issue_type))}
        · 📍 ${esc(b?.location_name || "")}
        · <a href="#/event/${wo.event_id}" data-jet-event="${wo.event_id}">事件 #${wo.event_id}</a>
      </div>
    </div>`;
  }).join("");
}

/* ---------------- 座椅详情 ---------------- */

async function renderBench(id) {
  const b = await api(`/api/benches/${id}`).catch(() => null);
  if (!b) {
    $app.innerHTML = `<div class="card error-box">找不到座椅 ${esc(id)}　<a class="back-link" href="#/map">返回地图</a></div>`;
    return;
  }
  const s = benchSafety(b);
  const armBadge = (v) => `<span class="part-pill ${v === "ok" ? "ok" : "bad"}">扶手：${PART[v] || v}</span>`;
  const fixBadge = (v) => `<span class="part-pill ${v === "ok" ? "ok" : "bad"}">固定件：${PART[v] || v}</span>`;

  $app.innerHTML = `
    <div class="cross-links">
      <a class="back-link" href="#/map">← 地图</a>
      <a class="link-chip" href="#/map?focus=${b.id}">📍 地图标注</a>
    </div>
    <div class="safety-banner ${s.cls === "ok" ? "safe" : "unsafe"}">
      <span class="sicon">${s.icon}</span>
      <div>
        <div class="big">${esc(b.id)} · ${esc(s.text)}</div>
        <div class="sub">📍 ${esc(b.location_name)}（${b.lat.toFixed(5)}, ${b.lng.toFixed(5)}）</div>
      </div>
    </div>
    <div class="layout">
      <div class="card">
        <h2>${matIcon(b.material)} ${esc(b.id)} 台账信息</h2>
        <dl class="kv">
          <dt>材质</dt><dd>${matIcon(b.material)} ${esc(matLabel(b.material))}</dd>
          <dt>位置</dt><dd>${esc(b.location_name)}</dd>
          <dt>坐标</dt><dd>${b.lat.toFixed(5)}, ${b.lng.toFixed(5)}</dd>
          <dt>扶手状态</dt><dd>${armBadge(b.armrest)}</dd>
          <dt>固定件状态</dt><dd>${fixBadge(b.fixture)}</dd>
          <dt>拆除标记</dt><dd>${b.removed ? "🚧 已拆除待重装" : "在位"}</dd>
        </dl>
        <div class="section-title">关联事件 / 工单</div>
        ${(b.events || []).length ? b.events.map(eventRow).join("") : '<p class="small">暂无</p>'}
      </div>
      <div class="card">
        <h2>📣 市民报损</h2>
        ${b.usable && !b.removed ? "" : `<div class="notice">该座椅当前<b>不可使用</b>，重复报损将自动归并到未结事件。</div>`}
        <form id="report-form">
          <label class="fld">损坏情况</label>
          <select name="issue_type" required>
            ${Object.entries(state.meta.issues).map(([k, v]) =>
              `<option value="${k}">${esc(v.label)}</option>`).join("")}
          </select>
          <div class="measure" id="report-measure"></div>
          <label class="fld">称呼</label>
          <input name="reporter" value="${ROLE_LABELS[state.me] === "市民" ? "" : esc(ROLE_LABELS[state.me])}" placeholder="选填，默认匿名市民">
          <label class="fld">情况描述</label>
          <textarea name="note" placeholder="例：坐板裂缝有毛刺，孩子差点被扎到"></textarea>
          <div class="btn-row"><button class="btn orange" type="submit">提交报损</button></div>
        </form>
      </div>
    </div>`;

  const issueSel = document.querySelector("#report-form [name=issue_type]");
  const updateMeasure = () => {
    const v = state.meta.issues[issueSel.value];
    document.getElementById("report-measure").innerHTML =
      `<b>现场措施：</b>${esc(v.measure)}<br><b>封控方式：</b>${esc(v.cordon)}`;
  };
  issueSel.onchange = updateMeasure;
  updateMeasure();

  document.getElementById("report-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const r = await api("/api/report", {
        method: "POST",
        body: {
          bench_id: b.id,
          issue_type: fd.get("issue_type"),
          reporter: fd.get("reporter") || "匿名市民",
          note: fd.get("note") || "",
        },
      });
      if (r.merged) {
        toast(`已归并到未结事件 #${r.event_id}（工单 ${r.event.work_order?.id || ""}），报损 ${r.event.report_count} 次`, "merge");
      } else {
        toast(`报损成功，已生成工单 ${r.event.work_order?.id}`);
      }
      location.hash = `#/event/${r.event_id}`;
    } catch (err) {
      toast(err.message, "error");
    }
  };
}

function eventRow(e) {
  return `<div class="wo-card" data-go-event="${e.id}" style="margin-bottom:8px">
    <div class="wo-head">
      <span class="badge status ${e.status}">${esc(STATUS_LABEL[e.status] || e.status)}</span>
      <b>${esc(e.issue_label)}</b>
      ${e.report_count > 1 ? `<span class="merge-tag">×${e.report_count}</span>` : ""}
    </div>
    <div class="wo-sub">事件 #${e.id} · ${fmtTime(e.created_at)} · 点击查看处理详情</div>
  </div>`;
}

/* ---------------- 事件 / 工单详情 ---------------- */

async function renderEvent(id) {
  let ev;
  try {
    ev = await api(`/api/events/${id}`);
  } catch (e) {
    $app.innerHTML = `<div class="card error-box">${esc(e.message)}　<a class="back-link" href="#/orders">返回工单</a></div>`;
    return;
  }
  const b = ev.bench;
  const s = benchSafety({ ...b, usable: ev.status === "closed" ? 1 : b.usable });
  const mine = CREW_ROLES.includes(state.me) && ROLE_LABELS[state.me] === ev.claimed_by;
  const isInspector = state.me === "inspector";
  const crewName = ROLE_LABELS[state.me];

  $app.innerHTML = `
    <div class="cross-links">
      <a class="back-link" href="#/orders">← 工单列表</a>
      <a class="link-chip" href="#/bench/${b.id}">🪑 ${esc(b.id)} 座椅台账</a>
      <a class="link-chip" href="#/map?focus=${b.id}">📍 地图标注</a>
      <span class="link-chip wo">工单 ${esc(ev.work_order?.id || "—")}</span>
    </div>
    <div class="safety-banner ${ev.status === "closed" ? "safe" : "unsafe"}">
      <span class="sicon">${ev.status === "closed" ? "✅" : s.icon}</span>
      <div>
        <div class="big">${esc(ev.issue_label)} · ${esc(STATUS_LABEL[ev.status] || ev.status)}</div>
        <div class="sub">📍 ${esc(b.id)} ${esc(b.location_name)}　|　${ev.status === "closed" ? "已恢复可使用" : "安全封控，暂停使用"}</div>
      </div>
    </div>
    <div class="layout">
      <div>
        <div class="card">
          <h2>事件 #${ev.id} <span class="badge status ${ev.status}">${esc(STATUS_LABEL[ev.status] || ev.status)}</span></h2>
          <div class="measure"><b>现场措施：</b>${esc(ev.measure)}<br><b>封控方式：</b>${esc(ev.cordon_kind)}</div>
          <dl class="kv">
            <dt>首次报损</dt><dd>${esc(ev.first_reporter)} · ${fmtTime(ev.created_at)}</dd>
            <dt>报损次数</dt><dd>${ev.report_count} 次（重复报损已归并）</dd>
            <dt>认领班组</dt><dd>${esc(ev.claimed_by || "待认领")}${ev.claimed_at ? " · " + fmtTime(ev.claimed_at) : ""}</dd>
            <dt>维修完成</dt><dd>${ev.repaired_at ? esc(ev.repaired_by) + " · " + fmtTime(ev.repaired_at) : "—"}</dd>
            <dt>复验</dt><dd>${ev.reviewed_at ? esc(ev.reviewed_by) + " · " + fmtTime(ev.reviewed_at) + (ev.review_passed ? " · ✅ 通过" : " · ↩️ 返工") : "—"}</dd>
          </dl>
          <details class="reports-merged" ${ev.report_count > 1 ? "open" : ""}>
            <summary class="section-title" style="display:inline-block;cursor:pointer">
              报损明细（${ev.merged_reports.length} 条，重复报损归并至本事件）
            </summary>
            <ul>${ev.merged_reports.map((r) =>
              `<li><b>${esc(r.reporter)}</b> <span class="small">${esc(r.at)}</span>${r.note ? `：${esc(r.note)}` : ""}</li>`).join("")}</ul>
          </details>
        </div>

        <div class="card" style="margin-top:12px">
          <h2>📷 维修前后照片</h2>
          <div class="photos">
            <div class="photo-box">
              <div class="tag">维修前</div>
              ${ev.before_photo ? `<img src="${esc(ev.before_photo)}?t=${ev.id}" alt="维修前照片">` : '<div class="missing">尚未上传</div>'}
            </div>
            <div class="photo-box">
              <div class="tag">维修后（复验核对）</div>
              ${ev.after_photo ? `<img src="${esc(ev.after_photo)}?t=${ev.id}" alt="维修后照片">` : '<div class="missing">尚未上传</div>'}
            </div>
          </div>
        </div>

        <div class="card" style="margin-top:12px">
          <h2>🧰 材料消耗</h2>
          ${ev.materials.length ? `<table class="mat-table">
            <tr><th>材料</th><th>数量</th><th>领料班组</th></tr>
            ${ev.materials.map((m) => `<tr><td>${esc(m.item)}</td><td>${m.qty} ${esc(m.unit)}</td><td>${esc(m.used_by || "")}</td></tr>`).join("")}
          </table>` : '<p class="small">暂无材料记录</p>'}
          <div id="materials-form"></div>
        </div>
      </div>

      <div>
        <div class="card" id="action-card"></div>
        <div class="card" style="margin-top:12px">
          <h2>🚧 封控 / 处理记录</h2>
          <div id="cordon-box"></div>
          <div class="section-title">事件时间线</div>
          <ul class="timeline">
            ${ev.logs.slice().reverse().map((l) => `
              <li><span class="act">${esc(l.action)}</span> <span class="who">${esc(l.actor)}</span>
                <span class="at">${fmtTime(l.created_at)}</span>
                ${l.detail ? `<span class="det">${esc(l.detail)}</span>` : ""}
              </li>`).join("")}
          </ul>
        </div>
      </div>
    </div>`;

  renderCordon(ev, mine, crewName);
  renderActions(ev, mine, isInspector, crewName);
  renderMaterialsForm(ev, mine, crewName);
}

function renderCordon(ev, mine, crewName) {
  const box = document.getElementById("cordon-box");
  if (ev.cordon) {
    box.innerHTML = `<div class="notice">
      <b>已封控</b>（${esc(ev.cordon_kind)}）<br>
      ${esc(ev.cordon_note || "")}<br>
      <span class="small">布设：${esc(ev.cordon_by)} · ${fmtTime(ev.cordon_at)}</span><br>
      警示带撤除：${ev.tape_removed ? "✅ 已撤除" : "🚧 尚未撤除"}
    </div>`;
  } else {
    box.innerHTML = `<p class="small">尚未布设封控。${ev.claimed_by ? "" : "认领工单后由责任班组布设。"}</p>`;
  }
}

function renderMaterialsForm(ev, mine, crewName) {
  const box = document.getElementById("materials-form");
  if (!mine || !["claimed", "repairing", "awaiting_review"].includes(ev.status)) {
    box.innerHTML = "";
    return;
  }
  const suggested = (state.meta.issues[ev.issue_type]?.suggested || []);
  box.innerHTML = `
    <div class="section-title">登记领料</div>
    <form id="mat-form" class="form-grid">
      <select name="item">${suggested.map(([i]) => `<option>${esc(i)}</option>`).join("")}
        <option value="其他">其他</option></select>
      <div style="display:flex;gap:6px">
        <input name="qty" type="number" step="0.01" min="0.01" value="1" style="width:90px">
        <input name="unit" value="${esc(suggested[0]?.[1] || "个")}" style="width:80px">
        <button class="btn" type="submit">添加</button>
      </div>
    </form>`;
  document.getElementById("mat-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const item = fd.get("item") === "其他" ? prompt("材料名称") : fd.get("item");
    if (!item) return;
    try {
      await api(`/api/events/${ev.id}`, {
        method: "POST",
        body: { action: "materials", crew: crewName,
                items: [{ item, qty: parseFloat(fd.get("qty")), unit: fd.get("unit") || "个" }] },
      });
      toast("材料消耗已登记");
      router();
    } catch (err) { toast(err.message, "error"); }
  };
}

function renderActions(ev, mine, isInspector, crewName) {
  const box = document.getElementById("action-card");
  const st = ev.status;
  let html = `<h2>🛠 处理工单</h2>`;

  if (st === "reported") {
    html += `<div class="notice">工单待认领。${CREW_ROLES.includes(state.me)
      ? `以 <b>${esc(crewName)}</b> 身份接单（并发抢单时仅一个班组成功）。`
      : "请切换到养护班组身份接单。"}</div>`;
    if (CREW_ROLES.includes(state.me)) {
      html += `<div class="btn-row"><button class="btn orange" data-act="claim">🙋 认领工单</button></div>`;
    }
  } else if (st === "claimed" || st === "repairing") {
    if (mine) {
      html += `<div class="notice">责任班组：<b>${esc(ev.claimed_by)}</b>（你）。按现场措施完成封控与维修。</div>`;
      if (!ev.cordon) {
        html += `<div class="section-title">① 布设封控</div>
          <form id="cordon-form">
            <textarea name="note">${esc(ev.cordon_kind)}：${esc(ev.measure)}</textarea>
            <div class="btn-row"><button class="btn orange" type="submit">🚧 提交封控记录</button></div>
          </form>`;
      }
      html += `<div class="section-title">${ev.cordon ? "②" : "②"} 维修前照片</div>
        ${photoPicker("before", ev.before_photo)}
        <div class="section-title">③ 完成维修 · 提交复验</div>
        <form id="complete-form">
          <label class="fld">维修说明</label>
          <textarea name="repair_note" placeholder="处理过程、更换部件等"></textarea>
          <label class="fld">维修后照片（必传）</label>
          ${photoPicker("after", ev.after_photo, true)}
          <div class="checkline"><input type="checkbox" name="painted" id="painted-chk">
            <label for="painted-chk" style="margin:0">本次刷漆（未干前复验将被阻断）</label></div>
          <div id="dry-min-wrap" style="display:none">
            <label class="fld">油漆干燥时间（分钟）</label>
            <input type="number" name="paint_dry_minutes" value="60" min="1">
          </div>
          <div class="btn-row"><button class="btn" type="submit">✅ 完成维修，申请复验</button></div>
        </form>`;
    } else {
      html += `<div class="notice">工单已由 <b>${esc(ev.claimed_by)}</b> 认领，其他班组不可操作。</div>`;
    }
  } else if (st === "awaiting_review") {
    html += paintPanel(ev);
    if (mine) {
      html += `<div class="notice">你所在班组完成了维修，<b>不能自行复验</b>，请等待复验员到场。</div>`;
    }
    if (isInspector) {
      const wet = ev.paint.painted && !ev.paint.dry;
      html += `<div class="section-title">复验员核对</div>
        <div class="checkline"><input type="checkbox" id="tape-chk" ${wet ? "" : ""}>
          <label for="tape-chk" style="margin:0">警示带 / 围挡已撤除，现场清理完毕</label></div>
        <div class="checkline"><input type="checkbox" id="photo-chk" ${ev.after_photo ? "checked" : ""} disabled>
          <label style="margin:0">维修后照片已上传（${ev.after_photo ? "✅" : "❌ 缺失，禁止通过"}）</label></div>
        <form id="review-form">
          <label class="fld">复验意见</label>
          <textarea name="note" placeholder="复验记录"></textarea>
          <div class="btn-row">
            <button class="btn" type="submit" data-pass="1" ${wet || !ev.after_photo ? "disabled" : ""}>✅ 复验通过，恢复使用</button>
            <button class="btn danger" type="submit" data-pass="0">↩️ 不通过，退回返工</button>
          </div>
        </form>`;
    }
  } else if (st === "closed") {
    html += `<div class="notice">✅ 事件已归档，座椅恢复可使用。材料、照片与处理记录已留存。</div>`;
  }

  if (state.me === "citizen" && ["reported", "claimed", "repairing", "awaiting_review"].includes(st)) {
    html += `<p class="small">市民可在<a href="#/bench/${ev.bench_id}">座椅页</a>继续报损，相同问题会自动归并到本事件。</p>`;
  }
  box.innerHTML = html;
  bindActionEvents(ev, crewName);
}

function paintPanel(ev) {
  if (!ev.paint.painted) return "";
  if (ev.paint.dry) {
    return `<div class="paint-dry">🎨 漆面已干燥（${esc(ev.paint.ready_at)} 后达到干燥要求），可以开放。</div>`;
  }
  return `<div class="paint-wet">🎨 油漆未干！预计 ${esc(ev.paint.ready_at)} 干燥，还需约
    <b id="wet-remain">${ev.paint.remain_minutes}</b> 分钟。在此之前复验通过将被系统阻断。</div>`;
}

function photoPicker(which, existing, required = false) {
  if (existing) {
    return `<div class="small" style="margin:4px 0">✅ 已上传：${esc(existing)}　<input type="file" accept="image/*" data-photo="${which}" style="margin-top:4px"></div>`;
  }
  return `<input type="file" accept="image/*" data-photo="${which}" ${required ? "required" : ""}
    style="margin:4px 0"><div class="small">支持现场拍照，压缩后本地存储</div>
    <input type="hidden" name="${which}_data">`;
}

function readPhoto(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const max = 800;
        const scale = Math.min(1, max / Math.max(img.width, img.height));
        const cv = document.createElement("canvas");
        cv.width = img.width * scale;
        cv.height = img.height * scale;
        cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
        resolve(cv.toDataURL("image/jpeg", 0.82));
      };
      img.onerror = reject;
      img.src = reader.result;
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function bindActionEvents(ev, crewName) {
  const claimBtn = document.querySelector("[data-act=claim]");
  if (claimBtn) {
    claimBtn.onclick = async () => {
      claimBtn.disabled = true;
      try {
        await api(`/api/events/${ev.id}`, {
          method: "POST", body: { action: "claim", crew: crewName },
        });
        toast(`工单已由 ${crewName} 认领`);
        router();
      } catch (e) {
        toast(e.message, "error");
        claimBtn.disabled = false;
      }
    };
  }

  const cordonForm = document.getElementById("cordon-form");
  if (cordonForm) {
    cordonForm.onsubmit = async (e) => {
      e.preventDefault();
      try {
        await api(`/api/events/${ev.id}`, {
          method: "POST",
          body: { action: "cordon", crew: crewName,
                  note: new FormData(cordonForm).get("note") },
        });
        toast("封控记录已提交");
        router();
      } catch (err) { toast(err.message, "error"); }
    };
  }

  // 维修前照片即时上传
  document.querySelectorAll("input[data-photo=before]").forEach((inp) => {
    inp.onchange = async () => {
      if (!inp.files[0]) return;
      try {
        const data = await readPhoto(inp.files[0]);
        await api(`/api/events/${ev.id}`, {
          method: "POST",
          body: { action: "before_photo", crew: crewName, photo: data },
        });
        toast("维修前照片已保存");
        router();
      } catch (err) { toast(err.message, "error"); }
    };
  });

  const paintedChk = document.getElementById("painted-chk");
  if (paintedChk) {
    paintedChk.onchange = () => {
      document.getElementById("dry-min-wrap").style.display = paintedChk.checked ? "block" : "none";
    };
  }

  const completeForm = document.getElementById("complete-form");
  if (completeForm) {
    completeForm.onsubmit = async (e) => {
      e.preventDefault();
      if (e.submitter && e.submitter.dataset.locked) return;
      const file = completeForm.querySelector("input[data-photo=after]")?.files?.[0];
      const existing = ev.after_photo;
      if (!file && !existing) {
        toast("必须上传维修后照片才能提交复验", "error");
        return;
      }
      e.submitter.dataset.locked = "1";
      try {
        const fd = new FormData(completeForm);
        const body = {
          action: "complete", crew: crewName,
          repair_note: fd.get("repair_note") || "",
          painted: fd.get("painted") === "on",
          paint_dry_minutes: parseInt(fd.get("paint_dry_minutes") || "60", 10),
        };
        if (file) body.after_photo = await readPhoto(file);
        await api(`/api/events/${ev.id}`, { method: "POST", body });
        toast("维修完成，已提交复验");
        router();
      } catch (err) {
        toast(err.message, "error");
        delete e.submitter.dataset.locked;
      }
    };
  }

  const reviewForm = document.getElementById("review-form");
  if (reviewForm) {
    reviewForm.onsubmit = async (e) => {
      e.preventDefault();
      const passed = e.submitter.dataset.pass === "1";
      const tape = document.getElementById("tape-chk").checked;
      if (passed && !tape) {
        toast("重新开放前必须确认警示带已撤除", "error");
        return;
      }
      try {
        const r = await api(`/api/events/${ev.id}`, {
          method: "POST",
          body: {
            action: "review", inspector: ROLE_LABELS[state.me],
            passed, tape_removed: tape,
            note: new FormData(reviewForm).get("note") || "",
          },
        });
        toast(passed ? "复验通过，座椅恢复可使用" : "已退回班组返工");
        if (passed) location.hash = `#/bench/${r.bench_id}`;
        else router();
      } catch (err) { toast(err.message, "error"); }
    };

    // 油漆未干倒计时（服务端为最终裁决）
    if (ev.paint.painted && !ev.paint.dry) {
      let remain = ev.paint.remain_minutes;
      state.timer = setInterval(() => {
        remain = Math.max(0, remain - 0.1);
        const el = document.getElementById("wet-remain");
        if (el) el.textContent = remain.toFixed(1);
        if (remain <= 0) { clearInterval(state.timer); router(); }
      }, 6000);
    }
  }
}

/* ---------------- 全局事件委托 ---------------- */

document.addEventListener("click", (e) => {
  const goBench = e.target.closest("[data-go-bench], [data-jet-bench]");
  if (goBench) {
    e.preventDefault();
    location.hash = `#/bench/${goBench.dataset.goBench || goBench.dataset.jetBench}`;
    return;
  }
  const goEvent = e.target.closest("[data-go-event], [data-jet-event]");
  if (goEvent) {
    e.preventDefault();
    location.hash = `#/event/${goEvent.dataset.goEvent || goEvent.dataset.jetEvent}`;
  }
});

document.getElementById("role").value = state.me;
document.getElementById("role").onchange = (e) => {
  state.me = e.target.value;
  localStorage.setItem("benchcare-role", state.me);
  toast(`已切换身份：${ROLE_LABELS[state.me]}`);
  router();
};

if (!location.hash) location.hash = "#/map";
router();
