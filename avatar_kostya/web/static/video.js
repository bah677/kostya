/* Киностудия — ролики для YouTube. Вход общий с контент-заводом: кука
   ставится там, здесь только проверяется.

   Правило страницы: ничего не сохраняется молча. Любое действие, которое
   меняет то, что выйдет на канал, сначала показывает «было → стало» и
   ждёт подтверждения. Ошибка здесь видна только завтра утром, когда ролик
   уже опубликован, поэтому лишний вопрос дешевле неверной правки. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

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

const nf = (v) => Number(v || 0).toLocaleString("ru-RU");

function plural(n, one, few, many) {
  const a = Math.abs(Number(n) || 0);
  if (a % 10 === 1 && a % 100 !== 11) return one;
  if (a % 10 >= 2 && a % 10 <= 4 && !(a % 100 >= 12 && a % 100 <= 14)) return few;
  return many;
}

function signed(v) {
  const n = Number(v || 0);
  return (n > 0 ? "+" : n < 0 ? "−" : "") + nf(Math.abs(n));
}

function short(text, max = 90) {
  const t = String(text ?? "").trim();
  if (!t) return "пусто";
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
}

/* Описание героини — абзац на четыреста знаков, и правка в нём обычно
   в одном месте. Обрезка по первым девяноста знакам показывала бы два
   одинаковых огрызка: «было» и «стало» неотличимы. Поэтому для длинных
   текстов показываем окно вокруг того, что действительно поменялось. */
function diffPair(before, after, max = 90, ctx = 28) {
  const a = String(before ?? "").trim(), b = String(after ?? "").trim();
  if (a.length <= max && b.length <= max) return [short(a, max), short(b, max)];
  let p = 0;
  const n = Math.min(a.length, b.length);
  while (p < n && a[p] === b[p]) p += 1;
  let s = 0;
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s += 1;
  const start = Math.max(0, p - ctx);
  const cut = (t, end) => {
    const head = start > 0 ? "…" : "";
    const stop = Math.min(t.length, end + ctx);
    return head + t.slice(start, stop) + (stop < t.length ? "…" : "");
  };
  return [
    short(cut(a, a.length - s), max + 60) || "пусто",
    short(cut(b, b.length - s), max + 60) || "пусто",
  ];
}

/* ── подтверждение ───────────────────────────────────────────────────── */

let _confirmResolve = null;

function closeConfirm(answer) {
  const dlg = $("#confirm-dialog");
  if (dlg.open) dlg.close();
  const fn = _confirmResolve;
  _confirmResolve = null;
  if (fn) fn(answer);
}

/* rows: [{label, before, after}] либо [{label, note}] для простых действий. */
function askConfirm({ kicker = "Подтверждение", title, lead = "", rows = [], okText = "Применить", danger = false }) {
  const body = $("#confirm-body");
  body.textContent = "";
  rows.forEach((r) => {
    const row = el("div", "cdiff");
    row.append(el("div", "cdiff-label", r.label));
    if (r.note !== undefined) {
      row.append(el("div", "cdiff-note", r.note));
    } else {
      const [was, now] = diffPair(r.before, r.after);
      const pair = el("div", "cdiff-pair");
      pair.append(el("span", "cdiff-was", was));
      pair.append(el("span", "cdiff-arrow", "→"));
      pair.append(el("span", "cdiff-now", now));
      row.append(pair);
    }
    body.append(row);
  });
  $("#confirm-kicker").textContent = kicker;
  $("#confirm-title").textContent = title;
  $("#confirm-lead").textContent = lead;
  $("#confirm-lead").hidden = !lead;
  const ok = $("#confirm-yes");
  ok.textContent = okText;
  ok.classList.toggle("danger", !!danger);
  const dlg = $("#confirm-dialog");
  dlg.showModal();
  ok.focus();
  return new Promise((resolve) => {
    _confirmResolve = resolve;
  });
}

$("#confirm-yes")?.addEventListener("click", () => closeConfirm(true));
$("#confirm-no")?.addEventListener("click", () => closeConfirm(false));
$("#confirm-dialog")?.addEventListener("cancel", (e) => {
  e.preventDefault();
  closeConfirm(false);
});

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

