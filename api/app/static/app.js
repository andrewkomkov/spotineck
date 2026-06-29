// spotineck web — REST + WS /ws поверх spotineck-api
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const api = (path, opts) => fetch("/api" + path, opts);
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : null });
const put = (path, body) =>
  api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

let state = null;
let lastStateAt = 0;
let connOk = false;
let spotify = { configured: false, authorized: false };
let lastVol = 50;            // для mute/unmute
let curView = "now";

// ───────────────────────── toasts ─────────────────────────
function toast(msg, kind) {
  const el = document.createElement("div");
  el.className = "toast" + (kind === "err" ? " err" : "");
  el.innerHTML = `<span class="ti">${kind === "err" ? "⚠" : "✓"}</span><span></span>`;
  el.querySelector("span:last-child").textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 3000);
}
async function action(promise, okMsg) {
  try {
    const r = await promise;
    if (r && r.ok === false) throw new Error();
    if (r && typeof r.status === "number" && r.status >= 400) {
      const t = await r.text().catch(() => "");
      throw new Error(t || r.status);
    }
    if (okMsg) toast(okMsg);
    return true;
  } catch (e) {
    toast("Не получилось — " + (e.message || "ошибка"), "err");
    return false;
  }
}

// ───────────────────────── навигация ─────────────────────────
function switchView(view) {
  curView = view;
  $$("[data-view]").forEach((x) => x.classList.toggle("active", x.dataset.view === view));
  $$(".view").forEach((v) => v.classList.add("hidden"));
  const el = $("#view-" + view);
  if (el) el.classList.remove("hidden");
  $(".main").scrollTop = 0;
  if (view === "queue") loadQueue();
  if (view === "settings") loadSettings();
  if (view === "search") setTimeout(() => $("#search-input").focus(), 50);
}
$$("[data-view]").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
$("#np-open").addEventListener("click", () => switchView("now"));

// ───────────────────────── транспорт ─────────────────────────
function bindTransport(playSel, prevSel, nextSel, shufSel, repSel) {
  $(playSel).addEventListener("click", () => post("/playback/toggle"));
  $(nextSel).addEventListener("click", () => post("/playback/next"));
  $(prevSel).addEventListener("click", () => post("/playback/previous"));
  $(shufSel).addEventListener("click", () => post("/playback/shuffle", { enabled: !(state && state.playback.shuffle) }));
  $(repSel).addEventListener("click", () => {
    const order = ["off", "all", "single"];
    const cur = state ? state.playback.repeat : "off";
    post("/playback/repeat", { mode: order[(order.indexOf(cur) + 1) % 3] });
  });
}
bindTransport("#c-play", "#c-prev", "#c-next", "#c-shuffle", "#c-repeat");
bindTransport("#h-play", "#h-prev", "#h-next", "#h-shuffle", "#h-repeat");

// ───────────────────────── draggable bar (seek / volume) ─────────────────────────
// onCommit(ratio) шлётся на отпускании; во время тащи — только визуально.
function draggableBar(barEl, { onPreview, onCommit }) {
  let dragging = false;
  const ratioFromEvent = (e) => {
    const r = barEl.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    return Math.max(0, Math.min(1, x / r.width));
  };
  const start = (e) => {
    dragging = true; barEl.classList.add("drag");
    onPreview(ratioFromEvent(e));
    e.preventDefault();
  };
  const move = (e) => { if (dragging) onPreview(ratioFromEvent(e)); };
  const end = (e) => {
    if (!dragging) return;
    dragging = false; barEl.classList.remove("drag");
    onCommit(ratioFromEvent(e));
  };
  barEl.addEventListener("pointerdown", (e) => { barEl.setPointerCapture(e.pointerId); start(e); });
  barEl.addEventListener("pointermove", move);
  barEl.addEventListener("pointerup", end);
  barEl.addEventListener("pointercancel", end);
  return { isDragging: () => dragging };
}

