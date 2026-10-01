/* 公共座椅管护 —— 前端单页应用（原生 JS，零依赖） */
"use strict";

const state = {
  tab: "map",
  meta: null,
  me: { name: "游客", role: "citizen" },
  seats: [],
  orders: [],
  incidents: [],
  position: null,
  selectedSeatId: null,
  woFilter: "",
};

/* ---------------- API ---------------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败 ${res.status}`);
  return data;
}

async function refresh() {
  [state.seats, state.orders, state.incidents, state.position] = await Promise.all([
    api("/api/seats"), api("/api/work-orders"),
    api("/api/incidents"), api("/api/location"),
  ]);
  updateBadges();
  render();
}

function updateBadges() {
  const openWo = state.orders.filter(w => w.status !== "verified").length;
  const openInc = state.incidents.filter(i => i.status === "open").length;
  document.getElementById("woBadge").textContent = openWo || "";
  document.getElementById("incBadge").textContent = openInc || "";
}

/* ---------------- 小工具 ---------------- */
const $ = sel => document.querySelector(sel);
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toastRoot").appendChild(el);
  setTimeout(() => el.remove(), 3200);
}
function modal(html) {
  const root = $("#modalRoot");
  root.innerHTML = `<div class="modal-mask"><div class="modal">${html}</div></div>`;
  root.querySelector(".modal-mask").addEventListener("click", e => {
    if (e.target.classList.contains("modal-mask")) closeModal();
  });
  root.querySelector(".modal").scrollTop = 0;
}
function closeModal() { $("#modalRoot").innerHTML = ""; }

const WO_STATUS = { open: "待认领", claimed: "维修中", done: "待复验", verified: "已复验" };
const INC_STATUS = { open: "未结", closed: "已结" };

/* 简洁材质图标 */
function materialIcon(material, cls = "material-icon") {
  const common = `class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"`;
  const shapes = {
    wood: `<path d="M3.5 8h17M3.5 12.5h17M3.5 17h17"/><path d="M4 6.5v12.5M20 6.5v12.5"/>`,
    metal: `<path d="M5 4h14M7.5 4v16M16.5 4v16M5 20h14"/><circle cx="12" cy="9" r="1"/><circle cx="12" cy="15" r="1"/>`,
    stone: `<path d="M5 18 7 9.5 12.5 6 19 10 18.5 18 11 20Z"/><path d="M9 13h6"/>`,
    plastic: `<path d="M12 3.5 19.5 8v8L12 20.5 4.5 16V8Z"/><circle cx="12" cy="12" r="2.2"/>`,
  };
  return `<svg ${common} aria-label="${esc(state.meta?.materials[material] || "")}">${shapes[material] || ""}</svg>`;
}

function usableBadge(seat) {
  return seat.usable
    ? `<span class="badge safe">✓ 可使用</span>`
    : `<span class="badge unsafe">⛔ 封控停用</span>`;
}
function woStatusBadge(st) {
  const cls = { open: "gray", claimed: "unsafe", done: "wet", verified: "safe" }[st];
  return `<span class="badge ${cls}">${WO_STATUS[st]}</span>`;
}
function photoUrl(name) { return `/api/photos/${encodeURIComponent(name)}`; }

/* 经纬度 → SVG 坐标 */
function project() {
  const lats = state.seats.map(s => s.lat), lngs = state.seats.map(s => s.lng);
  const minLat = Math.min(...lats) - 0.0008, maxLat = Math.max(...lats) + 0.0012;
  const minLng = Math.min(...lngs) - 0.0012, maxLng = Math.max(...lngs) + 0.0012;
  const W = 1000, H = 640;
  return (lat, lng) => ({
    x: ((lng - minLng) / (maxLng - minLng)) * W,
    y: H - ((lat - minLat) / (maxLat - minLat)) * H,
  });
}

