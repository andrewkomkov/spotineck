// spotineck web — общается с spotineck-api (REST + WS /ws)
const $ = (s) => document.querySelector(s);
const api = (path, opts) => fetch("/api" + path, opts);
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : null });
const put = (path, body) =>
  api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

let state = null;
let lastStateAt = 0;       // performance.now() когда пришёл прогресс — для интерполяции
let seeking = false;

// ───── навигация (сайдбар + нижнее меню) ─────
function switchView(view) {
  document.querySelectorAll("[data-view]").forEach((x) => x.classList.toggle("active", x.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  const el = $("#view-" + view);
  if (el) el.classList.remove("hidden");
  if (view === "queue") loadQueue();
  if (view === "settings") loadSettings();
}
document.querySelectorAll("[data-view]").forEach((b) =>
  b.addEventListener("click", () => switchView(b.dataset.view))
);

// ───── настройки: имя устройства ─────
async function loadSettings() {
  try {
    const d = await (await api("/device-name")).json();
    $("#device-name-input").value = d.name || "";
  } catch {}
  const sc = $("#set-conn"); if (sc) sc.textContent = connOk ? "в сети" : "нет связи";
}
async function saveDeviceName() {
  const btn = $("#device-name-save");
  const name = $("#device-name-input").value.trim();
  if (!name) return;
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await post("/device-name", { name });
    const d = await r.json().catch(() => ({}));
    $("#device-name-hint").textContent = r.ok
      ? `Готово — устройство теперь «${d.name}». Spotify Connect перезапущен (~3 сек).`
      : `Ошибка: ${d.error || r.status}`;
  } catch (e) {
    $("#device-name-hint").textContent = "Ошибка сети";
  }
  btn.disabled = false; btn.textContent = "Сохранить";
}
$("#device-name-save").addEventListener("click", saveDeviceName);
$("#device-name-input").addEventListener("keydown", (e) => { if (e.key === "Enter") saveDeviceName(); });

// ───── транспорт ─────
$("#c-play").addEventListener("click", () => post("/playback/toggle"));
$("#c-next").addEventListener("click", () => post("/playback/next"));
$("#c-prev").addEventListener("click", () => post("/playback/previous"));
$("#c-shuffle").addEventListener("click", () =>
  post("/playback/shuffle", { enabled: !(state && state.playback.shuffle) })
);
$("#c-repeat").addEventListener("click", () => {
  const order = ["off", "all", "single"];
  const cur = state ? state.playback.repeat : "off";
  post("/playback/repeat", { mode: order[(order.indexOf(cur) + 1) % 3] });
});

// seek по клику
$("#seek-bar").addEventListener("click", (e) => {
  if (!state) return;
  const r = e.currentTarget.getBoundingClientRect();
  const pos = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  post("/playback/seek", { position_ms: Math.round(pos * state.playback.length_ms) });
});
// громкость зоны по клику
$("#vol-bar").addEventListener("click", (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  const v = Math.round(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * 100);
  post("/playback/volume", { volume: v });
});

// ───── колонки ─────
$("#btn-all-on").addEventListener("click", () =>
  put("/speakers/group", { ids: state.speakers.map((s) => s.id) })
);
$("#btn-all-off").addEventListener("click", () => put("/speakers/group", { ids: [] }));

let speakersSig = "";

function speakerCard(sp) {
  const el = document.createElement("div");
  el.className = "speaker" + (sp.selected ? " on" : "");
  el.dataset.id = sp.id;
  el.innerHTML = `
    <div class="speaker-top">
      <div>
        <div class="speaker-name"></div>
        <div class="speaker-type"></div>
      </div>
      <label class="switch">
        <input type="checkbox" ${sp.selected ? "checked" : ""}/>
        <span class="slider"></span>
      </label>
    </div>
    <div class="speaker-vol">
      <span class="vic">🔈</span>
      <input type="range" class="vol-range" min="0" max="100" value="${sp.volume}" style="--p:${sp.volume}%"/>
    </div>
    <div class="offset-row">
      <span class="offset-label">задержка</span>
      <input type="range" class="offset-range" min="-2000" max="2000" step="10" value="${sp.offset_ms}"/>
      <span class="offset-val">${sp.offset_ms} ms</span>
    </div>
    ${sp.needs_auth ? `<div class="auth-row">
      <span class="badge-auth">нужен PIN с колонки:</span>
      <input class="pin-input" maxlength="4" inputmode="numeric" placeholder="0000"/>
      <button class="chip pin-ok">OK</button>
    </div>` : ""}
  `;
  el.querySelector(".speaker-name").textContent = sp.name;
  el.querySelector(".speaker-type").textContent = sp.type;
  el.querySelector("input[type=checkbox]").addEventListener("change", (e) =>
    post("/speakers/" + sp.id, { selected: e.target.checked })
  );
  const vol = el.querySelector(".vol-range");
  vol.addEventListener("input", (e) => e.target.style.setProperty("--p", e.target.value + "%"));
  vol.addEventListener("change", (e) => post("/speakers/" + sp.id, { volume: +e.target.value }));
  const off = el.querySelector(".offset-range");
  const offVal = el.querySelector(".offset-val");
  off.addEventListener("input", (e) => (offVal.textContent = e.target.value + " ms"));
  off.addEventListener("change", (e) => post("/speakers/" + sp.id, { offset_ms: +e.target.value }));
  if (sp.needs_auth) {
    const pin = el.querySelector(".pin-input");
    const send = async () => {
      const r = await post("/speakers/" + sp.id + "/verify", { pin: pin.value });
      if (r.ok) post("/speakers/" + sp.id, { selected: true });
      else { pin.value = ""; pin.placeholder = "ещё раз"; }
    };
    el.querySelector(".pin-ok").addEventListener("click", send);
    pin.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  }
  return el;
}

// Полный ребилд только при структурном изменении (состав/выбор/auth).
// Иначе обновляем значения на месте, не трогая контрол, который юзер сейчас тащит.
function renderSpeakers() {
  const sig = state.speakers.map((s) => s.id + s.selected + s.needs_auth).join("|");
  if (sig === speakersSig) { updateSpeakerValues(); return; }
  speakersSig = sig;
  const wrap = $("#speakers-list");
  wrap.innerHTML = "";
  state.speakers.forEach((sp) => wrap.appendChild(speakerCard(sp)));
}

function updateSpeakerValues() {
  for (const sp of state.speakers) {
    const card = document.querySelector('.speaker[data-id="' + sp.id + '"]');
    if (!card) continue;
    card.classList.toggle("on", sp.selected);
    const cb = card.querySelector("input[type=checkbox]");
    if (document.activeElement !== cb) cb.checked = sp.selected;
    const vol = card.querySelector(".vol-range");
    if (document.activeElement !== vol) { vol.value = sp.volume; vol.style.setProperty("--p", sp.volume + "%"); }
    const off = card.querySelector(".offset-range");
    if (document.activeElement !== off) {
      off.value = sp.offset_ms;
      card.querySelector(".offset-val").textContent = sp.offset_ms + " ms";
    }
  }
}

// ───── очередь ─────
async function loadQueue() {
  const q = await (await api("/queue")).json();
  const wrap = $("#queue-list");
  wrap.innerHTML = "";
  (q.items || []).forEach((it, i) => wrap.appendChild(trackRow(it, i + 1)));
  if (!(q.items || []).length) wrap.innerHTML = '<p class="muted">Очередь пуста. Запусти spotineck в Spotify.</p>';
}
$("#btn-clear-queue").addEventListener("click", async () => { await post("/queue/clear"); loadQueue(); });

function trackRow(it, n) {
  const el = document.createElement("div");
  el.className = "track";
  el.innerHTML = `<div class="tn">${n}</div>
    <div class="track-meta">
      <div class="track-title"></div>
      <div class="track-sub"></div>
    </div>`;
  el.querySelector(".track-title").textContent = it.title || "—";
  el.querySelector(".track-sub").textContent = [it.artist, it.album].filter(Boolean).join(" · ");
  return el;
}

// ───── поиск ─────
let searchTimer;
$("#search-input").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  searchTimer = setTimeout(() => doSearch(q), 300);
});
async function doSearch(q) {
  const wrap = $("#search-results");
  if (!q) { wrap.innerHTML = ""; return; }
  const r = await (await api("/search?q=" + encodeURIComponent(q))).json();
  wrap.innerHTML = "";
  const tracks = (r.tracks && r.tracks.items) || [];
  if (!tracks.length) { wrap.innerHTML = '<p class="muted">Ничего не найдено в локальной библиотеке.</p>'; return; }
  tracks.forEach((t, i) => wrap.appendChild(trackRow(t, i + 1)));
}