// seek bars (нижний бар + hero)
let seekDrag = false, seekPreview = 0;
function setupSeek(barSel, fillSel, curSel) {
  const bar = $(barSel), fill = $(fillSel), cur = $(curSel);
  draggableBar(bar, {
    onPreview: (ratio) => {
      seekDrag = true; seekPreview = ratio;
      fill.style.width = ratio * 100 + "%";
      bar.querySelector(".bar-knob").style.left = ratio * 100 + "%";
      if (state && cur) cur.textContent = fmt(ratio * state.playback.length_ms);
    },
    onCommit: (ratio) => {
      seekDrag = false;
      if (state && state.playback.length_ms > 0)
        post("/playback/seek", { position_ms: Math.round(ratio * state.playback.length_ms) });
    },
  });
}
setupSeek("#seek-bar", "#seek-fill", "#t-cur");
setupSeek("#hero-seek", "#hero-fill", "#h-cur");

// volume bar (нижний)
let volDrag = false;
draggableBar($("#vol-bar"), {
  onPreview: (ratio) => {
    volDrag = true;
    $("#vol-fill").style.width = ratio * 100 + "%";
    $("#vol-bar .bar-knob").style.left = ratio * 100 + "%";
  },
  onCommit: (ratio) => { volDrag = false; setVolume(Math.round(ratio * 100)); },
});
function setVolume(v) {
  v = Math.max(0, Math.min(100, v));
  if (v > 0) lastVol = v;
  post("/playback/volume", { volume: v });
}
$("#vol-mute").addEventListener("click", () => {
  const cur = state ? state.playback.volume : 0;
  setVolume(cur > 0 ? 0 : lastVol || 40);
});

// master volume (range в «Колонки»)
const mvol = $("#master-vol");
mvol.addEventListener("input", (e) => {
  $("#master-vol-val").textContent = e.target.value;
  e.target.style.setProperty("--p", e.target.value + "%");
});
mvol.addEventListener("change", (e) => setVolume(+e.target.value));

// ───────────────────────── колонки ─────────────────────────
$("#btn-all-on").addEventListener("click", () =>
  action(put("/speakers/group", { ids: state.speakers.map((s) => s.id) }), "Все колонки в зоне"));
$("#btn-all-off").addEventListener("click", () =>
  action(put("/speakers/group", { ids: [] }), "Зона выключена"));
