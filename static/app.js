(() => {
const $ = (id) => document.getElementById(id);
const UID = +document.body.dataset.uid;
const COLORS = ["#0f7b8a", "#0ea5e9", "#8b5cf6", "#f59e0b", "#ef4444", "#10b981", "#ec4899", "#6366f1", "#f97316"];
const S = { me: null, contacts: [], tab: "chats", chat: null, msgs: [], sel: new Set(), edit: null, q: "" };
const IC = {
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  paste: '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/>',
  edit: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  del: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  sel: '<path d="M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
  cut: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/>',
};
const svg = (p) => `<svg class="ic" viewBox="0 0 24 24">${p}</svg>`;

function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  kids.flat().forEach((c) => c != null && e.append(c.nodeType ? c : document.createTextNode(c)));
  return e;
}
async function api(url, body, method) {
  const opt = { method: method || (body ? "POST" : "GET"), headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "Ошибка запроса");
  return j;
}
let tt;
function toast(t) { const e = $("toast"); e.textContent = t; e.classList.remove("hid"); clearTimeout(tt); tt = setTimeout(() => e.classList.add("hid"), 3200); }
const ini = (n) => (String(n || "U").trim().split(/\s+/).slice(0, 2).map((x) => x[0]).join("") || "U").toUpperCase();
function avatar(el, name, color, online) {
  el.textContent = ini(name); el.style.background = color || COLORS[0];
  if (online) el.append(h("i", { class: "dot" }));
}
const pd = (s) => new Date(String(s).includes("T") ? s : String(s).replace(" ", "T") + "Z");
const hm = (s) => s ? pd(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
function dayLabel(s) {
  const d = pd(s), n = new Date(), y = new Date(Date.now() - 864e5);
  if (d.toDateString() === n.toDateString()) return "Сегодня";
  if (d.toDateString() === y.toDateString()) return "Вчера";
  return d.toLocaleDateString([], { day: "numeric", month: "long" });
}
function listTime(s) {
  if (!s) return "";
  const d = pd(s);
  return d.toDateString() === new Date().toDateString() ? hm(s) : d.toLocaleDateString([], { day: "2-digit", month: "2-digit" });
}

/* ---------- socket ---------- */
const sock = io();
sock.on("contacts_updated", (d) => { S.contacts = d.contacts; if (S.tab === "chats") renderList(); refreshHead(); });
sock.on("message_received", (d) => {
  if (S.chat && d.conversation_id === S.chat.id) {
    if (!S.msgs.some((m) => m.id === d.message.id)) { S.msgs.push(d.message); renderMsgs(true); }
    if (d.message.sender_id !== UID) api("/api/chat/" + S.chat.id).catch(() => {});
  }
});
sock.on("message_updated", (d) => { const i = S.msgs.findIndex((m) => m.id === d.message.id); if (i > -1) { S.msgs[i] = d.message; renderMsgs(); } });
sock.on("message_deleted", (d) => { S.msgs = S.msgs.filter((m) => m.id !== d.message_id); S.sel.delete(d.message_id); renderMsgs(); updSel(); });
sock.on("call_signal", onSignal);

/* ---------- list ---------- */
async function renderList() {
  const box = $("list"), q = S.q.trim().toLowerCase();
  box.replaceChildren();
  if (S.tab === "chats") {
    const items = S.contacts.filter((c) => !q || (c.display_name || "").toLowerCase().includes(q));
    if (!items.length) return box.append(h("div", { class: "empty" }, "Чатов пока нет. Найдите человека во вкладке «Люди»."));
    items.forEach((c) => {
      const av = h("span", { class: "av" }); avatar(av, c.display_name, c.avatar_color, c.status === "online");
      box.append(h("button", { class: "row" + (S.chat && S.chat.id === c.conversation_id ? " on" : ""), onclick: () => openChat(c.conversation_id) }, av,
        h("div", { class: "tx" }, h("div", { class: "t1" }, h("span", {}, c.display_name), h("time", {}, listTime(c.last_message_time))),
          h("div", { class: "t2" }, h("span", {}, c.last_message || ""), c.unread_count ? h("i", { class: "bd" }, c.unread_count) : null))));
    });
  } else if (S.tab === "people") {
    let users = [];
    try { users = (await api("/api/users?q=" + encodeURIComponent(S.q.trim()))).users; } catch (e) { return; }
    if (S.tab !== "people") return;
    box.replaceChildren();
    if (!users.length) return box.append(h("div", { class: "empty" }, "Никого не нашли. Попробуйте имя, логин или телефон."));
    users.forEach((u) => {
      const av = h("span", { class: "av" }); avatar(av, u.display_name, u.avatar_color, u.online);
      box.append(h("button", { class: "row", onclick: () => startWith(u.id) }, av,
        h("div", { class: "tx" }, h("div", { class: "t1" }, h("span", {}, u.display_name)), h("div", { class: "t2" }, h("span", {}, "@" + u.username + (u.bio ? " · " + u.bio : ""))))));
    });
  } else {
    let calls = [];
    try { calls = (await api("/api/calls")).calls; } catch (e) { return; }
    if (S.tab !== "calls") return;
    box.replaceChildren();
    if (!calls.length) return box.append(h("div", { class: "empty" }, "Звонков ещё не было."));
    calls.forEach((c) => {
      const av = h("span", { class: "av" }); avatar(av, c.peer_name, COLORS[c.peer_id % COLORS.length]);
      box.append(h("button", { class: "row", onclick: () => startWith(c.peer_id) }, av,
        h("div", { class: "tx" }, h("div", { class: "t1" }, h("span", {}, c.peer_name), h("time", {}, listTime(c.created_at))),
          h("div", { class: "t2" }, h("span", {}, c.call_type === "video" ? "Видеозвонок" : "Аудиозвонок")))));
    });
  }
}
async function startWith(uid) {
  try { const r = await api("/api/chat/start", { user_id: uid }); S.tab = "chats"; setTabs(); await openChat(r.conversation_id); } catch (e) { toast(e.message); }
}
function setTabs() { document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === S.tab)); renderList(); }
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => { S.tab = b.dataset.tab; setTabs(); }));
let qt; $("q").addEventListener("input", (e) => { S.q = e.target.value; clearTimeout(qt); qt = setTimeout(renderList, 200); });

