"use strict";

const state = {
  product: null,
  formats: [],
  stages: [],
  passports: [],
  tree: null,
  names: {},          // id объекта → имя
  chats: [],
  chat: null,         // активный чат с сообщениями
  polling: null,
  search: "",
  open: {},
  passportKind: "",
  formatId: "",
  formatDefaultSkill: "",
  llm: null,
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* ── иконки (контуры в духе Lucide) ──────────────────────────────────── */
const ICONS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  sync: '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16"/><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  send: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>',
  code: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12z"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
};

function icon(name) {
  return `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

function setIcon(node, name) {
  node.dataset.icon = name;
  node.innerHTML = icon(name);
}

function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((node) => {
    if (!node.querySelector("svg")) node.insertAdjacentHTML("afterbegin", icon(node.dataset.icon));
  });
}

/** Кнопка-иконка с подписью: <button><svg/>Текст</button>. */
function iconButton(name, text, cls) {
  const btn = el("button", cls);
  btn.type = "button";
  btn.innerHTML = icon(name);
  if (text) btn.append(document.createTextNode(text));
  return btn;
}

function setIconButton(btn, name, text) {
  btn.innerHTML = icon(name);
  btn.append(document.createTextNode(text));
}

/* ── уведомления ─────────────────────────────────────────────────────── */
function toast(text, kind) {
  const node = el("div", "toast" + (kind ? ` ${kind}` : ""), text);
  $("#toasts").append(node);
  setTimeout(() => node.remove(), kind === "bad" ? 5000 : 2600);
}

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "•";
}

/** «5 мин назад», «вчера», «12 сен» — из строки времени Postgres/ISO. */
function relTime(raw) {
  if (!raw) return "";
  const date = new Date(String(raw).replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return "";
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  if (sameDay) return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "вчера";
  const opts = { day: "numeric", month: "short" };
  if (date.getFullYear() !== today.getFullYear()) opts.year = "numeric";
  return date.toLocaleDateString("ru-RU", opts);
}

const isTouch = () => window.matchMedia("(pointer: coarse)").matches;
const isMobile = () => window.matchMedia("(max-width: 900px)").matches;

const TOKEN_KEY = "studio.token";

function savedToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch (err) {
    return "";
  }
}

function saveToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch (err) {
    /* приватный режим — останемся на cookie */
  }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = savedToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers,
  });
  if (res.status === 401 || res.status === 503) {
    showLogin(res.status === 503 ? "Студия не настроена: нет WEB_AUTH_TOKEN" : "");
    throw new Error("auth");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Ошибка ${res.status}`);
  return data;
}

/* ── вход ────────────────────────────────────────────────────────────── */
let loginRef = null;

function showLogin(message) {
  $("#app").hidden = true;
  $("#login").hidden = false;
  loginError(message);
  loadAdmins();
}

function loginError(message) {
  const err = $("#login-err");
  err.hidden = !message;
  err.textContent = message || "";
}

function loginStep(step) {
  $("#step-admin").hidden = step !== "admin";
  $("#step-code").hidden = step !== "code";
  $("#step-token").hidden = step !== "token";
}

async function loadAdmins() {
  loginStep("admin");
  const box = $("#admin-list");
  box.textContent = "";
  try {
    const res = await fetch("/api/admins", { credentials: "same-origin" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "не удалось получить список");
    $("#btn-token-login").hidden = !data.token_login;
    (data.admins || []).forEach((admin) => {
      const btn = el("button", "admin");
      btn.type = "button";
      btn.append(el("span", "avatar", initials(admin.name)), el("span", null, admin.name));
      btn.insertAdjacentHTML("beforeend", `<span class="arrow">${icon("arrow")}</span>`);
      btn.addEventListener("click", async () => {
        box.querySelectorAll("button").forEach((b) => (b.disabled = true));
        try {
          await requestCode(admin);
        } finally {
          box.querySelectorAll("button").forEach((b) => (b.disabled = false));
        }
      });
      box.append(btn);
    });
    if (!(data.admins || []).length) {
      box.append(el("p", "muted", "Список админов пуст: добавьте их командой /admin_add в боте."));
    }
  } catch (err) {
    loginError(err.message);
  }
}

async function requestCode(admin) {
  loginError("");
  try {
    const res = await fetch("/api/login/request", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: admin.ref }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "не удалось отправить код");
    loginRef = admin.ref;
    $("#code-hint").textContent = `Бот отправил код в Telegram (${admin.name}). Действует 10 минут.`;
    loginStep("code");
    $("#login-code").value = "";
    $("#login-code").focus();
  } catch (err) {
    loginError(err.message);
  }
}

// Шесть цифр набраны — входим сами, без лишнего нажатия.
$("#login-code").addEventListener("input", (e) => {
  const digits = e.target.value.replace(/\D/g, "").slice(0, 6);
  if (digits !== e.target.value) e.target.value = digits;
  if (digits.length === 6) $("#step-code").requestSubmit();
});

let verifying = false;
$("#step-code").addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = $("#login-code").value.trim();
  if (!code || !loginRef || verifying) return;
  loginError("");
  verifying = true;
  try {
    const res = await fetch("/api/login/verify", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: loginRef, code }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "неверный код");
    if (data.token) saveToken(data.token);
    $("#login").hidden = true;
    await boot();
  } catch (err) {
    loginError(err.message);
    $("#login-code").select();
  } finally {
    verifying = false;
  }
});

$("#step-token").addEventListener("submit", async (e) => {
  e.preventDefault();
  const token = $("#login-token").value.trim();
  if (!token) return;
  loginError("");
  try {
    const res = await fetch("/api/login/token", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "неверный код");
    if (data.token) saveToken(data.token);
    $("#login").hidden = true;
    await boot();
  } catch (err) {
    loginError(err.message);
  }
});

$("#btn-token-login").addEventListener("click", () => {
  loginError("");
  loginStep("token");
});
$("#btn-back").addEventListener("click", () => loadAdmins());
$("#btn-back2").addEventListener("click", () => loadAdmins());

$("#btn-logout").addEventListener("click", async () => {
  try {
    await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  } catch (err) {
    /* всё равно уходим на экран входа */
  }
  saveToken("");
  state.chat = null;
  showLogin("");
});

/* ── загрузка ────────────────────────────────────────────────────────── */
async function boot() {
  state.open = loadOpen();
  const data = await api("/api/bootstrap");
  state.product = data.product;
  state.formats = data.formats || [];
  state.stages = data.stages || [];
  state.passports = data.passports || [];
  state.tree = data.tree;
  state.names = collectNames(data.tree);
  state.chats = data.chats || [];
  state.defaults = data.defaults || {};
  $("#app").hidden = false;
  const appName = $("#app-name");
  if (appName) appName.textContent = "Контент завод";
  if (data.product?.name) document.title = `Контент завод · ${data.product.name}`;
  else document.title = "Контент завод";
  try {
    const me = await api("/api/me");
    const box = $("#me");
    box.textContent = "";
    if (me.name) {
      box.append(el("span", "avatar", initials(me.name)), el("span", "name", me.name));
      box.title = me.name;
    }
    box.hidden = !me.name;
  } catch (err) {
    $("#me").hidden = true;
  }
  renderFormats();
  renderStages();
  renderPassports();
  try {
    state.llm = await api("/api/llm-settings");
  } catch (err) {
    state.llm = null;
  }
  renderLlmSummary();
  renderTree();
  renderChats();
  if (state.chats.length) {
    await openChat(state.chats[0].id);
  } else {
    await newChat();
  }
  loadQueue();
}

function collectNames(tree) {
  const out = {};
  const walk = (items) => (items || []).forEach((item) => {
    if (item.id) out[item.id] = item.name;
    walk(item.children);
  });
  (tree?.groups || []).forEach((group) => {
    walk(group.items);
    (group.modules || []).forEach((mod) => walk(mod.lessons));
  });
  return out;
}