$("#btn-only-local").addEventListener("click", () => {
  const local = state.speakers.filter((s) => s.type === "ALSA").map((s) => s.id);
  action(put("/speakers/group", { ids: local }), "Только локальный звук");
});

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
    <div class="offset-presets">
      <button class="opreset" data-off="0">0</button>
      <button class="opreset" data-off="-250">−250</button>
      <button class="opreset" data-off="-500">−500</button>
      <button class="opreset" data-off="-1000">−1000 (видео)</button>
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
    post("/speakers/" + sp.id, { selected: e.target.checked }));
  const vol = el.querySelector(".vol-range");
  vol.addEventListener("input", (e) => e.target.style.setProperty("--p", e.target.value + "%"));
  vol.addEventListener("change", (e) => post("/speakers/" + sp.id, { volume: +e.target.value }));
  const off = el.querySelector(".offset-range");
  const offVal = el.querySelector(".offset-val");
  off.addEventListener("input", (e) => (offVal.textContent = e.target.value + " ms"));
  off.addEventListener("change", (e) => post("/speakers/" + sp.id, { offset_ms: +e.target.value }));
  el.querySelectorAll(".opreset").forEach((b) => b.addEventListener("click", () => {
    const v = +b.dataset.off;
    off.value = v; offVal.textContent = v + " ms";
    action(post("/speakers/" + sp.id, { offset_ms: v }), `${sp.name}: задержка ${v} ms`);
  }));
  if (sp.needs_auth) {
    const pin = el.querySelector(".pin-input");
    const send = async () => {
      const r = await post("/speakers/" + sp.id + "/verify", { pin: pin.value });
      if (r.ok) { post("/speakers/" + sp.id, { selected: true }); toast(sp.name + " подтверждена"); }
      else { pin.value = ""; pin.placeholder = "ещё раз"; toast("Неверный PIN", "err"); }
    };
    el.querySelector(".pin-ok").addEventListener("click", send);
    pin.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  }
  return el;
}
function renderSpeakers() {
  const sig = state.speakers.map((s) => s.id + s.selected + s.needs_auth).join("|");
  if (sig !== speakersSig) {
    speakersSig = sig;
    const wrap = $("#speakers-list"); wrap.innerHTML = "";
    state.speakers.forEach((sp) => wrap.appendChild(speakerCard(sp)));
  } else {
    updateSpeakerValues();
  }
}
function updateSpeakerValues() {
  for (const sp of state.speakers) {
    const card = document.querySelector('.speaker[data-id="' + CSS.escape(sp.id) + '"]');
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

// зона: чипы быстрого включения (в Now-hero)
function renderZoneChips() {
  const wrap = $("#now-chips"); wrap.innerHTML = "";
  state.speakers.forEach((sp) => {
    const c = document.createElement("button");
    c.className = "zchip" + (sp.selected ? " on" : "");
    c.innerHTML = `<span class="zdot"></span><span></span>`;
    c.querySelector("span:last-child").textContent = sp.name;
    c.addEventListener("click", () => post("/speakers/" + sp.id, { selected: !sp.selected }));
    wrap.appendChild(c);
  });
  const n = state.speakers.filter((s) => s.selected).length;
  $("#zone-summary").textContent = n ? `В зоне: ${n} ${plural(n, "колонка", "колонки", "колонок")}` : "Зона пуста — включи колонку";
}
function plural(n, a, b, c) {
  const m = n % 100, d = n % 10;
  if (m >= 11 && m <= 14) return c;
  if (d === 1) return a; if (d >= 2 && d <= 4) return b; return c;
}

// ───────────────────────── очередь ─────────────────────────
async function loadQueue() {
  const wrap = $("#queue-list");
  wrap.innerHTML = '<div class="empty"><span class="spin"></span></div>';
  try {
    const q = await (await api("/queue")).json();
    wrap.innerHTML = "";
    const items = q.items || [];
    const curId = state && state.playback ? null : null;
    if (!items.length) { wrap.innerHTML = '<div class="empty">Очередь пуста. Запусти музыку из Поиска или выбери spotineck в Spotify.</div>'; return; }
    items.forEach((it, i) => {
      const playing = state && state.playback.track && it.title === state.playback.track.title && it.artist === state.playback.track.artist;
      wrap.appendChild(rowEl({
        num: i + 1,
        title: it.title || "—",
        sub: [it.artist, it.album].filter(Boolean).join(" · "),
        art: it.artwork_url ? "/api/artwork" : null,
        playing,
      }));
    });
  } catch { wrap.innerHTML = '<div class="empty">Не удалось загрузить очередь</div>'; }
}
$("#btn-clear-queue").addEventListener("click", async () => { await action(post("/queue/clear"), "Очередь очищена"); loadQueue(); });

// общий рендер строки результата/очереди
function rowEl({ num, title, sub, art, round, playing, actions }) {
  const el = document.createElement("div");
  el.className = "row" + (playing ? " playing" : "");
  const artHtml = art !== undefined
    ? (art ? `<img class="row-art ${round ? "round" : ""}" src="${art}" loading="lazy" alt=""/>` : `<div class="row-art ${round ? "round" : ""}"></div>`)
    : `<div class="row-num">${num || ""}</div>`;
  el.innerHTML = `
    ${artHtml}
    <div class="row-meta">
      <div class="row-title"></div>
      <div class="row-sub"></div>
    </div>
    <div class="row-actions"></div>`;
  el.querySelector(".row-title").textContent = title;
  el.querySelector(".row-sub").textContent = sub || "";
  if (actions) {
    const a = el.querySelector(".row-actions");
    actions.forEach((act) => {
      const b = document.createElement("button");
      b.className = "row-btn" + (act.primary ? " play" : "");
      b.textContent = act.icon; b.title = act.title || "";
      b.addEventListener("click", (e) => { e.stopPropagation(); act.fn(); });
      a.appendChild(b);
    });
  }
  return el;
}

// ───────────────────────── поиск (Spotify каталог) ─────────────────────────
let searchTimer, searchType = "track", lastQuery = "";
$("#search-input").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  lastQuery = q;
  $("#search-clear").classList.toggle("hidden", !q);
  searchTimer = setTimeout(() => doSearch(q), 280);
});
$("#search-clear").addEventListener("click", () => {
  $("#search-input").value = ""; lastQuery = ""; $("#search-clear").classList.add("hidden");
  $("#search-results").innerHTML = ""; $("#search-input").focus();
});
$$("#search-seg .seg-item").forEach((b) => b.addEventListener("click", () => {
  $$("#search-seg .seg-item").forEach((x) => x.classList.remove("active"));
  b.classList.add("active"); searchType = b.dataset.type;
  if (lastQuery) doSearch(lastQuery);
}));