/* ---------------- 地图页 ---------------- */
function renderMap() {
  const proj = project();
  const P = proj(state.position.lat, state.position.lng);
  const markers = state.seats.map(s => {
    const p = proj(s.lat, s.lng);
    const sel = s.id === state.selectedSeatId;
    const unsafe = !s.usable;
    return `<g class="map-marker ${unsafe ? "unsafe" : ""} ${s.open_incident_id ? "has-open" : ""} ${sel ? "selected" : ""}"
       transform="translate(${p.x.toFixed(1)},${p.y.toFixed(1)})" data-seat="${s.id}">
      ${unsafe ? '<circle class="ring" r="24" fill="none" stroke="#f07d22" stroke-width="2"/>' : ""}
      <circle class="body" r="17" fill="${unsafe ? "#f07d22" : "#1f5135"}" stroke="#fff" stroke-width="2.5"/>
      <g transform="translate(-9,-9) scale(.76)" style="color:#fff" pointer-events="none">
        ${materialIcon(s.material).replace("<svg ", '<svg width="24" height="24" ').replace(/class="material-icon[^"]*"/, "")}
      </g>
      <text y="34" text-anchor="middle" font-size="14" font-weight="700"
        fill="#1f5135" stroke="#fff" stroke-width="4" paint-order="stroke">${esc(s.code)}</text>
    </g>`;
  }).join("");

  return `
  <div class="two-col">
    <div>
      <div class="map-wrap">
        <svg class="park-map" viewBox="0 0 1000 640" id="parkMap">
          <!-- 公园绿地 -->
          <rect x="0" y="0" width="690" height="640" fill="#dcead4"/>
          <rect x="690" y="0" width="310" height="640" fill="#e7e7e2"/>
          <text x="330" y="48" text-anchor="middle" font-size="26" font-weight="700" fill="#2e6b48" opacity=".55">中央公园</text>
          <text x="848" y="48" text-anchor="middle" font-size="24" font-weight="700" fill="#777" opacity=".6">文一路</text>
          <!-- 湖 -->
          <ellipse cx="330" cy="300" rx="150" ry="92" fill="#bcd9e8"/>
          <text x="330" y="308" text-anchor="middle" font-size="18" fill="#3d7896" opacity=".7">景观湖</text>
          <!-- 步道 -->
          <path d="M40 560 C 260 470, 420 520, 660 400" stroke="#f4f1e4" stroke-width="22" fill="none"/>
          <path d="M210 60 L 560 620" stroke="#f4f1e4" stroke-width="16" fill="none"/>
          <!-- 马路 -->
          <line x1="690" y1="0" x2="690" y2="640" stroke="#b9b9b2" stroke-width="10"/>
          <line x1="700" y1="0" x2="700" y2="640" stroke="#fff" stroke-width="2" stroke-dasharray="18 16"/>
          <rect x="760" y="470" width="170" height="70" rx="10" fill="#cfd6cc"/>
          <text x="845" y="512" text-anchor="middle" font-size="15" fill="#5c6b63">公交站</text>
          <!-- 树木点缀 -->
          ${[[120,150],[560,140],[150,430],[600,520],[470,200]].map(([x,y]) =>
            `<circle cx="${x}" cy="${y}" r="20" fill="#9ec49a"/><circle cx="${x}" cy="${y}" r="12" fill="#7fb279"/>`).join("")}
          ${markers}
          <!-- 模拟定位 -->
          <g transform="translate(${P.x.toFixed(1)},${P.y.toFixed(1)})" pointer-events="none">
            <circle r="20" fill="#2464d8" opacity=".18"/>
            <circle class="pos-pin" r="7"/>
            <text y="-16" text-anchor="middle" font-size="13" fill="#2464d8" font-weight="700">我的位置</text>
          </g>
        </svg>
        <div class="map-legend">
          <span><i class="dot green"></i>可使用</span>
          <span><i class="dot orange"></i>封控/报修</span>
          <span><i class="pos-pin" style="width:10px;height:10px"></i>模拟定位</span>
          <span>图标区分材质：木 / 钢 / 石 / 塑木</span>
        </div>
      </div>
      <p class="muted" style="margin-top:6px">${esc(state.position.desc)} · 定位适配器：mock（可替换为真实 GPS）</p>
    </div>
    <div>
      <div class="section-title">附近座椅</div>
      <div id="mapSeatList">${state.seats
        .slice()
        .sort((a, b) => (a.usable - b.usable) || a.code.localeCompare(b.code))
        .map(seatListItem).join("")}</div>
    </div>
  </div>`;
}