/* ── чаты ────────────────────────────────────────────────────────────── */
function renderChats() {
  const box = $("#chat-list");
  box.textContent = "";
  if (!state.chats.length) {
    box.append(el("div", "list-empty", "Пока нет чатов"));
    return;
  }
  state.chats.forEach((chat) => {
    const active = state.chat && chat.id === state.chat.id;
    const node = el("div", "chat-item" + (active ? " active" : ""));
    node.tabIndex = 0;
    node.setAttribute("role", "button");
    if (active) node.setAttribute("aria-current", "true");
    node.append(el("div", "name", chat.title || "Без названия"));
    const fmt = state.formats.find((f) => f.id === chat.format);
    const meta = [fmt ? fmt.title : chat.format];
    const when = relTime(chat.updated_at);
    if (when) meta.push(when);
    if ((chat.objects || []).length) meta.push(`материалов: ${chat.objects.length}`);
    node.append(el("div", "meta", meta.filter(Boolean).join(" · ")));
    const del = iconButton("trash", "", "icon-btn del");
    del.title = "Удалить чат";
    del.setAttribute("aria-label", "Удалить чат");
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteChat(chat);
    });
    node.append(del);
    const open = () => {
      showPane("pane-chat");
      if (!active) openChat(chat.id);
    };
    node.addEventListener("click", open);
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });
    box.append(node);
  });
}

async function deleteChat(chat) {
  if (!confirm(`Удалить чат «${chat.title || "Без названия"}»? Это нельзя отменить.`)) return;
  try {
    await api(`/api/chats/${chat.id}`, { method: "DELETE" });
  } catch (err) {
    toast(`Не удалилось: ${err.message}`, "bad");
    return;
  }
  const wasActive = state.chat && state.chat.id === chat.id;
  await refreshChats();
  toast("Чат удалён");
  if (!wasActive) return;
  if (state.chats.length) await openChat(state.chats[0].id);
  else await newChat();
}

async function newChat() {
  const current = state.chat;
  const payload = {
    format: current?.format || "stories",
    stage: current?.stage || state.defaults?.stage,
    focus: current?.focus ?? state.defaults?.focus,
    objects: [],
  };
  const data = await api("/api/chats", { method: "POST", body: JSON.stringify(payload) });
  state.chat = data.chat;
  state.chat.messages = state.chat.messages || [];
  await refreshChats();
  renderAll();
}

async function refreshChats() {
  const data = await api("/api/chats");
  state.chats = data.chats || [];
  renderChats();
}

async function openChat(id) {
  const data = await api(`/api/chats/${id}`);
  state.chat = data.chat;
  await dropMissingObjects();
  renderAll();
}

/** Объект могли удалить с Диска — убираем его из контекста чата. */
async function dropMissingObjects() {
  const ids = state.chat?.objects || [];
  if (!ids.length || !Object.keys(state.names || {}).length) return;
  const alive = ids.filter((id) => state.names[id]);
  if (alive.length === ids.length) return;
  state.chat.objects = alive;
  try {
    await api(`/api/chats/${state.chat.id}`, {
      method: "PATCH",
      body: JSON.stringify({ objects: alive }),
    });
  } catch (err) {
    /* не критично: конвейер такие объекты и так пропускает */
  }
}

function renderAll() {
  renderChats();
  renderFormats();
  renderStages();
  renderTree();
  renderMessages();
  renderContext();
  $("#chat-title").textContent = state.chat?.title || "Новый чат";
  $("#focus").value = state.chat?.focus || "";
  autoGrow();
}

/* ── переименование чата: клик по заголовку ──────────────────────────── */
$("#chat-title").addEventListener("click", () => {
  if (!state.chat) return;
  const input = $("#chat-title-input");
  input.value = state.chat.title || "";
  $("#chat-title").hidden = true;
  input.hidden = false;
  input.focus();
  input.select();
});

async function finishRename(save) {
  const input = $("#chat-title-input");
  if (input.hidden) return;
  input.hidden = true;
  $("#chat-title").hidden = false;
  const title = input.value.trim();
  if (!save || !title || title === state.chat?.title) return;
  $("#chat-title").textContent = title;
  try {
    await patchChat({ title });
  } catch (err) {
    toast(`Не переименовалось: ${err.message}`, "bad");
    $("#chat-title").textContent = state.chat?.title || "Новый чат";
  }
}

$("#chat-title-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    finishRename(true);
  } else if (e.key === "Escape") {
    finishRename(false);
  }
});
$("#chat-title-input").addEventListener("blur", () => finishRename(true));