function playUri(uri, label) { return action(post("/spotify/play", { uri }), label ? "▶ " + label : "Играет"); }
function queueUri(uri, label) { return action(post("/spotify/queue", { uri }), "В очередь: " + (label || "")); }

async function doSearch(q) {
  const wrap = $("#search-results");
  if (!q) { wrap.innerHTML = ""; return; }
  wrap.innerHTML = '<div class="empty"><span class="spin"></span></div>';
  if (!spotify.authorized) return localSearch(q);
  try {
    // limit=10: у Spotify-приложения в dev-режиме поиск капается на 10 (≥12 → "Invalid limit")
    const r = await (await api(`/spotify/search?q=${encodeURIComponent(q)}&type=${searchType}&limit=10`)).json();
    if (q !== lastQuery) return;
    wrap.innerHTML = "";
    const key = searchType + "s";
    const items = ((r[key] && r[key].items) || []).filter(Boolean);
    if (!items.length) { wrap.innerHTML = '<div class="empty">Ничего не найдено</div>'; return; }
    items.forEach((it) => wrap.appendChild(searchRow(it)));
  } catch { wrap.innerHTML = '<div class="empty">Ошибка поиска</div>'; }
}

function imgOf(it) {
  const imgs = it.images || (it.album && it.album.images) || [];
  return imgs.length ? imgs[imgs.length > 2 ? 1 : 0].url : null;
}
function searchRow(it) {
  const round = searchType === "artist";
  let title = it.name, sub = "";
  if (searchType === "track") sub = (it.artists || []).map((a) => a.name).join(", ") + (it.album ? " · " + it.album.name : "");
  else if (searchType === "album") sub = "Альбом · " + (it.artists || []).map((a) => a.name).join(", ");
  else if (searchType === "artist") sub = "Артист";
  else if (searchType === "playlist") sub = "Плейлист · " + ((it.owner && it.owner.display_name) || "");
  const row = rowEl({
    title, sub, art: imgOf(it), round,
    actions: [
      { icon: "▶", primary: true, title: "Играть на зоне", fn: () => playUri(it.uri, it.name) },
      ...(searchType === "track" ? [{ icon: "＋", title: "В очередь", fn: () => queueUri(it.uri, it.name) }] : []),
    ],
  });
  row.addEventListener("click", () => playUri(it.uri, it.name));
  return row;
}
async function localSearch(q) {
  const wrap = $("#search-results");
  try {
    const r = await (await api("/search?q=" + encodeURIComponent(q))).json();
    if (q !== lastQuery) return;
    wrap.innerHTML = "";
    const tracks = (r.tracks && r.tracks.items) || [];
    if (!tracks.length) { wrap.innerHTML = '<div class="empty">Ничего не найдено в локальной библиотеке</div>'; return; }
    tracks.forEach((t, i) => wrap.appendChild(rowEl({ num: i + 1, title: t.title || "—", sub: [t.artist, t.album].filter(Boolean).join(" · ") })));
  } catch { wrap.innerHTML = '<div class="empty">Ошибка поиска</div>'; }
}