function seatListItem(s) {
  const openInc = state.incidents.find(i => i.id === s.open_incident_id);
  return `<div class="card seat-item ${s.usable ? "" : "unsafe"}" data-seat-card="${s.id}">
    <div class="iconwrap" style="color:${s.usable ? "#1f5135" : "#b45a12"}">${materialIcon(s.material, "material-icon lg")}</div>
    <div class="main">
      <div class="row"><span class="code">${esc(s.code)}</span>${usableBadge(s)}</div>
      <div class="loc">${esc(s.name)} · ${esc(s.location)}</div>
      <div class="muted">${esc(state.meta.materials[s.material])} · ${esc(state.meta.fixture[s.fixture])}
        ${openInc && openInc.wo_code ? ` · <span class="wo-ref" data-wo="${openInc.work_order_id}">${esc(openInc.wo_code)}</span>` : ""}
      </div>
    </div>
    <div class="chev">›</div>
  </div>`;
}

/* ---------------- 座椅详情（移动端：位置 + 安全状态优先） ---------------- */
async function openSeat(seatId, jumpMap = true) {
  const s = await api(`/api/seats/${seatId}`);
  const r = state.me.role;
  const wo = s.open_incident?.work_order_id
    ? state.orders.find(w => w.id === s.open_incident.work_order_id) : null;
  const tape = wo ? await api(`/api/work-orders/${wo.id}`).then(d => d.tape_state).catch(() => "none") : "none";

  modal(`
    <h3>${esc(s.code)} ${esc(s.name)}
      <span class="x" data-close>×</span></h3>

    <!-- 安全状态置顶 -->
    <div class="safety-banner ${s.usable ? "ok" : "block"}">
      <span class="big">${s.usable ? "✅" : "⚠️"}</span>
      <span>${s.usable ? "安全状态：可正常使用" : "安全状态：封控停用，请勿落座"}</span>
    </div>

    <div class="card" style="box-shadow:none">
      <div class="row" style="margin-bottom:6px">
        <span class="iconwrap" style="display:inline-flex;width:38px;height:38px;border-radius:10px;
          background:${s.usable ? "#e3efe6" : "#fdeedd"};color:${s.usable ? "#1f5135" : "#b45a12"}">
          ${materialIcon(s.material, "material-icon lg")}</span>
        <strong>${esc(state.meta.materials[s.material])}座椅</strong>
        ${usableBadge(s)}
      </div>
      <dl class="kv">
        <dt>位置</dt><dd><strong>${esc(s.park)}</strong> · ${esc(s.location)}</dd>
        <dt>坐标</dt><dd>${s.lat.toFixed(5)}, ${s.lng.toFixed(5)}（模拟定位）</dd>
        <dt>编号</dt><dd>${esc(s.code)}</dd>
        <dt>扶手</dt><dd>${esc(state.meta.armrest[s.armrest])}</dd>
        <dt>固定件</dt><dd>${esc(state.meta.fixture[s.fixture])}</dd>
        ${s.status_note ? `<dt>备注</dt><dd class="badge unsafe">${esc(s.status_note)}</dd>` : ""}
      </dl>
      <div class="row">
        <button class="btn line sm" data-show-map="${s.id}">🗺️ 地图定位</button>
        ${wo ? `<span class="muted">关联工单：<span class="wo-ref" data-wo="${wo.id}">${esc(wo.code)}</span></span>` : ""}
      </div>
      ${tape === "on" ? '<p class="badge tape" style="margin-top:8px">警示带围蔽中</p>' : ""}
    </div>

    ${s.open_incident ? incidentBlock(s.open_incident) : ""}

    ${r === "citizen" ? reportForm(s) : ""}
  `);

  modalBindSeat(s, jumpMap);
}

function incidentBlock(inc) {
  return `<div class="card" style="box-shadow:none;border-color:#f0c99a">
    <div class="row"><span class="badge unsafe">未结事件 #${inc.id}</span>
      <strong>${esc(inc.title)}</strong></div>
    <p class="muted" style="margin-top:6px">重复报损会自动归并到本事件，不重复开工单。</p>
  </div>`;
}