/* ---------- chat ---------- */
async function openChat(id) {
  try {
    const d = await api("/api/chat/" + id);
    S.chat = { id, ...d.conversation }; S.msgs = d.messages; S.sel.clear(); cancelEdit(); updSel();
    $("ph").classList.add("hid"); $("chat").classList.remove("hid"); $("app").classList.add("open");
    refreshHead(); renderMsgs(true); renderList();
    if (matchMedia("(min-width:761px)").matches) $("inp").focus();
  } catch (e) { toast(e.message); }
}
function refreshHead() {
  if (!S.chat) return;
  const c = S.contacts.find((x) => x.conversation_id === S.chat.id);
  const st = S.chat.type === "group" ? (S.chat.member_count || 0) + " участн." : (c ? c.status : S.chat.status) === "online" ? "в сети" : "не в сети";
  avatar($("chAv"), S.chat.display_name, c ? c.avatar_color : null);
  $("chName").textContent = S.chat.display_name; $("chSt").textContent = st; $("chSt").className = st === "в сети" ? "on" : "";
  const grp = S.chat.type === "group"; $("audioBtn").classList.toggle("hid", grp); $("videoBtn").classList.toggle("hid", grp);
}
function renderMsgs(scroll) {
  const box = $("msgs"), atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  box.replaceChildren(); box.classList.toggle("selmode", S.sel.size > 0);
  let last = "";
  S.msgs.forEach((m) => {
    const dl = dayLabel(m.created_at);
    if (dl !== last) { box.append(h("div", { class: "day" }, dl)); last = dl; }
    const mine = m.sender_id === UID, body = [];
    if (S.chat.type === "group" && !mine) body.push(h("div", { class: "nm", style: "color:" + (m.sender_color || "var(--ac)") }, m.sender_name));
    if (m.attachment_url) {
      const t = m.attachment_type || "";
      if (t.startsWith("image/")) body.push(h("img", { src: m.attachment_url, alt: m.attachment_name || "", loading: "lazy" }));
      else if (t.startsWith("video/")) body.push(h("video", { src: m.attachment_url, controls: "", preload: "metadata", playsinline: "" }));
      else if (t.startsWith("audio/")) body.push(h("audio", { src: m.attachment_url, controls: "" }));
      else body.push(h("a", { class: "file", href: m.attachment_url, target: "_blank", download: m.attachment_name || "" }, "📎 " + (m.attachment_name || "Файл")));
    }
    if (m.text) body.push(h("div", { class: "tx" }, m.text));
    body.push(h("div", { class: "mt" }, (m.edited_at ? "изменено · " : "") + hm(m.created_at)));
    box.append(h("div", { class: "msg" + (mine ? " mine" : "") + (S.sel.has(m.id) ? " sel" : ""), "data-id": m.id }, body));
  });
  if (scroll || atEnd) box.scrollTop = box.scrollHeight;
}
async function send() {
  const inp = $("inp"), text = inp.value.trim();
  if (!S.chat) return;
  try {
    if (S.edit) {
      if (!text) return;
      await api(`/api/messages/${S.edit}/edit`, { text }); cancelEdit();
    } else {
      if (!text) return;
      inp.value = ""; autosize();
      const r = await api("/api/send", { conversation_id: S.chat.id, text });
      if (!S.msgs.some((m) => m.id === r.message.id)) { S.msgs.push(r.message); renderMsgs(true); }
    }
  } catch (e) { toast(e.message); }
}
async function sendFile(f) {
  if (!f || !S.chat) return;
  const fd = new FormData(); fd.append("conversation_id", S.chat.id); fd.append("media", f); fd.append("text", "");
  try { const r = await api("/api/send", fd); if (!S.msgs.some((m) => m.id === r.message.id)) { S.msgs.push(r.message); renderMsgs(true); } } catch (e) { toast(e.message); }
}
function autosize() { const i = $("inp"); i.style.height = "auto"; i.style.height = Math.min(i.scrollHeight, 140) + "px"; }
$("inp").addEventListener("input", autosize);
$("inp").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && matchMedia("(min-width:761px)").matches) { e.preventDefault(); send(); }
  if (e.key === "Escape") cancelEdit();
});
$("sendBtn").addEventListener("click", send);
$("attachBtn").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", (e) => { sendFile(e.target.files[0]); e.target.value = ""; });
$("backBtn").addEventListener("click", () => { $("app").classList.remove("open"); S.chat = null; renderList(); });
function startEdit(m) { S.edit = m.id; $("inp").value = m.text; $("editTx").textContent = "Изменить: " + m.text; $("editBar").classList.remove("hid"); autosize(); $("inp").focus(); }
function cancelEdit() { if (S.edit) $("inp").value = ""; S.edit = null; $("editBar").classList.add("hid"); autosize(); }
$("editX").addEventListener("click", cancelEdit);

