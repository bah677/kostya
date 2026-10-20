/* Студия молитв — отдельная страница. Вход общий с контент-заводом:
   кука ставится там, здесь только проверяется. */

const $ = (sel, root = document) => root.querySelector(sel);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function toast(text, kind) {
  const node = el("div", "toast" + (kind ? ` ${kind}` : ""), text);
  $("#toasts").append(node);
  setTimeout(() => node.remove(), kind === "bad" ? 5000 : 2600);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (res.status === 401 || res.status === 503) {
    $("#app").hidden = true;
    $("#login").hidden = false;
    throw new Error("auth");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `Ошибка ${res.status}`);
  return data;
}

/* ── тема ────────────────────────────────────────────────────────────── */

function applyTheme() {
  const saved = localStorage.getItem("studio-theme") || "auto";
  document.documentElement.dataset.theme = saved === "auto" ? "" : saved;
}

$("#btn-theme")?.addEventListener("click", () => {
  const now = localStorage.getItem("studio-theme") || "auto";
  const next = now === "auto" ? "dark" : now === "dark" ? "light" : "auto";
  localStorage.setItem("studio-theme", next);
  applyTheme();
});

/* ── ключи YouTube ───────────────────────────────────────────────────── */

let _loading = false;

function keyCard(k) {
  const ok = k.alive && !k.error;
  const card = el("div", "yt-key" + (ok ? " ok" : " bad"));

  const head = el("div", "yt-key-head");
  head.append(el("b", null, k.label));
  head.append(el("span", "yt-key-state", ok ? "ключ жив" : "нужен перевыпуск"));
  card.append(head);

  if (k.channel_title) card.append(el("div", "muted small", k.channel_title));
  if (ok && k.subscribers !== null && k.subscribers !== undefined) {
    const n = (v) => Number(v || 0).toLocaleString("ru-RU");
    card.append(el("div", "yt-key-stats",
      `${n(k.subscribers)} подписчиков · ${n(k.videos)} роликов · ${n(k.views)} просмотров`));
  }
  if (k.refreshed_human) card.append(el("div", "muted small", `ключ обновлён ${k.refreshed_human}`));
  if (k.error) card.append(el("div", "yt-key-error small", k.error));

  const btn = el("button", "btn small", ok ? "Перевыпустить" : "Выпустить заново");
  btn.type = "button";
  btn.addEventListener("click", () => openKeyDialog(k));
  card.append(btn);
  return card;
}

async function loadKeys() {
  const box = $("#yt-keys");
  if (!box || _loading) return;
  _loading = true;
  box.textContent = "Проверяю ключи…";
  try {
    const data = await api("/api/youtube/keys");
    box.textContent = "";
    const keys = data.keys || [];
    if (!keys.length) {
      box.textContent = "Каналы не настроены.";
      return;
    }
    keys.forEach((k) => box.append(keyCard(k)));
  } catch (e) {
    if (e.message !== "auth") box.textContent = `Не удалось проверить: ${e.message}`;
  } finally {
    _loading = false;
  }
}

let _lang = "";

async function openKeyDialog(k) {
  _lang = k.lang;
  const dlg = $("#ytkey-dialog");
  $("#ytkey-title").textContent = k.label + (k.channel_title ? ` · ${k.channel_title}` : "");
  $("#ytkey-hint").textContent = "Готовлю ссылку…";
  $("#ytkey-code").value = "";
  const link = $("#ytkey-url");
  link.removeAttribute("href");
  dlg.showModal();
  try {
    const d = await api(`/api/youtube/keys/${k.lang}/auth-url`, { method: "POST" });
    link.href = d.url;
    $("#ytkey-hint").textContent = d.hint || "";
  } catch (e) {
    $("#ytkey-hint").textContent = `Не вышло: ${e.message}`;
  }
}

$("#ytkey-save")?.addEventListener("click", async () => {
  const raw = ($("#ytkey-code").value || "").trim();
  if (!raw) {
    toast("Вставьте адрес из браузера", "bad");
    return;
  }
  const btn = $("#ytkey-save");
  btn.disabled = true;
  btn.textContent = "Сохраняю…";
  try {
    const d = await api(`/api/youtube/keys/${_lang}/exchange`, {
      method: "POST",
      body: JSON.stringify({ code: raw }),
    });
    toast(`Ключ обновлён: ${d.channel_title}`);
    $("#ytkey-dialog").close();
    loadKeys();
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "Сохранить ключ";
  }
});

$("#btn-keys-refresh")?.addEventListener("click", () => loadKeys());

/* ── старт ───────────────────────────────────────────────────────────── */

(async function boot() {
  applyTheme();
  try {
    await api("/api/me");
    $("#app").hidden = false;
    $("#login").hidden = true;
    loadKeys();
  } catch (e) {
    if (e.message !== "auth") {
      $("#app").hidden = false;
      $("#login").hidden = true;
      toast(e.message, "bad");
    }
  }
})();