/* ── разделы ─────────────────────────────────────────────────────────── */
/* Четыре раздела на одной странице тонули друг в друге: настройки,
   которые трогают каждый день, лежали вровень с ключами, которые трогают
   раз в неделю. Теперь видно по одному. */

const SECTION_KEY = "studio.section";

function showSection(name) {
  $$("#studio-nav button").forEach((b) => b.classList.toggle("active", b.dataset.sec === name));
  $$(".pane").forEach((p) => { p.hidden = p.dataset.pane !== name; });
  try {
    localStorage.setItem(SECTION_KEY, name);
  } catch (err) {
    /* приватный режим */
  }
}

$("#studio-nav")?.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-sec]");
  if (btn) showSection(btn.dataset.sec);
});

function markSectionDirty(name, dirty) {
  const btn = $(`#studio-nav button[data-sec="${name}"]`);
  if (btn) btn.classList.toggle("dirty", !!dirty);
}

function restoreSection() {
  let saved = "night";
  try {
    saved = localStorage.getItem(SECTION_KEY) || "night";
  } catch (err) {
    /* приватный режим */
  }
  showSection($(`#studio-nav button[data-sec="${saved}"]`) ? saved : "night");
}

/* ── что выйдет ночью ────────────────────────────────────────────────── */

function renderPlan(plan) {
  if (!plan) return;
  $("#plan-headline").textContent = plan.headline;
  const box = $("#plan-channels");
  box.textContent = "";
  (plan.channels || []).forEach((c) => {
    const card = el("div", "plan-chan" + (c.idle ? " idle" : ""));
    card.append(el("div", "plan-chan-title", c.title));
    card.append(el("div", "plan-chan-sum", c.summary));
    const when = [];
    if (c.premieres) when.push(`молитвы ${c.premieres}`);
    if (c.talk_premieres) when.push(`разговорные ${c.talk_premieres}`);
    if (when.length) card.append(el("div", "plan-chan-when", `${when.join(" · ")} ${c.tz}`));
    box.append(card);
  });
  if (plan.wide) {
    const card = el("div", "plan-chan");
    card.append(el("div", "plan-chan-title", "Горизонтальные 16:9"));
    card.append(el("div", "plan-chan-sum", `${plan.wide} за ночь`));
    box.append(card);
  }
}

$("#btn-plan-reload")?.addEventListener("click", () => loadGenSettings());

/* ── ключи YouTube ───────────────────────────────────────────────────── */

let _keysLoading = false;