/* ---------- selection ---------- */
function toggleSel(id) { S.sel.has(id) ? S.sel.delete(id) : S.sel.add(id); updSel(); renderMsgs(); }
function updSel() { const on = S.sel.size > 0; $("selHead").classList.toggle("hid", !on); $("chHead").classList.toggle("hid", on); $("selCount").textContent = "Выбрано: " + S.sel.size; $("msgs").classList.toggle("selmode", on); }
$("selCancel").addEventListener("click", () => { S.sel.clear(); updSel(); renderMsgs(); });
const selMsgs = () => S.msgs.filter((m) => S.sel.has(m.id));
$("selCopy").addEventListener("click", () => { copyText(selMsgs().map((m) => m.text).filter(Boolean).join("\n")); S.sel.clear(); updSel(); renderMsgs(); });
$("selDel").addEventListener("click", () => delMsgs(selMsgs()));
async function delMsgs(list) {
  const own = list.filter((m) => m.sender_id === UID);
  if (!own.length) return toast("Можно удалять только свои сообщения");
  if (!confirm(own.length > 1 ? `Удалить сообщения (${own.length})?` : "Удалить сообщение?")) return;
  for (const m of own) { try { await api(`/api/messages/${m.id}/delete`, {}); } catch (e) { toast(e.message); } }
  S.sel.clear(); updSel();
}