// ───── now playing ─────
function fmt(ms) {
  ms = Math.max(0, ms || 0);
  const s = Math.floor(ms / 1000);
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

function renderNow() {
  const p = state.playback, t = p.track;
  $("#np-title").textContent = t.title || (p.state === "stop" ? "Ничего не играет" : "spotineck");
  $("#np-artist").textContent = t.artist || "";
  const art = $("#np-art");
  if (t.artwork_url) { art.src = t.artwork_url + "?t=" + Date.now(); art.style.visibility = "visible"; }
  else art.style.visibility = "hidden";
  $("#c-play").textContent = p.state === "play" ? "⏸" : "▶";
  $("#c-shuffle").classList.toggle("active", p.shuffle);
  $("#c-repeat").classList.toggle("active", p.repeat !== "off");
  $("#c-repeat").textContent = p.repeat === "single" ? "↻¹" : "↻";
  $("#t-len").textContent = fmt(p.length_ms);
  $("#vol-fill").style.width = p.volume + "%";
}

function tickProgress() {
  if (!state) return;
  const p = state.playback;
  let prog = p.progress_ms;
  if (p.state === "play") prog += performance.now() - lastStateAt;
  if (p.length_ms > 0) {
    $("#seek-fill").style.width = Math.min(100, (prog / p.length_ms) * 100) + "%";
    $("#t-cur").textContent = fmt(prog);
  } else {
    $("#seek-fill").style.width = "0%";
    $("#t-cur").textContent = "0:00";
  }
}
setInterval(tickProgress, 250);

// ───── приём состояния ─────
function applyState(s) {
  state = s;
  lastStateAt = performance.now();
  renderSpeakers();
  renderNow();
}

// ───── WebSocket ─────
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onclose = () => { setConn(false); setTimeout(connectWS, 2000); };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") applyState(msg.data);
  };
}
let connOk = false;
function setConn(ok) {
  connOk = ok;
  $("#conn-dot").classList.toggle("ok", ok);
  $("#conn-text").textContent = ok ? "в сети" : "переподключение…";
  const sc = $("#set-conn"); if (sc) sc.textContent = ok ? "в сети" : "нет связи";
}

// первичная загрузка + старт
api("/state").then((r) => r.json()).then(applyState).catch(() => {});
connectWS();
