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
/* Ключ и цикл те же, что на заводе: выбор переносится между страницами. */

const ICONS = {
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
};

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
  if (!btn) return;
  btn.dataset.icon = THEME_ICON[theme];
  btn.innerHTML = `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[THEME_ICON[theme]] || ""}</svg>`;
  btn.title = THEME_LABEL[theme];
}

function restoreTheme() {
  let saved = "auto";
  try {
    saved = localStorage.getItem(THEME_KEY) || "auto";
  } catch (err) {
    /* приватный режим — тема живёт до перезагрузки */
  }
  applyTheme(saved === "light" || saved === "dark" ? saved : "auto");
}

$("#btn-theme")?.addEventListener("click", () => {
  const next = THEME_NEXT[currentTheme()];
  applyTheme(next);
  try {
    if (next === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, next);
  } catch (err) {
    /* приватный режим */
  }
  toast(THEME_LABEL[next]);
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

/* ── настройки генерации ─────────────────────────────────────────────── */
/* Правки живут в файле рядом с данными, langs.setting читает его первым —
   поэтому сохранение действует на ближайший прогон без перезапуска. */

let _genFields = [];

function genInput(f) {
  const wrap = el("div", "gen-field");
  const id = `gen-${f.key}`;
  const label = el("label", null, f.title);
  label.htmlFor = id;
  wrap.append(label);

  let input;
  if (f.kind === "bool") {
    input = el("select", "field");
    [["", "как в .env"], ["1", "включено"], ["0", "выключено"]].forEach(([v, t]) => {
      const o = el("option", null, t);
      o.value = v;
      input.append(o);
    });
    input.value = f.value || "";
  } else {
    input = el("input", "field");
    input.type = f.kind === "number" ? "number" : "text";
    if (f.kind === "number") input.min = "0";
    input.value = f.value || "";
    input.placeholder = f.env || f.default || "";
  }
  input.id = id;
  input.dataset.key = f.key;
  input.addEventListener("input", () => {
    wrap.classList.toggle("changed", (input.value || "") !== (f.value || ""));
  });
  wrap.append(input);

  if (f.hint) wrap.append(el("div", "gen-hint", f.hint));
  const envText = f.env ? `в .env: ${f.env}` : "в .env не задано";
  wrap.append(el("div", "gen-env", `${envText} · сейчас действует: ${f.effective || "—"}`));
  return wrap;
}

function renderGenSettings(fields) {
  _genFields = fields;
  const box = $("#gen-settings");
  box.textContent = "";
  const groups = [];
  fields.forEach((f) => {
    let g = groups.find((x) => x.name === f.group);
    if (!g) {
      g = { name: f.group, items: [] };
      groups.push(g);
    }
    g.items.push(f);
  });
  groups.forEach((g) => {
    const node = el("div", "gen-group");
    node.append(el("h3", null, g.name));
    const grid = el("div", "gen-fields");
    g.items.forEach((f) => grid.append(genInput(f)));
    node.append(grid);
    box.append(node);
  });
}

async function loadGenSettings() {
  const box = $("#gen-settings");
  if (!box) return;
  box.textContent = "Загружаю…";
  try {
    const d = await api("/api/gen-settings");
    renderGenSettings(d.fields || []);
  } catch (e) {
    if (e.message !== "auth") box.textContent = `Не удалось загрузить: ${e.message}`;
  }
}

$("#btn-gen-save")?.addEventListener("click", async () => {
  const values = {};
  document.querySelectorAll("#gen-settings [data-key]").forEach((node) => {
    values[node.dataset.key] = node.value || "";
  });
  const btn = $("#btn-gen-save");
  btn.disabled = true;
  btn.textContent = "Сохраняю…";
  try {
    const d = await api("/api/gen-settings", {
      method: "PUT",
      body: JSON.stringify({ values }),
    });
    renderGenSettings(d.fields || []);
    const saved = $("#gen-saved");
    saved.hidden = false;
    setTimeout(() => { saved.hidden = true; }, 2500);
    toast("Настройки сохранены — подхватятся ближайшим прогоном");
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "Сохранить";
  }
});

$("#btn-gen-reload")?.addEventListener("click", () => loadGenSettings());

/* ── статистика каналов ──────────────────────────────────────────────── */
/* Цифры из ночных срезов: YouTube отдаёт только накопительные итоги, и
   прирост виден лишь как разница двух срезов. В API отсюда не ходим —
   страница открывается сразу и не тратит квоту. */

const nf = (v) => Number(v || 0).toLocaleString("ru-RU");

function signed(v) {
  const n = Number(v || 0);
  return (n > 0 ? "+" : n < 0 ? "−" : "") + nf(Math.abs(n));
}

function deltaNote(d, key) {
  if (!d) return "";
  const days = d.days === 1 ? "за сутки" : `за ${d.days} дн.`;
  return `${signed(d[key])} ${days}`;
}

function statNumber(title, value, delta, key) {
  const box = el("div", "stat");
  box.append(el("div", "stat-title", title));
  box.append(el("div", "stat-value", nf(value)));
  const d = deltaNote(delta, key);
  if (d) {
    const n = Number((delta || {})[key] || 0);
    box.append(el("div", "stat-delta" + (n > 0 ? " up" : n < 0 ? " down" : ""), d));
  }
  return box;
}

/* График подписчиков. Рисуем сами: ради одной линии тянуть библиотеку
   незачем, а срезов за месяц максимум тридцать. */
function sparkline(series) {
  const pts = series.map((s) => Number(s.subscribers || 0));
  const wrap = el("div", "spark");
  if (pts.length < 2) return wrap;
  const W = 320, H = 64, P = 4;
  const min = Math.min(...pts), max = Math.max(...pts);
  const span = max - min || 1;
  // По горизонтали — реальные дни, а не номера срезов: ночной прогон
  // иногда пропускает день, и равномерный шаг врал бы про скорость роста.
  const t = series.map((s) => Date.parse(s.day + "T00:00:00Z"));
  const t0 = t[0], tSpan = t[t.length - 1] - t0 || 1;
  const x = (i) => P + ((t[i] - t0) * (W - P * 2)) / tSpan;
  const y = (v) => H - P - ((v - min) / span) * (H - P * 2);
  const line = pts.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${P},${H} ${line} ${W - P},${H}`;
  wrap.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">` +
    `<polygon points="${area}" class="spark-area"/>` +
    `<polyline points="${line}" class="spark-line"/>` +
    `<circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(pts[pts.length - 1]).toFixed(1)}" r="3" class="spark-dot"/>` +
    `</svg>`;
  const ends = el("div", "spark-ends");
  ends.append(el("span", null, `${series[0].day.slice(5)} · ${nf(pts[0])}`));
  ends.append(el("span", null, `${series[series.length - 1].day.slice(5)} · ${nf(pts[pts.length - 1])}`));
  wrap.append(ends);
  return wrap;
}

function videoRow(v) {
  const row = el("div", "vid");
  const head = el("div", "vid-head");
  const link = el("a", "vid-title", v.title || "без названия");
  link.href = `https://www.youtube.com/watch?v=${v.id}`;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  head.append(link);
  row.append(head);
  const bits = [`${nf(v.views)} просмотров`];
  if (v.views_day !== null && v.views_day !== undefined) bits.push(`${signed(v.views_day)} с прошлого среза`);
  if (v.likes) bits.push(`${nf(v.likes)} лайков`);
  bits.push(v.published);
  row.append(el("div", "vid-meta", bits.join(" · ")));
  return row;
}

function channelBlock(c) {
  const box = el("div", "yt-chan");
  const head = el("div", "yt-chan-head");
  head.append(el("b", null, c.channel_title || c.label));
  head.append(el("span", "muted small", c.label));
  box.append(head);

  if (!c.days) {
    box.append(el("p", "muted small", c.note || "срезов ещё нет"));
    return box;
  }

  const stats = el("div", "stats");
  stats.append(statNumber("Подписчиков", c.totals.subscribers, c.delta_day, "subscribers"));
  stats.append(statNumber("Просмотров", c.totals.views, c.delta_day, "views"));
  stats.append(statNumber("Роликов", c.totals.videos, c.delta_day, "videos"));
  box.append(stats);

  if (c.delta_week) {
    box.append(el("div", "muted small",
      `За неделю: ${signed(c.delta_week.subscribers)} подписчиков, ` +
      `${signed(c.delta_week.views)} просмотров, ${signed(c.delta_week.videos)} роликов ` +
      `(с ${c.delta_week.from})`));
  }

  box.append(sparkline(c.series));

  if (c.shorts.count) {
    box.append(el("div", "muted small",
      `Шортсов ${nf(c.shorts.count)} · медиана ${nf(c.shorts.median)} просмотров · лучший ${nf(c.shorts.best)}`));
  }

  if (c.recent.length) {
    const list = el("div", "vids");
    list.append(el("div", "vids-title", "Ролики за неделю"));
    c.recent.forEach((v) => list.append(videoRow(v)));
    box.append(list);
  }

  const taken = (c.taken_at || "").replace("T", " ").slice(0, 16);
  if (taken) box.append(el("div", "muted small", `срез от ${taken}`));
  if (c.note) box.append(el("div", "muted small", c.note));
  return box;
}

function renderStats(channels) {
  const box = $("#yt-stats");
  box.textContent = "";
  if (!channels.length) {
    box.textContent = "Каналы не настроены.";
    return;
  }
  channels.forEach((c) => box.append(channelBlock(c)));
}

async function loadStats() {
  const box = $("#yt-stats");
  if (!box) return;
  box.textContent = "Загружаю…";
  try {
    const d = await api("/api/youtube/stats");
    renderStats(d.channels || []);
  } catch (e) {
    if (e.message !== "auth") box.textContent = `Не удалось загрузить: ${e.message}`;
  }
}

$("#btn-stats-refresh")?.addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  btn.textContent = "Снимаю…";
  try {
    const d = await api("/api/youtube/stats/refresh", { method: "POST" });
    renderStats(d.channels || []);
    const bad = Object.entries(d.result || {}).filter(([, v]) => v !== "ок");
    if (bad.length) toast(bad.map(([k, v]) => `${k}: ${v}`).join("; "), "bad");
    else toast("Срез снят");
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "Снять срез сейчас";
  }
});

/* ── старт ───────────────────────────────────────────────────────────── */

(async function boot() {
  restoreTheme();
  try {
    await api("/api/me");
    $("#app").hidden = false;
    $("#login").hidden = true;
    loadKeys();
    loadGenSettings();
    loadStats();
  } catch (e) {
    if (e.message !== "auth") {
      $("#app").hidden = false;
      $("#login").hidden = true;
      toast(e.message, "bad");
    }
  }
})();