/* ---------- clipboard + context menu ---------- */
async function copyText(t) {
  if (!t) return;
  try { await navigator.clipboard.writeText(t); } catch (e) {
    const a = h("textarea", { style: "position:fixed;opacity:0" }); a.value = t; document.body.append(a); a.select(); try { document.execCommand("copy"); } catch (_) {} a.remove();
  }
  toast("Скопировано");
}
async function pasteInto(inp) {
  try {
    const t = await navigator.clipboard.readText(); inp.focus();
    inp.setRangeText(t, inp.selectionStart, inp.selectionEnd, "end"); inp.dispatchEvent(new Event("input"));
  } catch (e) { inp.focus(); toast("Браузер не дал доступ к буферу. Нажмите Ctrl+V"); }
}
function showMenu(x, y, items) {
  const cm = $("cm"); cm.replaceChildren();
  items.forEach((it) => cm.append(it === "-" ? h("hr") : (() => { const b = h("button", { class: it.danger ? "dg" : "" }); b.innerHTML = svg(IC[it.icon]) + "<span></span>"; b.lastChild.textContent = it.label; b.addEventListener("click", () => { hideMenu(); it.run(); }); return b; })()));
  cm.classList.remove("hid"); cm.style.left = cm.style.top = "0px";
  const r = cm.getBoundingClientRect();
  cm.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + "px"; cm.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + "px";
}
const hideMenu = () => $("cm").classList.add("hid");
document.addEventListener("pointerdown", (e) => { if (!$("cm").contains(e.target)) hideMenu(); });
document.addEventListener("scroll", hideMenu, true); addEventListener("resize", hideMenu);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideMenu(); });

function msgMenu(el, x, y) {
  const m = S.msgs.find((v) => v.id === +el.dataset.id); if (!m) return;
  const own = m.sender_id === UID, selTxt = String(getSelection()).trim();
  const items = [{ icon: "copy", label: selTxt ? "Копировать выделенное" : "Копировать", run: () => copyText(selTxt || m.text || m.attachment_url || "") },
    { icon: "paste", label: "Вставить в сообщение", run: () => pasteInto($("inp")) },
    { icon: "sel", label: S.sel.has(m.id) ? "Снять выделение" : "Выделить", run: () => toggleSel(m.id) }];
  if (own) {
    items.push("-");
    if (m.message_type === "text" && m.text) items.push({ icon: "edit", label: "Редактировать", run: () => startEdit(m) });
    items.push({ icon: "del", label: "Удалить", danger: true, run: () => delMsgs([m]) });
  }
  showMenu(x, y, items);
}
function inputMenu(inp, x, y) {
  const has = inp.selectionStart !== inp.selectionEnd, val = () => inp.value.slice(inp.selectionStart, inp.selectionEnd);
  const items = [];
  if (has) items.push({ icon: "cut", label: "Вырезать", run: () => { copyText(val()); inp.setRangeText("", inp.selectionStart, inp.selectionEnd, "end"); inp.dispatchEvent(new Event("input")); } },
    { icon: "copy", label: "Копировать", run: () => copyText(val()) });
  items.push({ icon: "paste", label: "Вставить", run: () => pasteInto(inp) }, { icon: "sel", label: "Выделить всё", run: () => { inp.focus(); inp.select(); } });
  showMenu(x, y, items);
}
let lp = null, lpFired = false;
$("msgs").addEventListener("contextmenu", (e) => {
  e.preventDefault(); if (lpFired) { lpFired = false; return; }
  const el = e.target.closest(".msg");
  el ? msgMenu(el, e.clientX, e.clientY) : showMenu(e.clientX, e.clientY, [{ icon: "paste", label: "Вставить в сообщение", run: () => pasteInto($("inp")) }]);
});
$("inp").addEventListener("contextmenu", (e) => { e.preventDefault(); inputMenu($("inp"), e.clientX, e.clientY); });
$("msgs").addEventListener("touchstart", (e) => {
  const el = e.target.closest(".msg"); if (!el || S.sel.size) return;
  const t = e.touches[0]; clearTimeout(lp);
  lp = setTimeout(() => { lpFired = true; navigator.vibrate && navigator.vibrate(15); msgMenu(el, t.clientX, t.clientY); }, 480);
}, { passive: true });
["touchmove", "touchend", "touchcancel"].forEach((n) => $("msgs").addEventListener(n, () => clearTimeout(lp), { passive: true }));
$("msgs").addEventListener("click", (e) => {
  const el = e.target.closest(".msg"); if (el && S.sel.size) { e.preventDefault(); toggleSel(+el.dataset.id); }
});