function reportForm(s) {
  const opts = Object.entries(state.meta.damage_types)
    .map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("");
  return `<div class="card" style="box-shadow:none">
    <div class="section-title">📣 市民报损</div>
    <label class="field">损坏情况
      <select id="rpType">${opts}</select></label>
    <div id="rpMeasure" class="measure-box"></div>
    <label class="field">描述（选填）<textarea id="rpDesc" placeholder="说说您看到的情况"></textarea></label>
    <label class="field">联系方式（选填）<input type="tel" id="rpPhone" placeholder="便于反馈处理结果"></label>
    <label class="field">现场照片（选填）<input type="file" id="rpPhoto" accept="image/*"></label>
    <button class="btn orange" id="rpSubmit">提交报损</button>
    <p class="muted" id="rpHint"></p>
  </div>`;
}

function modalBindSeat(s, jumpMap) {
  $("#modalRoot [data-close]").onclick = closeModal;
  const mapBtn = $("#modalRoot [data-show-map]");
  if (mapBtn) mapBtn.onclick = () => {
    state.selectedSeatId = s.id; switchTab("map"); closeModal();
  };
  $("#modalRoot [data-wo]")?.addEventListener("click", e => {
    e.stopPropagation(); closeModal(); openWorkOrder(+e.target.dataset.wo);
  });

  const typeSel = $("#rpType");
  const measure = $("#rpMeasure");
  const fillMeasure = () => {
    if (!typeSel) return;
    const v = state.meta.damage_types[typeSel.value];
    measure.innerHTML = `<strong>现场措施：</strong>${esc(v.measure)}`;
  };
  if (typeSel) { typeSel.onchange = fillMeasure; fillMeasure(); }

  $("#rpSubmit")?.addEventListener("click", async () => {
    try {
      const res = await api("/api/reports", { method: "POST", body: {
        seat_id: s.id, damage_type: typeSel.value,
        description: $("#rpDesc").value, phone: $("#rpPhone").value,
      }});
      const file = $("#rpPhoto").files?.[0];
      if (file) await uploadPhoto(file, s.id, "report", null);
      toast(res.merged
        ? `已归并到该座椅的未结事件 #${res.incident_id}（不重复派单）`
        : `报损成功，已生成事件 #${res.incident_id}`, "ok");
      closeModal(); refresh();
    } catch (e) { toast(e.message, "err"); }
  });
}

async function uploadPhoto(file, seatId, phase, workOrderId) {
  const data = await new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result.split(",")[1]);
    fr.onerror = reject;
    fr.readAsDataURL(file);
  });
  const ext = (file.name.split(".").pop() || "jpg").toLowerCase();
  return api(`/api/seats/${seatId}/photos`, { method: "POST",
    body: { phase, data, ext, work_order_id: workOrderId } });
}

/* ---------------- 工单 ---------------- */
function renderOrders() {
  const filters = [["", "全部"], ["open", "待认领"], ["claimed", "维修中"],
                   ["done", "待复验"], ["verified", "已复验"]];
  const list = state.orders
    .filter(w => !state.woFilter || w.status === state.woFilter)
    .map(w => {
      const inc = state.incidents.find(i => i.id === w.incident_id);
      const reportCount = inc?.report_count ?? "—";
      return `<div class="card" data-wo-card="${w.id}" style="cursor:pointer">
        <div class="row" style="justify-content:space-between">
          <span class="wo-ref" data-wo="${w.id}">${esc(w.code)}</span>
          ${woStatusBadge(w.status)}
        </div>
        <h3 style="margin:6px 0 2px">${esc(w.seat_code)} ${esc(w.summary)}</h3>
        <div class="muted">${esc(w.park)} · ${esc(w.location)}</div>
        <div class="row" style="margin-top:8px">
          <span class="badge gray">报损 ${reportCount} 次（已归并）</span>
          ${w.crew ? `<span class="badge safe">班组：${esc(w.crew)}</span>` : '<span class="badge gray">无人认领</span>'}
          ${w.paint_wet ? '<span class="badge wet">🎨 油漆未干</span>' : ""}
          <button class="btn sm ghost" data-wo-map="${w.seat_id}">地图位置</button>
        </div>
      </div>`;
    }).join("");
  return `
    <div class="filterbar">
      ${filters.map(([k, label]) =>
        `<button class="${state.woFilter === k ? "on" : ""}" data-filter="${k}">${label}</button>`).join("")}
    </div>
    ${list || '<p class="muted">暂无工单</p>'}`;
}