async function patchChat(fields) {
  if (!state.chat) return;
  const data = await api(`/api/chats/${state.chat.id}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
  const messages = state.chat.messages || [];
  state.chat = data.chat;
  state.chat.messages = messages;
  renderChats();
  renderContext();
}

/* ── markdown в ответах ──────────────────────────────────────────────── */
function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Сначала экранируем всё, потом размечаем — так в разметку не попадёт чужой HTML.
function inlineMd(text) {
  return text
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, "$1<em>$2</em>")
    .replace(
      /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );
}

function renderMarkdown(raw) {
  const lines = escapeHtml(raw).split(/\r?\n/);
  const out = [];
  let list = null;      // "ul" | "ol"
  let para = [];

  const closeList = () => {
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
  };
  const closePara = () => {
    if (para.length) {
      out.push(`<p>${para.join("<br>")}</p>`);
      para = [];
    }
  };

  lines.forEach((line) => {
    const text = line.trimEnd();

    if (!text.trim()) {
      closePara();
      closeList();
      return;
    }
    if (/^\s*([-*_]\s*){3,}$/.test(text)) {
      closePara();
      closeList();
      out.push("<hr>");
      return;
    }
    const heading = text.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closePara();
      closeList();
      const tag = heading[1].length <= 2 ? "h3" : "h4";
      out.push(`<${tag}>${inlineMd(heading[2])}</${tag}>`);
      return;
    }
    // текст уже экранирован, поэтому ищем &gt;, а не >
    const quote = text.match(/^&gt;\s?(.*)$/);
    if (quote) {
      closePara();
      closeList();
      out.push(`<blockquote>${inlineMd(quote[1])}</blockquote>`);
      return;
    }
    const bullet = text.match(/^\s*[-*•]\s+(.*)$/);
    if (bullet) {
      closePara();
      if (list !== "ul") {
        closeList();
        out.push("<ul>");
        list = "ul";
      }
      out.push(`<li>${inlineMd(bullet[1])}</li>`);
      return;
    }
    const numbered = text.match(/^\s*\d+[.)]\s+(.*)$/);
    if (numbered) {
      closePara();
      if (list !== "ol") {
        closeList();
        out.push("<ol>");
        list = "ol";
      }
      out.push(`<li>${inlineMd(numbered[1])}</li>`);
      return;
    }
    closeList();
    para.push(inlineMd(text));
  });

  closePara();
  closeList();
  return out.join("\n");
}

/* ── сообщения ───────────────────────────────────────────────────────── */
function renderMessages() {
  const box = $("#messages");
  box.textContent = "";
  const messages = state.chat?.messages || [];
  if (!messages.length) {
    box.append(renderEmpty());
    return;
  }
  messages.forEach((msg) => box.append(renderMessage(msg)));
  box.scrollTop = box.scrollHeight;
}

const SUGGESTIONS = [
  "Сделай серию сторис на основе выбранных материалов",
  "Напиши пост: главная мысль эфира простыми словами",
  "Придумай 5 цепляющих тем для прогрева в клубе",
  "Собери ответы на частые возражения из диалогов",
];

function renderEmpty() {
  const hasObjects = (state.chat?.objects || []).length > 0;
  const wrap = el("div", "empty");
  const head = el("div");
  const logo = el("span", "logo");
  logo.style.margin = "0 auto 14px";
  logo.style.display = "block";
  head.append(logo, el("h2", null, "С чего начнём?"));
  head.append(
    el("p", "sub", "Студия пишет контент в голосе Константина, опираясь на эфиры, молитвы и переписку.")
  );
  wrap.append(head);

  const steps = el("div", "steps");
  const step = (n, title, text, done, pane) => {
    const node = el("div", "step" + (done ? " done" : ""));
    node.append(el("b", null, done ? `✓ ${title}` : `${n}. ${title}`), el("span", null, text));
    if (pane) {
      node.style.cursor = "pointer";
      node.addEventListener("click", () => focusPane(pane));
    }
    return node;
  };
  steps.append(
    step(1, "Материалы", hasObjects ? "Материалы выбраны" : "Отметьте слева эфиры, молитвы, встречи, отзывы", hasObjects, "pane-left"),
    step(2, "Формат", "Вид контента и этап прогрева — справа", Boolean(state.chat?.format), "pane-right"),
    step(3, "Задача", "Опишите, что нужно получить", false)
  );
  wrap.append(steps);

  const suggest = el("div", "suggest");
  SUGGESTIONS.forEach((text) => {
    const btn = el("button", null, text);
    btn.type = "button";
    btn.addEventListener("click", () => {
      const input = $("#input");
      input.value = text;
      autoGrow();
      input.focus();
    });
    suggest.append(btn);
  });
  wrap.append(suggest);
  return wrap;
}

function renderMessage(msg) {
  const wrap = el("div", `msg ${msg.role}`);
  if (msg.role === "system") {
    wrap.append(el("div", "bubble", msg.text));
    return wrap;
  }
  if (msg.role === "assistant") {
    const bubble = el("div", "bubble md");
    bubble.innerHTML = renderMarkdown(msg.text);
    wrap.append(bubble);
  } else {
    wrap.append(el("div", "bubble", msg.text));
  }
  if (msg.role === "assistant") {
    const tools = el("div", "tools");
    const copy = iconButton("copy", "Копировать");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(msg.text);
        setIconButton(copy, "check", "Скопировано");
        copy.classList.add("done");
        setTimeout(() => {
          setIconButton(copy, "copy", "Копировать");
          copy.classList.remove("done");
        }, 1500);
      } catch (e) {
        toast("Не удалось скопировать", "bad");
      }
    });
    tools.append(copy);
    if (msg.id) tools.append(goldenButton(msg));
    const trace = msg.meta?.trace;
    let pre = null;
    if (trace) {
      const toggle = iconButton("code", "Что ушло в модель");
      pre = el("div", "trace");
      pre.hidden = true;
      pre.textContent = traceText(trace);
      toggle.addEventListener("click", () => {
        pre.hidden = !pre.hidden;
        setIconButton(toggle, "code", pre.hidden ? "Что ушло в модель" : "Скрыть");
      });
      tools.append(toggle);
    }
    if (msg.model) tools.append(el("span", "model", msg.model));
    wrap.append(tools);
    if (pre) wrap.append(pre);
  }
  return wrap;
}

function goldenButton(msg) {
  const done = Boolean(msg.meta?.golden);
  const btn = iconButton(done ? "check" : "star", done ? "В золотом фонде" : "В золотой фонд");
  btn.title = "Сохранить как образец формата: следующие ответы будут ориентироваться на него";
  if (done) {
    btn.disabled = true;
    btn.classList.add("done");
  }
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    setIconButton(btn, "star", "Сохраняю…");
    try {
      await api(`/api/chats/${state.chat.id}/messages/${msg.id}/golden`, { method: "POST" });
      msg.meta = { ...(msg.meta || {}), golden: true };
      setIconButton(btn, "check", "В золотом фонде");
      btn.classList.add("done");
      toast("Сохранено как образец");
    } catch (err) {
      btn.disabled = false;
      setIconButton(btn, "star", "В золотой фонд");
      toast(`Не сохранилось: ${err.message}`, "bad");
    }
  });
  return btn;
}

function traceText(trace) {
  const lines = [];
  lines.push(`режим сырья: ${trace.mode || "—"}`);
  if ((trace.objects || []).length) lines.push(`объекты: ${trace.objects.join("; ")}`);
  if (trace.rag)
    lines.push(
      `из базы: чанков ${trace.rag.chunks ?? 0}, карточек ${trace.rag.cards ?? 0}` +
        (trace.rag.golden ? `, образцов ${trace.rag.golden}` : "")
    );
  if (trace.context_chars) lines.push(`контекст: ${trace.context_chars} знаков`);
  (trace.stages || []).forEach((stage) => {
    const bits = [`${stage.name}: ${stage.model || "—"}`, `${Math.round((stage.ms || 0) / 100) / 10} с`];
    if (stage.chars) bits.push(`${stage.chars} знаков сырья`);
    if (stage.raw_chars) bits.push(`${stage.raw_chars} знаков сырья`);
    if (stage.llm_chunks) bits.push(`${stage.llm_chunks} кусков`);
    if (stage.excerpts) bits.push(`${stage.excerpts} выдержек`);
    if (stage.is_revision) bits.push("правка прошлого ответа");
    if (stage.reused) bits.push("выжимка с прошлого хода");
    else if (stage.cached) bits.push("из кэша");
    if (stage.history) bits.push(`история: ${stage.history} сообщ. (${stage.history_chars || 0} знаков)`);
    if (stage.context_chars) bits.push(`${stage.context_chars} знаков контекста`);
    lines.push("— " + bits.join(", "));
    (stage.searches || []).forEach((s) => {
      const f = [];
      if (s.source_kind) f.push(s.source_kind.join("/"));
      if (s.types) f.push(s.types.join("/"));
      if (s.lesson_key) f.push(`урок ${s.lesson_key}`);
      lines.push(`    поиск [${s.collection}] «${s.text}» k=${s.k}${f.length ? " · " + f.join(" · ") : ""}`);
    });
    if (stage.notes) lines.push(`    заметка: ${stage.notes}`);
  });
  return lines.join("\n");
}

$("#composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  await send();
});

// Enter — отправить, Shift+Enter — перенос. На телефоне Enter — всегда перенос,
// отправка кнопкой. Ctrl/Cmd+Enter отправляет везде.
$("#input").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.isComposing) return;
  const force = e.metaKey || e.ctrlKey;
  if (force || (!e.shiftKey && !e.altKey && !isTouch())) {
    e.preventDefault();
    send();
  }
});

/** Поле ввода растёт под текст (потолок задан в CSS через max-height). */
function autoGrow() {
  const input = $("#input");
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
  updateSendState();
}

function updateSendState() {
  $("#btn-send").disabled = Boolean(state.polling) || !$("#input").value.trim();
}

$("#input").addEventListener("input", autoGrow);

async function send() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text || !state.chat || state.polling) return;
  input.value = "";
  autoGrow();
  state.chat.messages = state.chat.messages || [];
  state.chat.messages.push({ role: "user", text });
  renderMessages();
  setBusy(true, "Планирую, что искать");
  try {
    const data = await api(`/api/chats/${state.chat.id}/message`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    pollJob(data.job_id);
  } catch (err) {
    setBusy(false);
    // Возвращаем текст в поле — чтобы не пришлось набирать заново.
    if (!input.value) {
      input.value = text;
      autoGrow();
    }
    pushSystem(`Не отправилось: ${err.message}`);
  }
}

function pollJob(jobId) {
  const started = Date.now();
  const chatId = state.chat?.id;
  state.polling = setInterval(async () => {
    try {
      const data = await api(`/api/jobs/${jobId}`);
      const secs = Math.round((Date.now() - started) / 1000);
      $("#progress-time").textContent = `${secs} с`;
      if (data.status === "running") {
        $("#progress-text").textContent = data.stage_title || "Думаю…";
        return;
      }
      clearInterval(state.polling);
      state.polling = null;
      setBusy(false);
      if (data.status === "done" && data.message && document.hidden) {
        document.title = "✓ Ответ готов — Контент завод";
      }
      if (data.status === "done" && data.message) {
        if (state.chat?.id === chatId) {
          state.chat.messages.push(data.message);
          renderMessages();
        } else {
          toast("Ответ готов в другом чате");
        }
        await refreshChats();
      } else {
        pushSystem(`Ошибка: ${data.error || "неизвестно"}`);
      }
    } catch (err) {
      clearInterval(state.polling);
      state.polling = null;
      setBusy(false);
      pushSystem(`Ошибка: ${err.message}`);
    }
  }, 1500);
}

function setBusy(on, label) {
  $("#progress").hidden = !on;
  if (on) $("#progress-time").textContent = "";
  if (on && label) $("#progress-text").textContent = label;
  // state.polling выставляется чуть позже — учитываем явный флаг.
  $("#btn-send").disabled = on || !$("#input").value.trim();
}

function pushSystem(text) {
  state.chat.messages = state.chat.messages || [];
  state.chat.messages.push({ role: "system", text, meta: {} });
  renderMessages();
}

/* ── дерево объектов ─────────────────────────────────────────────────── */
function selected() {
  return new Set(state.chat?.objects || []);
}

const OPEN_KEY = "studio.open";

function loadOpen() {
  try {
    return JSON.parse(localStorage.getItem(OPEN_KEY) || "{}") || {};
  } catch (err) {
    return {};
  }
}

function saveOpen() {
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify(state.open || {}));
  } catch (err) {
    /* приватный режим — просто не запоминаем */
  }
}

function isOpen(key, fallback) {
  if (state.open && Object.prototype.hasOwnProperty.call(state.open, key)) return state.open[key];
  return fallback;
}

function toggleOpen(key, fallback) {
  state.open = state.open || {};
  state.open[key] = !isOpen(key, fallback);
  saveOpen();
  renderTree();
}

function countSelected(nodes, sel) {
  let n = 0;
  (nodes || []).forEach((node) => {
    if (sel.has(node.id)) n += 1;
    n += countSelected(node.children, sel);
  });
  return n;
}

function chevron() {
  const node = el("span", "chevron");
  node.innerHTML = icon("chevron");
  return node;
}

function foldRow(key, title, fallbackOpen, selectedCount, level) {
  const open = isOpen(key, fallbackOpen);
  const row = el("button", `fold level-${level}` + (open ? " open" : ""));
  row.type = "button";
  row.setAttribute("aria-expanded", String(open));
  row.append(chevron());
  row.append(el("span", "fold-title", title));
  if (selectedCount) row.append(el("span", "badge", String(selectedCount)));
  row.addEventListener("click", () => toggleOpen(key, fallbackOpen));
  return { row, open };
}

function renderTree() {
  const box = $("#tree");
  box.textContent = "";
  const sel = selected();
  const needle = state.search.trim().toLowerCase();
  const searching = needle.length > 0;
  const match = (item) =>
    !searching ||
    (item.name || "").toLowerCase().includes(needle) ||
    (item.meta || "").toLowerCase().includes(needle) ||
    (item.children || []).some(match);

  (state.tree?.groups || []).forEach((group) => {
    const items = (group.items || []).filter(match);
    const modules = (group.modules || [])
      .map((mod) => ({ ...mod, lessons: (mod.lessons || []).filter(match) }))
      .filter((mod) => mod.lessons.length);
    if (!items.length && !modules.length) return;

    const allNodes = [...items, ...modules.flatMap((m) => m.lessons)];
    const groupKey = `g:${group.id}`;
    const { row, open } = foldRow(
      groupKey,
      group.title,
      true,
      countSelected(allNodes, sel),
      0
    );
    const wrap = el("div", "tree-group");
    wrap.append(row);
    if (open || searching) {
      const body = el("div", "fold-body");
      if (group.hint) body.append(el("div", "meta muted hint", group.hint));
      items.forEach((item) => appendWithChildren(body, item, sel, 1, searching, match));
      modules.forEach((mod) => {
        const modKey = `m:${group.id}:${mod.id}`;
        const modFold = foldRow(
          modKey,
          mod.title,
          false,
          countSelected(mod.lessons, sel),
          1
        );
        body.append(modFold.row);
        if (modFold.open || searching) {
          const modBody = el("div", "fold-body");
          mod.lessons.forEach((lesson) =>
            appendWithChildren(modBody, lesson, sel, 2, searching, match)
          );
          body.append(modBody);
        }
      });
      wrap.append(body);
    }
    box.append(wrap);
  });
  if (!box.childElementCount) {
    box.append(
      el(
        "div",
        "tree-empty",
        searching
          ? "Ничего не нашлось"
          : "Материалов пока нет — дождитесь индексации эфиров/молитв в RAG"
      )
    );
  }
}

/** Строка объекта, а под ней — сворачиваемый список его частей. */
function appendWithChildren(box, item, sel, level, searching, match) {
  box.append(nodeRow(item, sel, level));
  const children = (item.children || []).filter(match);
  if (!children.length) return;
  const key = `c:${item.id}`;
  const open = isOpen(key, false) || searching;
  const label = item.kind === "facet" ? "записи" : item.kind === "lesson" ? "материалы" : "части";
  const toggle = el("button", `fold level-${level + 1} sub` + (open ? " open" : ""));
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(open));
  toggle.append(chevron());
  toggle.append(el("span", "fold-title", `${label}: ${children.length}`));
  const picked = countSelected(children, sel);
  if (picked) toggle.append(el("span", "badge", String(picked)));
  toggle.addEventListener("click", () => toggleOpen(key, false));
  box.append(toggle);
  if (open) {
    const kids = el("div", "fold-body");
    children.forEach((child) => kids.append(nodeRow(child, sel, level + 1)));
    box.append(kids);
  }
}

function nodeRow(item, sel, level) {
  const row = el("div", `node level-${level}` + (sel.has(item.id) ? " picked" : ""));
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = sel.has(item.id);
  cb.setAttribute("aria-label", item.name);
  const selectable = item.selectable !== false;
  cb.disabled = !selectable;
  cb.addEventListener("change", () => toggleObject(item.id, cb.checked));
  const label = el("label", "label" + (selectable ? "" : " off"));
  label.append(el("div", "name", item.name));
  if (item.meta) label.append(el("div", "meta", item.meta));
  if (selectable) {
    label.addEventListener("click", (e) => {
      e.preventDefault();
      cb.checked = !cb.checked;
      toggleObject(item.id, cb.checked);
    });
  } else {
    label.title = "Объект не обработан — выбрать нельзя";
  }
  const at = el("button", "at", "@");
  at.type = "button";
  at.title = selectable
    ? "Вставить название в сообщение и добавить в контекст"
    : "Вставить название в сообщение";
  at.addEventListener("click", async (e) => {
    e.preventDefault();
    insertName(item.name);
    if (selectable) {
      await toggleObject(item.id, true);
    }
  });
  let srcBtn = null;
  if (item.kind === "mat" || item.has_source) {
    srcBtn = el("button", "src", item.has_telemost ? "🎙" : "📄");
    srcBtn.type = "button";
    srcBtn.title = item.has_telemost
      ? "Открыть исходник (транскрипт телемоста / RAG)"
      : "Открыть исходный текст из RAG";
    srcBtn.addEventListener("click", (e) => {
      e.preventDefault();
      openSource(item.id, item.name);
    });
  }
  const status = el("span", `status ${item.status || "off"}`);
  row.append(cb, label);
  if (srcBtn) row.append(srcBtn);
  row.append(at, status);
  return row;
}

let sourceCurrentId = "";

async function openSource(id, fallbackName) {
  const dlg = $("#source-dialog");
  if (!dlg) return;
  sourceCurrentId = id;
  $("#source-title").textContent = fallbackName || "Исходник";
  $("#source-facet").textContent = "Загрузка…";
  $("#source-meta").textContent = "";
  $("#source-links").textContent = "";
  $("#source-body").textContent = "Загрузка исходника…";
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  try {
    const path = id.startsWith("mat:") ? id.slice(4) : id;
    const data = await api(`/api/materials/${encodeURIComponent(path).replace(/%3A/g, ":")}`);
    $("#source-title").textContent = data.name || fallbackName || id;
    $("#source-facet").textContent = data.facet_name || data.facet || "";
    const bits = [];
    if (data.origin) bits.push(`источник: ${data.origin}`);
    if (data.chunks) bits.push(`${data.chunks} фрагм. в RAG`);
    if (data.chars) bits.push(`${data.chars} символов`);
    if (data.meeting_id) bits.push(`телемост ${data.meeting_id}`);
    $("#source-meta").textContent = bits.join(" · ");
    const links = $("#source-links");
    links.textContent = "";
    if (data.private_link) {
      const a = el("a", null, data.has_telemost ? "Ссылка на телемост" : "Исходная ссылка");
      a.href = data.private_link;
      a.target = "_blank";
      a.rel = "noopener";
      links.append(a);
    }
    if (data.group_link) {
      const a = el("a", null, "Сообщение в группе");
      a.href = data.group_link;
      a.target = "_blank";
      a.rel = "noopener";
      links.append(a);
    }
    $("#source-body").textContent = data.text || data.preview || "Текст исходника пуст";
  } catch (err) {
    $("#source-facet").textContent = "Ошибка";
    $("#source-body").textContent = err.message || String(err);
  }
}

$("#source-use")?.addEventListener("click", async () => {
  if (!sourceCurrentId) return;
  await toggleObject(sourceCurrentId, true);
  const dlg = $("#source-dialog");
  if (dlg?.open) dlg.close();
  toast("Запись добавлена в контекст");
});

function insertName(name) {
  const input = $("#input");
  const prefix = input.value && !input.value.endsWith(" ") ? " " : "";
  input.value = `${input.value}${prefix}«${name}» `;
  autoGrow();
  showPane("pane-chat");
  input.focus();
}

/** id → id родителя (урок для своих материалов, «вся переписка» для срезов чата). */
function parentMap() {
  const map = {};
  const walk = (items) => (items || []).forEach((item) => {
    (item.children || []).forEach((child) => {
      map[child.id] = item.id;
      walk([child]);
    });
  });
  (state.tree?.groups || []).forEach((group) => {
    walk(group.items);
    (group.modules || []).forEach((mod) => walk(mod.lessons));
  });
  return map;
}

async function toggleObject(id, on) {
  const set = selected();
  if (!on) {
    set.delete(id);
  } else {
    set.add(id);
    // Родитель уже включает свои части — вместе их выбирать незачем.
    const parents = parentMap();
    const parent = parents[id];
    if (parent) set.delete(parent);
    Object.entries(parents).forEach(([child, p]) => {
      if (p === id) set.delete(child);
    });
  }
  try {
    await patchChat({ objects: [...set] });
  } catch (err) {
    toast(`Не сохранилось: ${err.message}`, "bad");
  }
  renderTree();
  if (!(state.chat?.messages || []).length) renderMessages();
}

$("#tree-search").addEventListener("input", (e) => {
  state.search = e.target.value || "";
  renderTree();
});

$("#tree-search").addEventListener("keydown", (e) => {
  if (e.key === "Escape" && e.target.value) {
    e.target.value = "";
    state.search = "";
    renderTree();
  }
});

$("#btn-clear-ctx").addEventListener("click", async () => {
  try {
    await patchChat({ objects: [] });
  } catch (err) {
    toast(`Не сохранилось: ${err.message}`, "bad");
  }
  renderTree();
  if (!(state.chat?.messages || []).length) renderMessages();
});

function renderContext() {
  const box = $("#ctx-chips");
  box.textContent = "";
  const ids = state.chat?.objects || [];
  ids.forEach((id) => {
    const chip = el("span", "chip");
    const name = state.names[id] || id;
    chip.title = name;
    chip.append(el("span", "t", name));
    const x = el("button");
    x.type = "button";
    x.innerHTML = icon("x");
    x.title = "Убрать из контекста";
    x.setAttribute("aria-label", `Убрать «${name}»`);
    x.addEventListener("click", () => toggleObject(id, false));
    chip.append(x);
    box.append(chip);
  });
  const fmt = state.formats.find((f) => f.id === state.chat?.format);
  const stage = state.stages.find((s) => s.id === state.chat?.stage);
  $("#ctx-format").textContent = fmt ? fmt.title : "Вид контента";
  $("#ctx-stage").textContent = stage ? stage.title : "Этап не выбран";
  $("#ctx-hint").textContent = ids.length ? "" : "Материалы не выбраны";
  $("#btn-clear-ctx").hidden = !ids.length;
  const badge = $("#tab-badge");
  badge.hidden = !ids.length;
  badge.textContent = String(ids.length);
}

// Пилюли формата/этапа у поля ввода ведут в настройки.
$("#ctx-format").addEventListener("click", () => focusPane("pane-right", "#formats"));
$("#ctx-stage").addEventListener("click", () => focusPane("pane-right", "#stages"));

/* ── формат, этап, фокус ─────────────────────────────────────────────── */
function optionRow(name, value, checked, title, hint, onPick) {
  const row = el("label", "option");
  const radio = document.createElement("input");
  radio.type = "radio";
  radio.name = name;
  radio.value = value;
  radio.checked = checked;
  radio.addEventListener("change", async () => {
    try {
      await onPick();
    } catch (err) {
      toast(`Не сохранилось: ${err.message}`, "bad");
    }
  });
  row.append(radio, el("span", "t", title));
  if (hint) row.append(el("span", "h", hint));
  return row;
}

function renderFormats() {
  const box = $("#formats");
  box.textContent = "";
  state.formats.forEach((fmt) => {
    const hint = [fmt.platform, fmt.customized ? "свой скилл" : ""]
      .filter(Boolean)
      .join(" · ");
    const row = optionRow(
      "format",
      fmt.id,
      state.chat?.format === fmt.id,
      fmt.title,
      hint,
      () => patchChat({ format: fmt.id })
    );
    const edit = el("button", "format-edit", "скилл");
    edit.type = "button";
    edit.title = "Править скилл вида контента";
    edit.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openFormatEditor(fmt.id);
    });
    row.style.position = "relative";
    row.append(edit);
    box.append(row);
  });
}

function renderStages() {
  const box = $("#stages");
  box.textContent = "";
  state.stages.forEach((stage) => {
    box.append(
      optionRow("stage", stage.id, state.chat?.stage === stage.id, stage.title, stage.hint, () =>
        patchChat({ stage: stage.id })
      )
    );
  });
}

function renderPassports() {
  const box = $("#passports");
  if (!box) return;
  box.textContent = "";
  (state.passports || []).forEach((p) => {
    const card = el("div", "passport-card");
    const head = el("button", "passport-card-main");
    head.type = "button";
    const fill = el(
      "span",
      "fill" + (p.done || (p.filled && p.filled >= p.total) ? " ok" : ""),
      `${p.filled || 0}/${p.total || 0}`
    );
    head.append(el("span", "t", p.title || p.kind), fill);
    head.append(
      el(
        "span",
        "h",
        p.done ? "Готов — попадает в генерацию" : "Открыть слоты вручную"
      )
    );
    head.addEventListener("click", () => openPassportEditor(p.kind));
    card.append(head);
    if (p.fill_url) {
      const fillBtn = el("a", "btn passport-fill-btn");
      fillBtn.href = p.fill_url;
      fillBtn.target = "_blank";
      fillBtn.rel = "noopener";
      fillBtn.textContent = "Заполнить паспорт";
      fillBtn.title = "Открыть ИИ-мастер в Telegram";
      card.append(fillBtn);
    }
    box.append(card);
  });
}

async function openPassportEditor(kind) {
  const dlg = $("#passport-dialog");
  if (!dlg) return;
  state.passportKind = kind;
  $("#passport-title").textContent = "Загрузка…";
  $("#passport-meta").textContent = "";
  $("#passport-slots").textContent = "Загрузка…";
  $("#passport-done").checked = false;
  const fillLink = $("#passport-fill-bot");
  if (fillLink) {
    fillLink.hidden = true;
    fillLink.removeAttribute("href");
  }
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  try {
    const data = await api(`/api/passports/${encodeURIComponent(kind)}`);
    $("#passport-title").textContent = data.title || kind;
    $("#passport-meta").textContent = data.updated_at
      ? `Обновлён: ${String(data.updated_at).replace("T", " ").slice(0, 16)}`
      : "Ещё не сохраняли";
    $("#passport-done").checked = Boolean(data.done);
    if (fillLink && data.fill_url) {
      fillLink.href = data.fill_url;
      fillLink.hidden = false;
    }
    const box = $("#passport-slots");
    box.textContent = "";
    (data.slot_meta || []).forEach((slot) => {
      const wrap = el("div", "edit-field");
      wrap.append(el("label", null, slot.label || slot.key));
      if (slot.hint) wrap.append(el("div", "hint", slot.hint));
      const ta = document.createElement("textarea");
      ta.className = "field";
      ta.rows = 3;
      ta.dataset.key = slot.key;
      ta.value = (data.slots && data.slots[slot.key]) || "";
      wrap.append(ta);
      box.append(wrap);
    });
  } catch (err) {
    $("#passport-title").textContent = "Ошибка";
    $("#passport-slots").textContent = err.message || String(err);
  }
}

$("#passport-save")?.addEventListener("click", async () => {
  const kind = state.passportKind;
  if (!kind) return;
  const slots = {};
  $("#passport-slots").querySelectorAll("textarea[data-key]").forEach((ta) => {
    slots[ta.dataset.key] = ta.value;
  });
  const btn = $("#passport-save");
  btn.disabled = true;
  try {
    const saved = await api(`/api/passports/${encodeURIComponent(kind)}`, {
      method: "PUT",
      body: JSON.stringify({ slots, done: $("#passport-done").checked }),
    });
    const brief = state.passports.find((p) => p.kind === kind);
    if (brief) {
      brief.filled = saved.filled;
      brief.total = saved.total;
      brief.done = saved.done;
      brief.title = saved.title || brief.title;
    } else {
      state.passports.push({
        kind: saved.kind,
        title: saved.title,
        filled: saved.filled,
        total: saved.total,
        done: saved.done,
      });
    }
    renderPassports();
    toast("Паспорт сохранён");
    const dlg = $("#passport-dialog");
    if (dlg?.open) dlg.close();
  } catch (err) {
    toast(`Не сохранилось: ${err.message}`, "bad");
  } finally {
    btn.disabled = false;
  }
});

function stageEditorCard(stage) {
  const card = el("div", "edit-stage");
  card.dataset.id = stage.id || "";
  const head = el("div", "edit-stage-head");
  head.append(el("h3", null, stage.id || "новый"));
  const del = el("button", "del", "Удалить");
  del.type = "button";
  del.addEventListener("click", () => {
    const box = $("#stages-editor");
    if (box.querySelectorAll(".edit-stage").length <= 1) {
      toast("Нужен хотя бы один этап", "bad");
      return;
    }
    card.remove();
  });
  head.append(del);
  card.append(head);
  const titleWrap = el("div", "edit-field");
  titleWrap.append(el("label", null, "Название"));
  const title = document.createElement("textarea");
  title.className = "field tiny";
  title.rows = 1;
  title.dataset.field = "title";
  title.value = stage.title || "";
  titleWrap.append(title);
  card.append(titleWrap);
  const descWrap = el("div", "edit-field");
  descWrap.append(el("label", null, "Описание этапа"));
  descWrap.append(
    el("div", "hint", "Общее: что происходит на этом шаге воронки. Попадает в план и в WRITE.")
  );
  const desc = document.createElement("textarea");
  desc.className = "field";
  desc.rows = 3;
  desc.dataset.field = "description";
  desc.value = stage.description || stage.hint || "";
  descWrap.append(desc);
  card.append(descWrap);
  return card;
}

async function openStagesEditor() {
  const dlg = $("#stages-dialog");
  if (!dlg) return;
  const box = $("#stages-editor");
  box.textContent = "Загрузка…";
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  try {
    const data = await api("/api/stages");
    box.textContent = "";
    (data.stages || []).forEach((stage) => box.append(stageEditorCard(stage)));
  } catch (err) {
    box.textContent = err.message || String(err);
  }
}

$("#btn-edit-stages")?.addEventListener("click", () => {
  focusPane("pane-right", "#stages");
  openStagesEditor();
});

$("#stages-add")?.addEventListener("click", () => {
  const box = $("#stages-editor");
  if (!box || box.textContent === "Загрузка…") return;
  box.append(
    stageEditorCard({
      id: "",
      title: "Новый этап",
      description: "",
    })
  );
  box.lastElementChild?.scrollIntoView({ block: "nearest" });
});

$("#stages-save")?.addEventListener("click", async () => {
  const stages = [];
  $("#stages-editor").querySelectorAll(".edit-stage").forEach((card) => {
    const row = { id: card.dataset.id || "" };
    card.querySelectorAll("textarea[data-field]").forEach((ta) => {
      row[ta.dataset.field] = ta.value;
    });
    if ((row.title || "").trim() || (row.description || "").trim()) stages.push(row);
  });
  if (!stages.length) {
    toast("Добавьте хотя бы один этап", "bad");
    return;
  }
  const btn = $("#stages-save");
  btn.disabled = true;
  try {
    const data = await api("/api/stages", {
      method: "PUT",
      body: JSON.stringify({ stages }),
    });
    state.stages = data.stages || [];
    renderStages();
    renderContext();
    // Если текущий этап удалили — выберем первый.
    if (state.chat && !state.stages.some((s) => s.id === state.chat.stage)) {
      const next = state.stages[0]?.id;
      if (next) await patchChat({ stage: next });
    }
    toast("Этапы сохранены");
    const dlg = $("#stages-dialog");
    if (dlg?.open) dlg.close();
  } catch (err) {
    toast(`Не сохранилось: ${err.message}`, "bad");
  } finally {
    btn.disabled = false;
  }
});

async function openFormatEditor(formatId) {
  const dlg = $("#format-dialog");
  if (!dlg) return;
  state.formatId = formatId;
  $("#format-title").textContent = "Загрузка…";
  $("#format-skill").value = "";
  state.formatDefaultSkill = "";
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  try {
    const data = await api(`/api/formats/${encodeURIComponent(formatId)}`);
    $("#format-title").textContent = data.title || formatId;
    $("#format-meta").textContent = data.customized
      ? `${data.platform || ""} · изменённый скилл`.replace(/^ · /, "")
      : `${data.platform || ""} · дефолтный скилл`.replace(/^ · /, "");
    $("#format-skill").value = data.skill || "";
    state.formatDefaultSkill = data.default_skill || data.skill || "";
  } catch (err) {
    $("#format-title").textContent = "Ошибка";
    $("#format-skill").value = err.message || String(err);
  }
}

$("#btn-edit-formats")?.addEventListener("click", () => {
  focusPane("pane-right", "#formats");
  const current = state.chat?.format || state.formats[0]?.id;
  if (current) openFormatEditor(current);
});

function closeFormatDialog() {
  const dlg = $("#format-dialog");
  if (dlg?.open) dlg.close();
}

$("#format-close")?.addEventListener("click", closeFormatDialog);
$("#format-cancel")?.addEventListener("click", closeFormatDialog);

$("#format-reset")?.addEventListener("click", () => {
  if (state.formatDefaultSkill) $("#format-skill").value = state.formatDefaultSkill;
});

function renderLlmSummary() {
  const box = $("#llm-summary");
  if (!box) return;
  box.textContent = "";
  const steps = state.llm?.steps || [];
  if (!steps.length) {
    box.textContent = "Не загрузилось";
    return;
  }
  steps.forEach((step) => {
    const row = el("button", "row");
    row.type = "button";
    row.append(el("span", "k", step.title));
    row.append(el("span", "v", `${step.provider} · ${step.model}`));
    row.addEventListener("click", () => openLlmEditor());
    box.append(row);
  });
}

function _llmModelOptions(provider, selected, modelsMap) {
  const list = (modelsMap && modelsMap[provider]) || [];
  const ids = list.map((m) => m.id);
  const opts = list.map((m) => ({ id: m.id, title: m.title || m.id }));
  if (selected && !ids.includes(selected)) {
    opts.unshift({ id: selected, title: `${selected} (своя)` });
  }
  opts.push({ id: "__custom__", title: "Своя модель…" });
  return opts;
}

function _llmSourceLabel(source) {
  const src = source || {};
  const bit = (name, key) => {
    const v = String(src[key] || "");
    if (v === "api") return `${name}: API`;
    if (v.startsWith("fallback")) return `${name}: запасной список`;
    return `${name}: ${v || "—"}`;
  };
  return `${bit("OpenAI", "openai")} · ${bit("DeepSeek", "deepseek")}`;
}

async function _loadLlmEditor(refresh) {
  const box = $("#llm-editor");
  if (!box) return;
  box.textContent = refresh ? "Обновляю список моделей…" : "Загрузка…";
  try {
    const data = await api(`/api/llm-settings${refresh ? "?refresh=1" : ""}`);
    state.llm = data;
    const meta = $("#llm-meta");
    if (meta) {
      meta.textContent = `Список моделей: ${_llmSourceLabel(data.models_source)}. Кэш ~10 мин.`;
    }
    box.textContent = "";
    (data.steps || []).forEach((step) => {
        const card = el("div", "llm-step");
        card.dataset.id = step.id;
        card.append(el("h3", null, step.title));
        if (step.hint) card.append(el("div", "hint", step.hint));
        const grid = el("div", "grid2");

        const provWrap = el("div", "edit-field");
        provWrap.append(el("label", null, "Провайдер"));
        const prov = document.createElement("select");
        prov.className = "field";
        prov.dataset.field = "provider";
        (data.providers || []).forEach((p) => {
          const opt = document.createElement("option");
          opt.value = p.id;
          opt.textContent = p.title || p.id;
          if (p.id === step.provider) opt.selected = true;
          prov.append(opt);
        });
        provWrap.append(prov);
        grid.append(provWrap);

        const modelWrap = el("div", "edit-field");
        modelWrap.append(el("label", null, "Модель"));
        const modelSel = document.createElement("select");
        modelSel.className = "field";
        modelSel.dataset.field = "model_select";
        const custom = document.createElement("input");
        custom.type = "text";
        custom.className = "field";
        custom.dataset.field = "model_custom";
        custom.placeholder = "id модели, например gpt-4.1";
        custom.hidden = true;
        const fillModels = (provider, selected) => {
          modelSel.textContent = "";
          _llmModelOptions(provider, selected, data.models).forEach((m) => {
            const opt = document.createElement("option");
            opt.value = m.id;
            opt.textContent = m.title;
            if (m.id === selected) opt.selected = true;
            modelSel.append(opt);
          });
          const known = ((data.models || {})[provider] || []).some((m) => m.id === selected);
          custom.hidden = Boolean(known || !selected);
          if (!known && selected) custom.value = selected;
          else if (known) custom.value = "";
        };
        fillModels(step.provider, step.model);
        prov.addEventListener("change", () => {
          const first = ((data.models || {})[prov.value] || [])[0];
          fillModels(prov.value, first?.id || "");
        });
        modelSel.addEventListener("change", () => {
          if (modelSel.value === "__custom__") {
            custom.hidden = false;
            custom.focus();
          } else {
            custom.hidden = true;
            custom.value = "";
          }
        });
        modelWrap.append(modelSel, custom);
        grid.append(modelWrap);
        card.append(grid);
        const def = el(
          "div",
          "hint",
          `Дефолт из .env: ${step.default_provider} · ${step.default_model}`
        );
        card.append(def);
        box.append(card);
      });
  } catch (err) {
    box.textContent = err.message || String(err);
  }
}

function openLlmEditor() {
  const dlg = $("#llm-dialog");
  if (!dlg) return;
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  _loadLlmEditor(false);
}

function closeLlmDialog() {
  const dlg = $("#llm-dialog");
  if (dlg?.open) dlg.close();
}

$("#btn-edit-llm")?.addEventListener("click", () => {
  focusPane("pane-right", "#llm-summary");
  openLlmEditor();
});
$("#llm-close")?.addEventListener("click", closeLlmDialog);
$("#llm-cancel")?.addEventListener("click", closeLlmDialog);
$("#llm-refresh")?.addEventListener("click", () => _loadLlmEditor(true));

$("#llm-reset")?.addEventListener("click", () => {
  const data = state.llm;
  if (!data?.steps) return;
  $("#llm-editor").querySelectorAll(".llm-step").forEach((card) => {
    const step = data.steps.find((s) => s.id === card.dataset.id);
    if (!step) return;
    const prov = card.querySelector('[data-field="provider"]');
    const modelSel = card.querySelector('[data-field="model_select"]');
    const custom = card.querySelector('[data-field="model_custom"]');
    if (prov) prov.value = step.default_provider;
    if (modelSel) {
      // пересобрать опции под дефолтный провайдер
      modelSel.textContent = "";
      _llmModelOptions(step.default_provider, step.default_model, data.models).forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.title;
        if (m.id === step.default_model) opt.selected = true;
        modelSel.append(opt);
      });
    }
    if (custom) {
      custom.value = "";
      custom.hidden = true;
    }
  });
});

$("#llm-save")?.addEventListener("click", async (e) => {
  e.preventDefault();
  const steps = {};
  $("#llm-editor").querySelectorAll(".llm-step").forEach((card) => {
    const id = card.dataset.id;
    if (!id) return;
    const provider = card.querySelector('[data-field="provider"]')?.value || "openai";
    const sel = card.querySelector('[data-field="model_select"]')?.value || "";
    const custom = (card.querySelector('[data-field="model_custom"]')?.value || "").trim();
    const model = sel === "__custom__" ? custom : sel;
    if (model) steps[id] = { provider, model };
  });
  const btn = $("#llm-save");
  btn.disabled = true;
  try {
    state.llm = await api("/api/llm-settings", {
      method: "POST",
      body: JSON.stringify({ steps }),
    });
    renderLlmSummary();
    toast("Модели сохранены");
    closeLlmDialog();
  } catch (err) {
    toast(`Не сохранилось: ${err.message}`, "bad");
  } finally {
    btn.disabled = false;
  }
});

$("#format-save")?.addEventListener("click", async (e) => {
  e.preventDefault();
  e.stopPropagation();
  const formatId = state.formatId;
  if (!formatId) {
    toast("Не выбран вид контента", "bad");
    return;
  }
  const btn = $("#format-save");
  const skill = $("#format-skill").value;
  btn.disabled = true;
  try {
    const saved = await api(`/api/formats/${encodeURIComponent(formatId)}`, {
      method: "POST",
      body: JSON.stringify({ skill }),
    });
    // Проверяем, что сервер реально вернул наш текст.
    if ((saved.skill || "").trim() !== skill.trim() && skill.trim() !== (saved.default_skill || "").trim()) {
      throw new Error("сервер вернул другой текст скилла");
    }
    const brief = state.formats.find((f) => f.id === formatId);
    if (brief) {
      brief.title = saved.title || brief.title;
      brief.customized = saved.customized;
    }
    renderFormats();
    renderContext();
    toast(saved.customized ? "Скилл сохранён" : "Скилл = дефолт (как в пособии)");
    closeFormatDialog();
  } catch (err) {
    toast(`Не сохранилось: ${err.message}`, "bad");
  } finally {
    btn.disabled = false;
  }
});

let focusTimer = null;
let savedTimer = null;
$("#focus").addEventListener("input", (e) => {
  const value = e.target.value;
  clearTimeout(focusTimer);
  $("#focus-saved").hidden = true;
  focusTimer = setTimeout(async () => {
    try {
      await patchChat({ focus: value });
      if ($("#focus-default").checked) {
        await api("/api/settings", { method: "POST", body: JSON.stringify({ focus: value }) });
      }
      $("#focus-saved").hidden = false;
      clearTimeout(savedTimer);
      savedTimer = setTimeout(() => ($("#focus-saved").hidden = true), 2000);
    } catch (err) {
      toast(`Фокус не сохранился: ${err.message}`, "bad");
    }
  }, 700);
});

/* ── прочее ──────────────────────────────────────────────────────────── */
async function startNewChat() {
  // Пустой чат уже открыт — новый не плодим, просто ставим курсор в поле.
  if (state.chat && !(state.chat.messages || []).length) {
    showPane("pane-chat");
    $("#input").focus();
    return;
  }
  try {
    await newChat();
    showPane("pane-chat");
    $("#input").focus();
  } catch (err) {
    if (err.message !== "auth") toast(`Не создался чат: ${err.message}`, "bad");
  }
}

$("#btn-new").addEventListener("click", startNewChat);
$("#btn-new-top").addEventListener("click", startNewChat);

$("#btn-sync").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.classList.add("spin");
  try {
    await reloadTree();
    renderIndexStats();
    toast("Материалы обновлены");
  } catch (err) {
    if (err.message !== "auth") toast(`Обновление: ${err.message}`, "bad");
  } finally {
    btn.disabled = false;
    btn.classList.remove("spin");
  }
});

async function reloadTree() {
  const tree = await api("/api/tree");
  state.tree = tree;
  state.names = collectNames(tree);
  renderTree();
  renderContext();
}

function renderIndexStats() {
  const box = $("#queue");
  if (!box) return;
  box.textContent = "";
  const items = [];
  (state.tree?.groups || []).forEach((g) => {
    (g.items || []).forEach((it) => items.push(it));
  });
  if (!items.length) {
    box.append(el("div", "q-empty", "В индексе пока нет материалов для клуба"));
    return;
  }
  let totalChunks = 0;
  let totalRecords = 0;
  items.forEach((it) => {
    const kids = (it.children || []).length;
    totalRecords += kids;
    const m = String(it.meta || "");
    const n = parseInt((m.match(/(\d+)\s*фрагмент/) || [])[1] || "0", 10);
    totalChunks += n;
    const row = el("div", "q-item");
    row.append(el("b", null, it.name || it.id));
    row.append(
      el(
        "span",
        "muted small",
        kids ? `${kids} записей · ${n || "?"} фрагм.` : m
      )
    );
    box.append(row);
  });
  const foot = el("div", "q-empty");
  foot.append(
    document.createTextNode(`Итого ${totalRecords} записей · ≈ ${totalChunks} фрагментов`)
  );
  box.append(foot);
}

async function loadQueue() {
  renderIndexStats();
}

/* ── вкладки на телефоне ─────────────────────────────────────────────── */
function showPane(target) {
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.pane === target);
  });
  $("#pane-left").classList.toggle("show", target === "pane-left");
  $("#pane-right").classList.toggle("show", target === "pane-right");
  $("#pane-chat").classList.toggle("hide", target !== "pane-chat");
}

/** Показать панель: на телефоне — переключить вкладку, на десктопе — подсветить блок. */
function focusPane(pane, selector) {
  if (isMobile()) showPane(pane);
  const target = selector ? $(selector) : $(`#${pane}`);
  if (!target) return;
  target.scrollIntoView({ block: "nearest", behavior: "smooth" });
  const first = target.querySelector("input:checked, input, textarea");
  if (first && !isTouch()) first.focus({ preventScroll: true });
}

document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => showPane(btn.dataset.pane));
});