/* ---------- profile ---------- */
let pColor = COLORS[0];
function fillProfile() {
  const p = S.me; if (!p) return;
  avatar($("meAv"), p.display_name, p.avatar_color); $("meName").textContent = p.display_name; $("meBio").textContent = p.bio || "";
}
$("meBtn").addEventListener("click", () => {
  const p = S.me; if (!p) return;
  $("pName").value = p.display_name || ""; $("pBio").value = p.bio || ""; $("pPhone").value = p.phone_number || "";
  $("pCur").value = $("pNew").value = ""; $("pErr").textContent = ""; pColor = p.avatar_color || COLORS[0];
  const draw = () => { avatar($("pAv"), $("pName").value || p.username, pColor); $("pSw").replaceChildren(...COLORS.map((c) => h("button", { class: c === pColor ? "on" : "", style: "background:" + c, "aria-label": c, onclick: () => { pColor = c; draw(); } }))); };
  draw(); $("pName").oninput = draw; $("profOv").classList.remove("hid"); $("pName").focus();
});
$("pCancel").addEventListener("click", () => $("profOv").classList.add("hid"));
$("profOv").addEventListener("pointerdown", (e) => { if (e.target === $("profOv")) $("profOv").classList.add("hid"); });
$("pSave").addEventListener("click", async () => {
  try {
    const r = await api("/api/profile", { display_name: $("pName").value, bio: $("pBio").value, phone_number: $("pPhone").value, avatar_color: pColor, current_password: $("pCur").value, new_password: $("pNew").value });
    S.me = r.profile; fillProfile(); $("profOv").classList.add("hid"); toast("Профиль сохранён");
  } catch (e) { $("pErr").textContent = e.message; }
});
$("logoutBtn").addEventListener("click", () => { if (confirm("Выйти из аккаунта?")) location.href = "/logout"; });