async function openWorkOrder(woId) {
  const w = await api(`/api/work-orders/${woId}`);
  const r = state.me.role;
  const photoBy = phase => w.photos.filter(p => p.phase === phase);
  const before = photoBy("before")[0], after = photoBy("after")[0];
  const reopen = photoBy("reopen")[0];

  modal(`
    <h3><span class="wo-ref">${esc(w.code)}</span>
      ${woStatusBadge(w.status)}
      <span class="x" data-close>×</span></h3>

    <!-- 位置与安全置顶 -->
    <div class="safety-banner ${w.seat_usable ? "ok" : "block"}">
      <span class="big">${w.seat_usable ? "✅" : "⚠️"}</span>
      <span>${esc(w.seat_code)} · ${esc(w.park)} —— ${w.seat_usable ? "可使用" : "封控停用"}
        <button class="btn sm line" data-wo-map="${w.seat_id}" style="margin-left:8px">地图定位</button>
      </span>
    </div>

    <div class="card" style="box-shadow:none">
      <div class="row"><span class="iconwrap" style="display:inline-flex;width:34px;height:34px;border-radius:9px;
        background:#e3efe6;color:#1f5135">${materialIcon(w.material, "material-icon lg")}</span>
        <strong>${esc(w.seat_code)} ${esc(w.seat_name)}</strong></div>
      <dl class="kv">
        <dt>位置</dt><dd>${esc(w.location)}</dd>
        <dt>班组</dt><dd>${esc(w.crew || "（待认领）")}</dd>
        <dt>警示带</dt><dd>${{ on: '<span class="badge tape">围蔽中</span>',
                              off: '<span class="badge safe">已撤除</span>',
                              none: '<span class="badge gray">未封控</span>' }[w.tape_state]}</dd>
      </dl>
      <p class="muted">${esc(w.summary)}${w.repair_note ? "；维修记录：" + esc(w.repair_note) : ""}</p>
    </div>

    <div class="section-title">📣 归并的报损（${w.reports.length}）</div>
    <div class="card" style="box-shadow:none">
      ${w.reports.map(rp => `<div style="padding:6px 0;border-bottom:1px solid var(--line)">
        <span class="damage-chip">${esc(state.meta.damage_types[rp.damage_type].label)}</span>
        <strong>${esc(rp.reporter)}</strong>
        <span class="muted">${esc(rp.created_at)}</span>
        <div>${esc(rp.description)}</div>
        <div class="measure-box"><strong>现场措施：</strong>${esc(state.meta.damage_types[rp.damage_type].measure)}</div>
      </div>`).join("")}
    </div>

    <!-- 维修前后照片并排 -->
    <div class="section-title">📷 维修前 / 维修后（并排核对）</div>
    <div class="photo-pair">
      <div class="photo-cell">
        <div class="cap"><span>维修前</span>${r === "crew" && ["claimed", "done"].includes(w.status) ? labelUpload("before", w) : ""}</div>
        ${before ? `<img src="${photoUrl(before.file_name)}" alt="维修前">` : '<div class="empty">暂无照片</div>'}
      </div>
      <div class="photo-cell">
        <div class="cap"><span>维修后</span>${r === "crew" && ["claimed", "done"].includes(w.status) ? labelUpload("after", w) : ""}</div>
        ${after ? `<img src="${photoUrl(after.file_name)}" alt="维修后">` : '<div class="empty">暂无照片</div>'}
      </div>
    </div>

    <!-- 班组操作区 -->
    ${r === "crew" ? crewPanel(w) : ""}

    <!-- 材料消耗 -->
    <div class="section-title">🧰 材料消耗</div>
    <div class="card" style="box-shadow:none">
      ${w.materials.length ? `<table class="mat">
        <tr><th>材料</th><th>数量</th><th>单位</th><th>登记时间</th></tr>
        ${w.materials.map(m => `<tr><td>${esc(m.name)}</td><td>${m.quantity}</td><td>${esc(m.unit)}</td><td class="muted">${esc(m.created_at)}</td></tr>`).join("")}
      </table>` : '<p class="muted">暂无材料记录</p>'}
      ${r === "crew" && ["claimed", "done"].includes(w.status) ? materialForm(w) : ""}
    </div>

    <!-- 复验区 -->
    ${r === "inspector" && ["done", "claimed"].includes(w.status) ? inspectorPanel(w, reopen) : ""}

    <!-- 时间线 -->
    <div class="section-title">🕘 流程记录</div>
    <ul class="timeline">
      <li>工单创建 ${esc(w.created_at)}</li>
      ${w.claimed_at ? `<li>班组「${esc(w.crew)}」认领 ${esc(w.claimed_at)}</li>` : ""}
      ${w.closures.map(c => `<li class="${c.action === "tape_on" ? "warn" : ""}">
        ${c.action === "tape_on" ? "🚧 设置警示带封控" : "✅ 撤除警示带"} —— ${esc(c.operator)}
        <div class="t">${esc(c.created_at)} ${esc(c.note)}</div></li>`).join("")}
      ${w.finished_at ? `<li>班组完工 ${esc(w.finished_at)}${w.paint_wet ? '（<strong style="color:#b45a12">油漆未干</strong>）' : ""}</li>` : ""}
      ${w.inspections.map(i => `<li class="${i.passed ? "" : "warn"}">
        ${i.passed ? "✔ 复验通过，恢复使用" : "↩ 复验驳回，返工"} —— 巡查员${esc(i.inspector)}
        <div class="t">${esc(i.created_at)} ${esc(i.note)}</div></li>`).join("")}
    </ul>
  `);

  bindWorkOrderModal(w);
}