// ───────────────────────── настройки ─────────────────────────
async function loadSettings() {
  try {
    const d = await (await api("/device-name")).json();
    $("#device-name-input").value = d.name || "";
  } catch {}
  $("#set-conn").textContent = connOk ? "в сети" : "нет связи";
  const n = state ? state.speakers.filter((s) => s.selected).length : 0;
  $("#set-zone").textContent = String(n);
  await loadSpotifyStatus();
}
async function loadSpotifyStatus() {
  try {
    spotify = await (await api("/spotify/status")).json();
  } catch { spotify = { configured: false, authorized: false }; }
  const badge = $("#sp-status");
  if (spotify.authorized) { badge.textContent = "подключён"; badge.className = "badge ok"; }
  else if (spotify.configured) { badge.textContent = "не авторизован"; badge.className = "badge warn"; }
  else { badge.textContent = "не настроен"; badge.className = "badge"; }
  $("#sp-login").classList.toggle("hidden", !spotify.configured || spotify.authorized);
  $("#sp-playhere").classList.toggle("hidden", !spotify.authorized);
  $("#search-hint").textContent = spotify.authorized
    ? "Полный каталог Spotify. Нажми на результат — заиграет на всей группе."
    : "Войди в Spotify (Настройки) для поиска по каталогу. Сейчас — локальная библиотека.";
}
$("#sp-login").addEventListener("click", () => window.open("/api/spotify/login", "_blank"));
$("#sp-playhere").addEventListener("click", () => action(post("/spotify/play-here"), "Переношу на spotineck"));
$("#h-playhere").addEventListener("click", () => action(post("/spotify/play-here"), "Переношу на spotineck"));

async function saveDeviceName() {
  const btn = $("#device-name-save");
  const name = $("#device-name-input").value.trim();
  if (!name) return;
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await post("/device-name", { name });
    const d = await r.json().catch(() => ({}));
    if (r.ok) { $("#device-name-hint").textContent = `Готово — устройство теперь «${d.name}». Spotify Connect перезапущен (~3 сек).`; toast("Имя сохранено"); }
    else { $("#device-name-hint").textContent = `Ошибка: ${d.error || r.status}`; toast("Ошибка сохранения", "err"); }
  } catch { $("#device-name-hint").textContent = "Ошибка сети"; toast("Ошибка сети", "err"); }
  btn.disabled = false; btn.textContent = "Сохранить";
}
$("#device-name-save").addEventListener("click", saveDeviceName);
$("#device-name-input").addEventListener("keydown", (e) => { if (e.key === "Enter") saveDeviceName(); });