function keyCard(k) {
  const ok = k.alive && !k.error;
  const card = el("div", "yt-key" + (ok ? " ok" : " bad"));

  const head = el("div", "yt-key-head");
  head.append(el("b", null, k.label));
  head.append(el("span", "yt-key-state", ok ? "ключ жив" : "нужен перевыпуск"));
  card.append(head);

  if (k.channel_title) card.append(el("div", "muted small", k.channel_title));
  if (ok && k.subscribers !== null && k.subscribers !== undefined) {
    card.append(el("div", "yt-key-stats",
      `${nf(k.subscribers)} подписчиков · ${nf(k.videos)} роликов · ${nf(k.views)} просмотров`));
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
  if (!box || _keysLoading) return;
  _keysLoading = true;
  box.textContent = "Проверяю ключи…";
  try {
    const data = await api("/api/youtube/keys");
    box.textContent = "";
    box.classList.remove("muted");
    const keys = data.keys || [];
    if (!keys.length) {
      box.textContent = "Каналы не настроены.";
      return;
    }
    keys.forEach((k) => box.append(keyCard(k)));
  } catch (e) {
    if (e.message !== "auth") box.textContent = `Не удалось проверить: ${e.message}`;
  } finally {
    _keysLoading = false;
  }
}

let _keyLang = "";
let _keyLabel = "";

async function openKeyDialog(k) {
  _keyLang = k.lang;
  _keyLabel = `${k.label}${k.channel_title ? ` · ${k.channel_title}` : ""}`;
  const dlg = $("#ytkey-dialog");
  $("#ytkey-title").textContent = _keyLabel;
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
  const go = await askConfirm({
    kicker: "Перевыпуск ключа",
    title: "Заменить ключ канала?",
    lead: "Старый токен перестанет работать сразу после замены.",
    rows: [{ label: _keyLabel, note: "выдаём новый доступ по присланному коду" }],
    okText: "Заменить ключ",
    danger: true,
  });
  if (!go) return;
  const btn = $("#ytkey-save");
  btn.disabled = true;
  btn.textContent = "Сохраняю…";
  try {
    const d = await api(`/api/youtube/keys/${_keyLang}/exchange`, {
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

/* ── ночной прогон ───────────────────────────────────────────────────── */
/* Правки живут в файле рядом с данными, langs.setting читает его первым —
   поэтому сохранение действует на ближайший прогон без перезапуска. */

let _genFields = [];

function effectiveOf(f, value) {
  return (value || "").trim() || f.env || f.default || "—";
}

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
    markGenDirty();
  });
  input.addEventListener("change", markGenDirty);
  wrap.append(input);

  if (f.hint) wrap.append(el("div", "gen-hint", f.hint));
  wrap.append(el("div", "gen-env", `сейчас действует: ${effectiveOf(f, f.value)}`));
  return wrap;
}

function renderGenSettings(data) {
  _genFields = data.fields || [];
  renderPlan(data.plan);
  const order = data.groups || [];
  const box = $("#gen-settings");
  box.textContent = "";
  box.classList.remove("muted");
  const names = [];
  _genFields.forEach((f) => { if (!names.includes(f.group)) names.push(f.group); });
  names.sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  names.forEach((name) => {
    const node = el("div", "gen-group");
    node.append(el("h3", null, name));
    const grid = el("div", "gen-fields");
    _genFields.filter((f) => f.group === name).forEach((f) => grid.append(genInput(f)));
    node.append(grid);
    box.append(node);
  });
  markGenDirty();
}

function genValues() {
  const out = {};
  $$("#gen-settings [data-key]").forEach((n) => { out[n.dataset.key] = n.value || ""; });
  return out;
}

function genDiff() {
  const values = genValues();
  const rows = [];
  _genFields.forEach((f) => {
    const now = values[f.key] ?? "";
    if ((now || "") === (f.value || "")) return;
    rows.push({
      label: `${f.group} · ${f.title}`,
      before: effectiveOf(f, f.value),
      after: effectiveOf(f, now),
    });
  });
  return rows;
}

function markGenDirty() {
  const n = genDiff().length;
  const btn = $("#btn-gen-save");
  if (btn) btn.disabled = n === 0;
  const note = $("#gen-dirty");
  if (note) note.textContent = n ? `${n} ${plural(n, "правка", "правки", "правок")}` : "";
  markSectionDirty("night", n);
}

async function loadGenSettings() {
  const box = $("#gen-settings");
  if (!box) return;
  box.textContent = "Загружаю…";
  try {
    renderGenSettings(await api("/api/gen-settings"));
  } catch (e) {
    if (e.message !== "auth") box.textContent = `Не удалось загрузить: ${e.message}`;
  }
}

$("#btn-gen-save")?.addEventListener("click", async () => {
  const rows = genDiff();
  if (!rows.length) {
    toast("Нечего сохранять");
    return;
  }
  const go = await askConfirm({
    kicker: "Ночной прогон",
    title: `Применить ${rows.length} ${plural(rows.length, "правку", "правки", "правок")}?`,
    lead: "Подействует на ближайший ночной прогон.",
    rows,
    okText: "Сохранить",
  });
  if (!go) return;
  const btn = $("#btn-gen-save");
  btn.disabled = true;
  btn.textContent = "Сохраняю…";
  try {
    renderGenSettings(await api("/api/gen-settings", {
      method: "PUT",
      body: JSON.stringify({ values: genValues() }),
    }));
    const saved = $("#gen-saved");
    saved.hidden = false;
    setTimeout(() => { saved.hidden = true; }, 2500);
    toast("Сохранено — подхватится ближайшим прогоном");
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.textContent = "Сохранить";
    markGenDirty();
  }
});

$("#btn-gen-reload")?.addEventListener("click", () => loadGenSettings());

/* ── статистика каналов ──────────────────────────────────────────────── */
/* Цифры из ночных срезов: YouTube отдаёт только накопительные итоги, и
   прирост виден лишь как разница двух срезов. В API отсюда не ходим —
   страница открывается сразу и не тратит квоту. */

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
  const link = el("a", "vid-title", v.title || "без названия");
  link.href = `https://www.youtube.com/watch?v=${v.id}`;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  row.append(link);
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
  box.classList.remove("muted");
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
    renderStats((await api("/api/youtube/stats")).channels || []);
  } catch (e) {
    if (e.message !== "auth") box.textContent = `Не удалось загрузить: ${e.message}`;
  }
}