function labelUpload(phase, w) {
  return `<label style="color:#1f5135;cursor:pointer;font-weight:600">上传
    <input type="file" accept="image/*" hidden data-upload="${phase}" data-seat="${w.seat_id}" data-wo="${w.id}"></label>`;
}

function crewPanel(w) {
  if (w.status === "open") {
    return `<div class="card" style="box-shadow:none;border-color:#f0c99a">
      <div class="section-title">🙋 班组接单</div>
      <p class="muted">多名班组同时认领时，仅一组获得任务（服务端原子确认）。</p>
      <button class="btn orange" data-action="claim" data-wo="${w.id}">认领工单</button>
    </div>`;
  }
  if (w.status === "claimed") {
    return `<div class="card" style="box-shadow:none;border-color:#f0c99a">
      <div class="section-title">🔧 维修作业（班组：${esc(w.crew)}）</div>
      <div class="row">
        ${w.tape_state === "on"
          ? `<button class="btn green sm" data-action="tape-off" data-wo="${w.id}">✅ 撤除警示带</button>`
          : `<button class="btn orange sm" data-action="tape-on" data-wo="${w.id}">🚧 设置警示带封控</button>`}
      </div>
      <label class="field">完工说明<textarea id="finishNote" placeholder="处置过程与结果">${esc(w.repair_note)}</textarea></label>
      <label class="checkline"><input type="checkbox" id="paintWet">
        <span>🎨 油漆未干（保留封控，复验将被<strong style="color:#b45a12">阻断</strong>，晾干后再解除）</span></label>
      <button class="btn green" data-action="finish" data-wo="${w.id}">填报完工，提交复验</button>
    </div>`;
  }
  if (w.status === "done") {
    return `<div class="card" style="box-shadow:none">
      <div class="section-title">⏳ 等待巡查员复验</div>
      ${w.tape_state === "on"
        ? `<div class="row" style="margin-bottom:8px">
             <button class="btn green sm" data-action="tape-off" data-wo="${w.id}">✅ 现场已清理，撤除警示带</button>
           </div>` : ""}
      ${w.paint_wet
        ? `<div class="safety-banner block" style="margin-bottom:8px"><span class="big">🎨</span>
           <span>油漆未干，座椅保持封控；确认晾干后可解除标记，再请巡查员复验。</span></div>
           <button class="btn orange sm" data-action="paint-dry" data-wo="${w.id}">确认油漆已干</button>`
        : '<p class="muted">请保持现场，等待复验；如被驳回将退回维修。</p>'}
    </div>`;
  }
  return "";
}

function materialForm(w) {
  return `<div class="form-grid" style="margin-top:8px">
    <label class="field">材料<input type="text" id="matName" placeholder="如 防腐木板 / 膨胀螺栓 / 木蜡油"></label>
    <label class="field">数量<input type="number" id="matQty" min="0" step="0.5" placeholder="0"></label>
    <label class="field">单位<input type="text" id="matUnit" value="件"></label>
    <div style="display:flex;align-items:end"><button class="btn sm green" data-action="add-mat" data-wo="${w.id}">登记消耗</button></div>
  </div>`;
}