/* ---------- calls (WebRTC) ---------- */
let C = null, ringT = null, tick = null;
const ICE = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }, { urls: "stun:stun1.l.google.com:19302" }] };
const sig = (to, kind, extra = {}) => sock.emit("call_signal", { to, kind, ...extra });
function callUI(o) {
  $("call").classList.remove("hid");
  $("cName").textContent = o.name; $("cSt").textContent = o.status; avatar($("cAv"), o.name, o.color);
  $("cAcc").classList.toggle("hid", !o.incoming); $("cMic").classList.toggle("hid", !o.live); $("cCam").classList.toggle("hid", !(o.live && C && C.type === "video"));
}
async function media(type) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error("Для звонков нужен HTTPS или localhost. Откройте сайт по https://");
  try { return await navigator.mediaDevices.getUserMedia({ audio: true, video: type === "video" ? { facingMode: "user" } : false }); }
  catch (e) { throw new Error(e.name === "NotAllowedError" ? "Разрешите доступ к микрофону" + (type === "video" ? " и камере" : "") + " в браузере" : e.name === "NotFoundError" ? "Микрофон или камера не найдены" : "Не удалось включить устройство: " + e.message); }
}
function makePC(peer) {
  const pc = new RTCPeerConnection(ICE);
  pc.onicecandidate = (e) => e.candidate && sig(peer, "ice", { payload: e.candidate });
  pc.ontrack = (e) => { $("rv").srcObject = e.streams[0]; $("rv").play().catch(() => {}); if (C && C.type === "video") $("rv").classList.remove("hid"); };
  pc.onconnectionstatechange = () => {
    if (!C) return;
    if (pc.connectionState === "connected") live();
    if (["failed", "closed"].includes(pc.connectionState)) endCall(false, "Соединение потеряно");
  };
  return pc;
}
function live() {
  if (!C || C.live) return; C.live = true; clearTimeout(ringT); const t0 = Date.now();
  callUI({ name: C.name, color: C.color, status: "00:00", live: true });
  tick = setInterval(() => { const s = Math.floor((Date.now() - t0) / 1000); $("cSt").textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }, 1000);
}
async function startCall(type) {
  if (C) return toast("Звонок уже идёт");
  const peer = S.contacts.find((x) => x.conversation_id === (S.chat && S.chat.id));
  if (!peer || !peer.peer_id) return toast("Звонить можно только в личном чате");
  C = { peer: peer.peer_id, name: peer.display_name, color: peer.avatar_color, type, pending: [] };
  try {
    C.stream = await media(type); const pc = C.pc = makePC(C.peer);
    C.stream.getTracks().forEach((t) => pc.addTrack(t, C.stream));
    $("lv").srcObject = C.stream; $("lv").classList.toggle("hid", type !== "video"); $("rv").classList.add("hid");
    callUI({ name: C.name, color: C.color, status: "Вызываем…" });
    const off = await pc.createOffer(); await pc.setLocalDescription(off);
    sig(C.peer, "offer", { call_type: type, payload: pc.localDescription });
    api("/api/call/" + type, { recipient_id: C.peer, conversation_id: S.chat.id }).catch(() => {});
    ringT = setTimeout(() => endCall(true, "Нет ответа"), 45000);
  } catch (e) { cleanup(); toast(e.message); }
}
async function acceptCall() {
  if (!C || !C.offer) return;
  try {
    C.stream = await media(C.type); const pc = C.pc = makePC(C.peer);
    C.stream.getTracks().forEach((t) => pc.addTrack(t, C.stream));
    $("lv").srcObject = C.stream; $("lv").classList.toggle("hid", C.type !== "video"); $("rv").classList.add("hid");
    await pc.setRemoteDescription(C.offer); for (const c of C.pending) await pc.addIceCandidate(c).catch(() => {}); C.pending = [];
    const ans = await pc.createAnswer(); await pc.setLocalDescription(ans);
    sig(C.peer, "answer", { payload: pc.localDescription });
    clearTimeout(ringT); callUI({ name: C.name, color: C.color, status: "Соединяем…", live: true });
  } catch (e) { sig(C.peer, "reject"); cleanup(); toast(e.message); }
}
async function onSignal(d) {
  if (d.kind === "offer") {
    if (C) return sig(d.from, "reject");
    C = { peer: d.from, name: d.from_name, color: d.from_color, type: d.call_type === "video" ? "video" : "voice", offer: d.payload, pending: [], incoming: true };
    callUI({ name: C.name, color: C.color, status: C.type === "video" ? "Входящий видеозвонок" : "Входящий аудиозвонок", incoming: true });
    ringT = setTimeout(() => endCall(true), 45000); return;
  }
  if (!C || d.from !== C.peer) return;
  if (d.kind === "answer" && C.pc) { await C.pc.setRemoteDescription(d.payload).catch(() => {}); for (const c of C.pending) await C.pc.addIceCandidate(c).catch(() => {}); C.pending = []; $("cSt").textContent = "Соединяем…"; }
  else if (d.kind === "ice") { if (C.pc && C.pc.remoteDescription) C.pc.addIceCandidate(d.payload).catch(() => {}); else C.pending.push(d.payload); }
  else if (d.kind === "end") endCall(false, "Звонок завершён");
  else if (d.kind === "reject") endCall(false, "Абонент занят или отклонил звонок");
  else if (d.kind === "unavailable") endCall(false, "Абонент не в сети");
}
function cleanup() {
  clearTimeout(ringT); clearInterval(tick);
  if (C) { C.stream && C.stream.getTracks().forEach((t) => t.stop()); C.pc && C.pc.close(); }
  C = null; $("rv").srcObject = $("lv").srcObject = null; $("call").classList.add("hid");
  ["cMic", "cCam"].forEach((i) => $(i).classList.remove("off"));
}
function endCall(notify, msg) { if (!C) return; if (notify) sig(C.peer, "end"); cleanup(); if (msg) toast(msg); }
$("audioBtn").addEventListener("click", () => startCall("voice"));
$("videoBtn").addEventListener("click", () => startCall("video"));
$("cAcc").addEventListener("click", acceptCall);
$("cEnd").addEventListener("click", () => { if (C && C.incoming && !C.pc) sig(C.peer, "reject"), cleanup(); else endCall(true); });
$("cMic").addEventListener("click", (e) => { const t = C.stream.getAudioTracks()[0]; if (t) { t.enabled = !t.enabled; e.currentTarget.classList.toggle("off", !t.enabled); } });
$("cCam").addEventListener("click", (e) => { const t = C.stream.getVideoTracks()[0]; if (t) { t.enabled = !t.enabled; e.currentTarget.classList.toggle("off", !t.enabled); } });

/* ---------- init ---------- */
(async () => {
  try { S.me = (await api("/api/profile")).profile; fillProfile(); S.contacts = (await api("/api/contacts")).contacts; } catch (e) {}
  renderList();
  const want = +document.body.dataset.chat; if (want) openChat(want);
})();
})();