$("#btn-stats-refresh")?.addEventListener("click", async (ev) => {
  const go = await askConfirm({
    kicker: "Статистика",
    title: "Снять свежий срез?",
    lead: "Сходим в YouTube по каждому каналу и перепишем сегодняшний срез.",
    rows: [{ label: "Сегодняшний срез", note: "заменится свежими цифрами" }],
    okText: "Снять срез",
  });
  if (!go) return;
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

/* ── герои и сцены ───────────────────────────────────────────────────── */
/* ДНК героев — не настройка, а то, что держит узнаваемость: один и тот же
   абзац уходит в каждый кадр. Поэтому правку видно рядом с исходным
   текстом из кода и её можно откатить одним нажатием. */

let _cast = null;

function dnaField(title, hint, f) {
  const wrap = el("div", "dna-field");
  const head = el("div", "dna-head");
  head.append(el("label", null, title));
  const area = el("textarea", "field");
  if (f.overridden) {
    const back = el("button", "link small", "вернуть как в коде");
    back.type = "button";
    back.addEventListener("click", () => {
      area.value = f.default;
      wrap.classList.add("changed");
      markCastDirty();
    });
    head.append(back);
  }
  wrap.append(head);
  area.rows = 4;
  area.value = f.value || "";
  area.dataset.dna = f.key;
  area.addEventListener("input", () => {
    wrap.classList.toggle("changed", area.value !== (f.value || ""));
    markCastDirty();
  });
  wrap.append(area);
  if (hint) wrap.append(el("div", "gen-hint", hint));
  if (f.overridden) wrap.append(el("div", "gen-env", "правка из студии, в коде другой текст"));
  return wrap;
}

function sceneCard(sc) {
  const card = el("div", "scene");
  card.dataset.key = sc.key;

  const shelf = el("div", "scene-frames");
  if (sc.frames.length) {
    sc.frames.forEach((fr) => {
      const a = el("a", "frame");
      a.href = fr.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.title = `${fr.name} · ${fr.kb} КБ`;
      const img = el("img");
      img.src = fr.thumb || fr.url;
      img.alt = sc.key;
      img.loading = "lazy";
      a.append(img);
      shelf.append(a);
    });
  } else {
    shelf.append(el("div", "frame empty", "нет кадра"));
  }
  const draw = el("button", "btn small", "Дорисовать");
  draw.type = "button";
  draw.title = `дубль №${sc.next_variant}`;
  draw.addEventListener("click", () => drawFrame(sc.key, draw, sc.next_variant));
  shelf.append(draw);
  card.append(shelf);

  const fields = el("div", "scene-fields");
  const top = el("div", "scene-top");
  const key = el("input", "field key");
  key.value = sc.key;
  key.dataset.f = "key";
  key.placeholder = "ключ кадра";
  top.append(key);
  const who = el("select", "field");
  who.dataset.f = "who";
  (_cast?.who_values || []).forEach((w) => {
    const o = el("option", null, w.label);
    o.value = w.key;
    who.append(o);
  });
  who.value = sc.who;
  top.append(who);
  const kill = el("button", "icon-btn", "×");
  kill.type = "button";
  kill.title = "убрать кадр из списка";
  kill.addEventListener("click", () => { card.remove(); markCastDirty(); });
  top.append(kill);
  fields.append(top);

  const shot = el("input", "field");
  shot.value = sc.shot;
  shot.dataset.f = "shot";
  shot.placeholder = "ракурс и крупность — идут первыми в промпте";
  fields.append(shot);

  const prompt = el("textarea", "field");
  prompt.rows = 2;
  prompt.value = sc.prompt;
  prompt.dataset.f = "prompt";
  prompt.placeholder = "что происходит в кадре";
  fields.append(prompt);

  const topics = el("input", "field");
  topics.value = (sc.topics || []).join(", ");
  topics.dataset.f = "topics";
  topics.placeholder = "темы через запятую; пусто — кадр подходит к любой";
  fields.append(topics);

  card.append(fields);
  $$("[data-f]", card).forEach((n) => {
    n.addEventListener("input", markCastDirty);
    n.addEventListener("change", markCastDirty);
  });
  return card;
}

function renderCast(d) {
  _cast = d;
  const dna = $("#cast-dna");
  dna.classList.remove("muted");
  dna.textContent = "";
  dna.append(dnaField("Общая гамма", "Свет, цвет и настроение — общие для всех кадров.", d.style));
  dna.append(dnaField("Героиня", "Та самая зрительница: обычное лицо, обычная одежда.", d.woman));
  dna.append(dnaField("Иисус", "Иконографическая традиция, без портретов в упор.", d.jesus));

  const box = $("#cast-scenes");
  box.textContent = "";
  d.scenes.forEach((sc) => box.append(sceneCard(sc)));

  const price = Number(d.price || 0).toFixed(2);
  $("#cast-bank-note").textContent =
    `${d.frames_total} в банке · ${d.model}, примерно $${price} за кадр` +
    (d.scenes_overridden ? " · список правлен из студии" : "");
  markCastDirty();
}

function collectCast() {
  const out = { scenes: [] };
  $$("#cast-dna [data-dna]").forEach((n) => { out[n.dataset.dna] = n.value || ""; });
  $$("#cast-scenes .scene").forEach((card) => {
    const get = (f) => ($("[data-f=\"" + f + "\"]", card)?.value || "").trim();
    out.scenes.push({
      key: get("key"),
      who: get("who"),
      shot: get("shot"),
      prompt: get("prompt"),
      topics: get("topics").split(",").map((t) => t.trim()).filter(Boolean),
    });
  });
  return out;
}

function castDiff() {
  if (!_cast) return [];
  const now = collectCast();
  const rows = [];
  [["style", "Общая гамма"], ["woman", "Героиня"], ["jesus", "Иисус"]].forEach(([k, label]) => {
    if ((now[k] || "") !== (_cast[k].value || "")) {
      rows.push({ label, before: _cast[k].value, after: now[k] });
    }
  });
  const was = new Map(_cast.scenes.map((s) => [s.key, s]));
  const seen = new Set();
  now.scenes.forEach((s) => {
    seen.add(s.key);
    const old = was.get(s.key);
    if (!old) {
      rows.push({ label: `Кадр ${s.key || "без ключа"}`, note: "новый кадр" });
      return;
    }
    [["who", "кто в кадре"], ["shot", "ракурс"], ["prompt", "что происходит"]].forEach(([f, name]) => {
      if ((s[f] || "") !== (old[f] || "")) {
        rows.push({ label: `${s.key} · ${name}`, before: old[f], after: s[f] });
      }
    });
    const a = (old.topics || []).join(", "), b = (s.topics || []).join(", ");
    if (a !== b) rows.push({ label: `${s.key} · темы`, before: a, after: b });
  });
  _cast.scenes.forEach((s) => {
    if (!seen.has(s.key)) {
      rows.push({
        label: `Кадр ${s.key}`,
        note: `убран из списка; ${s.frames.length ? "нарисованные кадры останутся в банке" : "кадров в банке не было"}`,
      });
    }
  });
  return rows;
}

function markCastDirty() {
  const n = castDiff().length;
  const btn = $("#btn-cast-save");
  if (btn) btn.disabled = n === 0;
  const note = $("#cast-dirty");
  if (note) note.textContent = n ? `${n} ${plural(n, "изменение", "изменения", "изменений")}` : "";
  markSectionDirty("cast", n);
}

async function loadCast() {
  const box = $("#cast-dna");
  if (!box) return;
  box.textContent = "Загружаю…";
  try {
    renderCast(await api("/api/cast"));
  } catch (e) {
    if (e.message !== "auth") box.textContent = `Не удалось загрузить: ${e.message}`;
  }
}

async function drawFrame(key, btn, variant) {
  const price = Number(_cast?.price || 0.04).toFixed(2);
  const go = await askConfirm({
    kicker: "Банк кадров",
    title: "Дорисовать кадр?",
    lead: "Уже нарисованное не трогаем — добавится новый дубль.",
    rows: [
      { label: "Кадр", note: `${key}, дубль №${variant}` },
      { label: "Стоимость", note: `примерно $${price}, до полутора минут` },
    ],
    okText: "Дорисовать",
  });
  if (!go) return;
  btn.disabled = true;
  btn.textContent = "Рисую…";
  try {
    const d = await api("/api/cast/generate", {
      method: "POST",
      body: JSON.stringify({ key }),
    });
    toast(`${d.frame}: ${d.status}`);
    loadCast();
  } catch (e) {
    toast(e.message, "bad");
    btn.disabled = false;
    btn.textContent = "Дорисовать";
  }
}

$("#btn-cast-save")?.addEventListener("click", async () => {
  const rows = castDiff();
  if (!rows.length) {
    toast("Нечего сохранять");
    return;
  }
  const touchesDna = rows.some((r) => ["Общая гамма", "Героиня", "Иисус"].includes(r.label));
  const go = await askConfirm({
    kicker: "Герои и сцены",
    title: `Применить ${rows.length} ${plural(rows.length, "изменение", "изменения", "изменений")}?`,
    lead: touchesDna
      ? "Описание героя уходит в каждый новый кадр: нарисованные раньше останутся прежними, и банк станет разнородным."
      : "Подействует на ближайший ночной прогон.",
    rows,
    okText: "Сохранить",
    danger: touchesDna,
  });
  if (!go) return;
  const btn = $("#btn-cast-save");
  btn.disabled = true;
  btn.textContent = "Сохраняю…";
  try {
    renderCast(await api("/api/cast", { method: "PUT", body: JSON.stringify(collectCast()) }));
    const saved = $("#cast-saved");
    saved.hidden = false;
    setTimeout(() => { saved.hidden = true; }, 2500);
    toast("Сохранено — подхватится ближайшим прогоном");
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.textContent = "Сохранить";
    markCastDirty();
  }
});

$("#btn-cast-reload")?.addEventListener("click", () => loadCast());

$("#btn-cast-add")?.addEventListener("click", () => {
  if (!_cast) return;
  const card = sceneCard({
    key: "", who: "woman", shot: "", prompt: "", topics: [],
    frames: [], next_variant: 1,
  });
  $("#cast-scenes").append(card);
  $('[data-f="key"]', card).focus();
  markCastDirty();
});

/* Несохранённое не должно теряться молча: закрыть вкладку с правкой,
   которую не применили, — та же потеря, что сохранить лишнее. */
if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (e) => {
    if (genDiff().length || castDiff().length) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
}

/* ── старт ───────────────────────────────────────────────────────────── */

(async function boot() {
  restoreTheme();
  restoreSection();
  try {
    await api("/api/me");
    $("#app").hidden = false;
    $("#login").hidden = true;
    loadGenSettings();
    loadCast();
    loadStats();
    loadKeys();
  } catch (e) {
    if (e.message !== "auth") {
      $("#app").hidden = false;
      $("#login").hidden = true;
      toast(e.message, "bad");
    }
  }
})();