function inspectorPanel(w, reopenPhoto) {
  if (w.status !== "done") {
    return `<div class="card" style="box-shadow:none">
      <div class="section-title">🔍 巡查员复验</div>
      <p class="muted">工单尚未提交完工，暂不可复验。</p></div>`;
  }
  const blockers = [];
  if (w.paint_wet) blockers.push("🎨 油漆未干");
  if (w.tape_state !== "off") blockers.push("🚧 警示带未撤除");
  if (!reopenPhoto) blockers.push("📷 缺少重开核对照片");
  return `<div class="card" style="box-shadow:none;border-color:#1f5135">
    <div class="section-title">🔍 巡查员复验（维修人不得自验）</div>
    ${blockers.length ? `<div class="safety-banner block"><span class="big">⛔</span><span>
      恢复使用前阻断项：${blockers.map(esc).join("、")}</span></div>` : ""}
    <label class="field">重开核对照片（警示带撤除后的现场）
      <input type="file" accept="image/*" data-upload="reopen" data-seat="${w.seat_id}" data-wo="${w.id}"></label>
    ${reopenPhoto ? `<div class="photo-row" style="margin:6px 0"><figure>
      <img src="${photoUrl(reopenPhoto.file_name)}"><figcaption>重开核对照片</figcaption></figure></div>` : ""}
    <label class="checkline"><input type="checkbox" id="tapeConfirm">
      <span>我已现场核对：<strong>警示带已全部撤除</strong>，现场无遗留工具与告示牌</span></label>
    <label class="field">复验意见<textarea id="inspNote" placeholder="通过或驳回说明"></textarea></label>
    <div class="row">
      <button class="btn green" data-action="inspect-pass" data-wo="${w.id}"
        ${blockers.length ? "disabled title='存在阻断项'" : ""}>✔ 复验通过，恢复使用</button>
      <button class="btn line" data-action="inspect-reject" data-wo="${w.id}">↩ 驳回返工</button>
    </div>
  </div>`;
}

function bindWorkOrderModal(w) {
  $("#modalRoot [data-close]").onclick = closeModal;
  $("#modalRoot").onclick = async e => {
    const mapBtn = e.target.closest("[data-wo-map]");
    if (mapBtn) {
      state.selectedSeatId = +mapBtn.dataset.woMap;
      switchTab("map"); closeModal(); return;
    }
  };

  // 照片上传
  $("#modalRoot").querySelectorAll("[data-upload]").forEach(inp => {
    inp.addEventListener("change", async () => {
      const file = inp.files[0];
      if (!file) return;
      try {
        await uploadPhoto(file, +inp.dataset.seat, inp.dataset.upload, +inp.dataset.wo);
        toast("照片已上传", "ok");
        closeModal(); openWorkOrder(w.id);
      } catch (err) { toast(err.message, "err"); }
    });
  });

  $("#modalRoot").querySelectorAll("[data-action]").forEach(btn => {
    btn.addEventListener("click", () => woAction(btn.dataset.action, +btn.dataset.wo));
  });
}

async function woAction(action, woId) {
  try {
    if (action === "claim") {
      const w = await api(`/api/work-orders/${woId}/claim`, { method: "POST", body: {} });
      toast(`认领成功：${w.code}`, "ok");
    } else if (action === "tape-on") {
      await api(`/api/work-orders/${woId}/closures`, { method: "POST", body: { action: "tape_on" } });
      toast("已登记：设置警示带封控", "ok");
    } else if (action === "tape-off") {
      await api(`/api/work-orders/${woId}/closures`, { method: "POST", body: { action: "tape_off" } });
      toast("已登记：警示带撤除", "ok");
    } else if (action === "finish") {
      await api(`/api/work-orders/${woId}/finish`, { method: "POST", body: {
        repair_note: $("#finishNote").value,
        paint_wet: $("#paintWet").checked,
      }});
      toast("已完工，等待巡查员复验", "ok");
    } else if (action === "paint-dry") {
      await api(`/api/work-orders/${woId}/paint-dry`, { method: "POST", body: {} });
      toast("油漆已干标记解除，可申请复验", "ok");
    } else if (action === "add-mat") {
      await api(`/api/work-orders/${woId}/materials`, { method: "POST", body: {
        name: $("#matName").value.trim(),
        quantity: parseFloat($("#matQty").value),
        unit: $("#matUnit").value.trim() || "件",
      }});
      toast("材料消耗已登记", "ok");
    } else if (action === "inspect-pass") {
      await api(`/api/work-orders/${woId}/inspect`, { method: "POST", body: {
        passed: true, note: $("#inspNote").value, tape_confirmed: $("#tapeConfirm").checked,
      }});
      toast("复验通过，座椅恢复可使用", "ok");
    } else if (action === "inspect-reject") {
      await api(`/api/work-orders/${woId}/inspect`, { method: "POST", body: {
        passed: false, note: $("#inspNote").value,
      }});
      toast("已驳回，工单退回维修", "");
    }
    closeModal(); refresh();
  } catch (e) { toast(e.message, "err"); }
}