/* ── горячие клавиши ─────────────────────────────────────────────────── */
document.addEventListener("keydown", (e) => {
  if ($("#app").hidden) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    focusPane("pane-left");
    $("#tree-search").focus();
    $("#tree-search").select();
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    document.title = state.product?.name
      ? `Контент завод · ${state.product.name}`
      : "Контент завод";
  }
});

/* ── тема ────────────────────────────────────────────────────────────── */
const THEME_KEY = "studio.theme";
const THEME_NEXT = { auto: "light", light: "dark", dark: "auto" };
const THEME_LABEL = { auto: "Тема: как в системе", light: "Тема: светлая", dark: "Тема: тёмная" };
const THEME_ICON = { auto: "auto", light: "sun", dark: "moon" };

function currentTheme() {
  const t = document.documentElement.dataset.theme;
  return t === "light" || t === "dark" ? t : "auto";
}

function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  const btn = $("#btn-theme");
  setIcon(btn, THEME_ICON[theme]);
  btn.title = THEME_LABEL[theme];
}

$("#btn-theme").addEventListener("click", () => {
  const next = THEME_NEXT[currentTheme()];
  applyTheme(next);
  try {
    if (next === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, next);
  } catch (err) {
    /* приватный режим — тема живёт до перезагрузки */
  }
  toast(THEME_LABEL[next]);
});