// ───────────────────────── now playing render ─────────────────────────
function fmt(ms) {
  ms = Math.max(0, ms || 0);
  const s = Math.floor(ms / 1000);
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}
let curArt = "";
function renderNow() {
  const p = state.playback, t = p.track;
  const playing = p.state === "play";
  const title = t.title || (p.state === "stop" ? "Ничего не играет" : "spotineck");
  const artist = t.artist || "";

  // нижний бар
  $("#np-title").textContent = title;
  $("#np-artist").textContent = artist;
  $("#c-play").textContent = playing ? "⏸" : "▶";
  $("#c-shuffle").classList.toggle("active", p.shuffle);
  $("#c-repeat").classList.toggle("active", p.repeat !== "off");
  $("#c-repeat").textContent = p.repeat === "single" ? "↻¹" : "↻";
  $("#t-len").textContent = fmt(p.length_ms);

  // hero
  $("#hero-title").textContent = title;
  $("#hero-artist").textContent = artist;
  $("#hero-album").textContent = t.album || "";
  $("#hero-source").textContent = artist ? "Сейчас играет" : "spotineck";
  $("#h-play").textContent = playing ? "⏸" : "▶";
  $("#h-shuffle").classList.toggle("active", p.shuffle);
  $("#h-repeat").classList.toggle("active", p.repeat !== "off");
  $("#h-repeat").textContent = p.repeat === "single" ? "↻¹" : "↻";
  $("#h-len").textContent = fmt(p.length_ms);
  $("#h-playhere").classList.toggle("hidden", !(spotify.authorized && p.state !== "play"));

  // обложка + амбиент
  const art = t.artwork_url ? (t.artwork_url.startsWith("/") ? t.artwork_url + "?t=" + Date.now() : t.artwork_url) : "";
  if (art !== curArt) {
    curArt = art;
    [["#np-art", true], ["#hero-art", false]].forEach(([sel, isBar]) => {
      const im = $(sel);
      if (art) { im.src = art; im.style.visibility = "visible"; } else im.style.visibility = "hidden";
    });
    $("#hero-art-ph").style.display = art ? "none" : "flex";
    const amb = $("#ambient");
    if (art) { amb.style.backgroundImage = `url("${art}")`; amb.classList.add("on"); }
    else amb.classList.remove("on");
  }

  // громкость
  if (!volDrag) { $("#vol-fill").style.width = p.volume + "%"; $("#vol-bar .bar-knob").style.left = p.volume + "%"; }
  $("#vol-mute").textContent = p.volume === 0 ? "🔇" : "🔊";
  if (document.activeElement !== mvol) {
    mvol.value = p.volume; $("#master-vol-val").textContent = p.volume; mvol.style.setProperty("--p", p.volume + "%");
  }
}

function tickProgress() {
  if (!state || seekDrag) return;
  const p = state.playback;
  let prog = p.progress_ms;
  if (p.state === "play") prog += performance.now() - lastStateAt;
  const ratio = p.length_ms > 0 ? Math.min(1, prog / p.length_ms) : 0;
  $("#seek-fill").style.width = ratio * 100 + "%";
  $("#seek-bar .bar-knob").style.left = ratio * 100 + "%";
  $("#hero-fill").style.width = ratio * 100 + "%";
  $("#hero-seek .bar-knob").style.left = ratio * 100 + "%";
  const cur = p.length_ms > 0 ? fmt(prog) : "0:00";
  $("#t-cur").textContent = cur; $("#h-cur").textContent = cur;
}
setInterval(tickProgress, 250);

// ───────────────────────── приём состояния ─────────────────────────
function applyState(s) {
  const first = !state;
  state = s;
  lastStateAt = performance.now();
  renderSpeakers();
  renderZoneChips();
  renderNow();
  if (curView === "queue") loadQueue();
}

// ───────────────────────── WebSocket ─────────────────────────
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
function setConn(ok) {
  connOk = ok;
  $("#conn-dot").classList.toggle("ok", ok);
  $("#conn-text").textContent = ok ? "в сети" : "переподключение…";
  const sc = $("#set-conn"); if (sc) sc.textContent = ok ? "в сети" : "нет связи";
}

// ───────────────────────── клавиатура ─────────────────────────
document.addEventListener("keydown", (e) => {
  if (/INPUT|TEXTAREA/.test(document.activeElement.tagName)) return;
  const p = state && state.playback;
  switch (e.key) {
    case " ": e.preventDefault(); post("/playback/toggle"); break;
    case "ArrowRight": if (e.shiftKey) post("/playback/next"); break;
    case "ArrowLeft": if (e.shiftKey) post("/playback/previous"); break;
    case "ArrowUp": e.preventDefault(); if (p) setVolume(p.volume + 5); break;
    case "ArrowDown": e.preventDefault(); if (p) setVolume(p.volume - 5); break;
    case "s": case "ы": post("/playback/shuffle", { enabled: !(p && p.shuffle) }); break;
    case "/": e.preventDefault(); switchView("search"); break;
  }
});

// ───────────────────────── PWA service worker ─────────────────────────
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

// ───────────────────────── старт ─────────────────────────
loadSpotifyStatus();
api("/state").then((r) => r.json()).then(applyState).catch(() => {});
connectWS();
if (location.search.includes("spotify=ok")) { toast("Spotify подключён"); history.replaceState(null, "", "/"); }