/* ---------------- 报损事件页 ---------------- */
function renderIncidents() {
  return state.incidents.map(i => `
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <span class="badge ${i.status === "open" ? "unsafe" : "safe"}">
          ${INC_STATUS[i.status]}事件 #${i.id}</span>
        ${i.wo_code ? `<span class="wo-ref" data-wo="${i.work_order_id}">${esc(i.wo_code)}</span>
          ${woStatusBadge(i.wo_status)}` : ""}
      </div>
      <h3 style="margin:6px 0 2px">${esc(i.title)}</h3>
      <div class="muted">${esc(i.seat_code)} · ${esc(i.park)} · ${esc(i.location)}</div>
      <div class="row" style="margin-top:8px">
        <span class="badge gray">归并报损 ${i.report_count} 次</span>
        <button class="btn sm ghost" data-seat-btn="${i.seat_id}">座椅详情/地图</button>
      </div>
    </div>`).join("") || '<p class="muted">暂无事件</p>';
}

/* ---------------- 台账页 ---------------- */
function renderSeats() {
  return state.seats.map(seatListItem).join("");
}

/* ---------------- 渲染与导航 ---------------- */
function render() {
  const titles = { map: "地图", seats: "座椅台账", orders: "维修工单", incidents: "报损事件" };
  const views = {
    map: renderMap, seats: renderSeats, orders: renderOrders, incidents: renderIncidents,
  };
  $("#app").innerHTML = views[state.tab]();
  bindGlobal();
}

function bindGlobal() {
  document.querySelectorAll("[data-seat],[data-seat-card],[data-seat-btn]").forEach(el => {
    el.addEventListener("click", e => {
      if (e.target.closest("[data-wo]")) return;
      const id = +(el.dataset.seat || el.dataset.seatCard || el.dataset.seatBtn);
      openSeat(id);
    });
  });
  document.querySelectorAll("[data-wo]").forEach(el => {
    if (el.closest("[data-seat-card]") || el.closest(".card")) {
      el.addEventListener("click", e => {
        e.stopPropagation();
        openWorkOrder(+el.dataset.wo);
      });
    }
  });
  document.querySelectorAll("[data-wo-card]").forEach(el => {
    el.addEventListener("click", e => {
      if (e.target.closest("button") || e.target.closest("[data-wo]")) return;
      openWorkOrder(+el.dataset.woCard);
    });
  });
  document.querySelectorAll("[data-filter]").forEach(b =>
    b.addEventListener("click", () => { state.woFilter = b.dataset.filter; render(); }));
}

function switchTab(t) {
  state.tab = t;
  document.querySelectorAll("nav.tabs button").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === t));
  render();
}

/* ---------------- 登录 ---------------- */
async function login() {
  const role = $("#roleSelect").value;
  const name = $("#userName").value.trim();
  const me = await api("/api/login", { method: "POST", body: { role, name } });
  state.me = { name: me.name, role: me.role };
  toast(`已切换为：${me.name}（${state.meta.roles[me.role]}）`, "ok");
  refresh();
}

async function boot() {
  state.meta = await api("/api/meta");
  state.me = await api("/api/me");
  $("#userName").value = state.me.role === "citizen" && state.me.name === "游客" ? "" : state.me.name;
  $("#roleSelect").value = state.me.role;
  $("#loginBtn").onclick = login;
  document.querySelectorAll("nav.tabs button").forEach(b =>
    b.addEventListener("click", () => switchTab(b.dataset.tab)));
  await refresh();
}
boot().catch(e => toast("启动失败：" + e.message, "err"));