hydrateIcons();
applyTheme(currentTheme());
updateSendState();

setInterval(() => {
  if (!state.polling) loadQueue();
}, 60000);

/* ── мини-апп Telegram ───────────────────────────────────────────────── */
const tg = window.Telegram?.WebApp;

async function telegramLogin() {
  if (!tg || !tg.initData) return false;
  try {
    const res = await fetch("/api/login/telegram", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: tg.initData }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showLogin(data.error || "Telegram не пустил в студию");
      return true; // экран входа уже показан, дальше не идём
    }
    if (data.token) saveToken(data.token);
    $("#login").hidden = true;
    await boot();
    return true;
  } catch (err) {
    return false;
  }
}

async function start() {
  if (tg) {
    try {
      tg.ready();
      tg.expand();
      // В Telegram своя тема, не обязательно совпадающая с системной.
      // telegram-web-app.js грузится и в обычном браузере — смотрим только внутри Telegram.
      if (tg.initData && currentTheme() === "auto" && (tg.colorScheme === "dark" || tg.colorScheme === "light")) {
        applyTheme(tg.colorScheme);
      }
      const dark = window.getComputedStyle(document.documentElement).colorScheme === "dark";
      const bg = dark ? "#0c0c10" : "#f7f7f9";
      tg.setHeaderColor?.(bg);
      tg.setBackgroundColor?.(bg);
    } catch (err) {
      /* старый клиент Telegram */
    }
    if (await telegramLogin()) return;
  }
  try {
    await boot();
  } catch (err) {
    if (err.message !== "auth") showLogin("");
  }
}

start();
