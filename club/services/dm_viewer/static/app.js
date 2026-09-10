(() => {
  const TAGS_CLUB = [
    ["all", "все"],
    ["active", "активная"],
    ["expired", "просрочка"],
    ["no_license", "без подписки"],
    ["blocked", "заблокировал бота"],
    ["has_dm", "есть личка"],
    ["onboarding", "онбординг"],
    ["greeter", "встречающие"],
    ["banned", "бан"],
  ];
  const TAGS_BIBLIA = [
    ["all", "все"],
    ["active", "активная"],
    ["expired", "просрочка"],
    ["no_license", "без подписки"],
    ["blocked", "заблокировал бота"],
    ["has_dm", "есть личка"],
    ["onboarding", "без онбординга"],
    ["banned", "бан"],
  ];

  const state = {
    bot: "club",
    tag: "all",
    sort: "last_activity",
    q: "",
    offset: 0,
    limit: 50,
    total: 0,
    users: [],
    selected: null,
    loadingUsers: false,
    loadingMsg: false,
    hasMoreUsers: true,
    messages: [],
    hasMoreMsg: false,
    nextBeforeId: null,
  };

  const el = {
    chips: document.getElementById("chips"),
    search: document.getElementById("search"),
    sort: document.getElementById("sort"),
    list: document.getElementById("user-list"),
    meta: document.getElementById("list-meta"),
    head: document.getElementById("chat-head"),
    body: document.getElementById("chat-body"),
    bubbles: document.getElementById("bubbles"),
    loadOlder: document.getElementById("load-older"),
    botSwitch: document.getElementById("bot-switch"),
    botSub: document.getElementById("bot-sub"),
  };

  function tagsForBot() {
    return state.bot === "biblia" ? TAGS_BIBLIA : TAGS_CLUB;
  }

  function botLabel() {
    return state.bot === "biblia" ? "БиблияБот" : "Клуб";
  }

  function apiUrl(path, params) {
    const u = new URL(path, window.location.href);
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") u.searchParams.set(k, v);
      });
    }
    return u;
  }

  async function api(path, params) {
    const res = await fetch(apiUrl(path, { bot: state.bot, ...(params || {}) }));
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function initials(name) {
    const p = String(name || "?").trim().split(/\s+/);
    return ((p[0]?.[0] || "?") + (p[1]?.[0] || "")).toUpperCase();
  }

  function fmtDT(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function fmtDay(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  }

  function fmtTime(iso) {
    const d = new Date(iso);
    return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }

  function tagLabel(t) {
    if (t === "active") return "активная";
    if (t === "expired") return "просрочка";
    if (t === "no_license") return "без подписки";
    if (t === "banned") return "бан";
    if (t === "blocked") return "блок бота";
    if (t === "has_dm") return "личка";
    if (t === "onboarding") return "онбординг";
    if (t === "greeter") return "встречающий";
    if (t.startsWith("origin:")) return t.slice(7);
    if (t.startsWith("touch:")) return t.slice(6);
    return t;
  }

  function syncBotUi() {
    el.botSwitch.querySelectorAll("[data-bot]").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.bot === state.bot);
    });
    el.botSub.textContent =
      state.bot === "biblia" ? "библия · reports" : "клуб · reports";
    document.title = `Личка · ${botLabel()}`;
  }

  function renderChips() {
    const allowed = new Set(tagsForBot().map(([id]) => id));
    if (!allowed.has(state.tag)) state.tag = "all";
    el.chips.innerHTML = tagsForBot()
      .map(
        ([id, label]) =>
          `<button type="button" class="chip${state.tag === id ? " on" : ""}" data-tag="${id}">${label}</button>`
      )
      .join("");
  }

  function renderUsers(append) {
    const html = state.users
      .map((u) => {
        const uname = u.username ? `@${u.username}` : u.user_id;
        const tags = (u.tags || [])
          .filter((t) => !t.startsWith("touch:") && !t.startsWith("origin:"))
          .slice(0, 4)
          .map((t) => `<span class="tag ${esc(t)}">${esc(tagLabel(t))}</span>`)
          .join("");
        const on = state.selected && state.selected.user_id === u.user_id ? " on" : "";
        return `<div class="user${on}" data-id="${u.user_id}">
          <div class="avatar">${esc(initials(u.display_name))}</div>
          <div>
            <div class="u-name">${esc(u.display_name)}</div>
            <div class="u-sub">${esc(uname)} · dm ${u.dm_count} · ${esc(fmtDT(u.last_dm_at || u.last_activity))}${
              u.bot_blocked_at ? ` · блок ${esc(fmtDT(u.bot_blocked_at))}` : ""
            }</div>
            <div class="u-tags">${tags}</div>
          </div>
        </div>`;
      })
      .join("");

    if (append) el.list.insertAdjacentHTML("beforeend", html);
    else el.list.innerHTML = html || `<div class="muted">Никого не нашлось</div>`;

    el.meta.textContent = `${botLabel()}: ${state.users.length} из ${state.total}`;
  }

  async function loadUsers(reset) {
    if (state.loadingUsers) return;
    if (reset) {
      state.offset = 0;
      state.users = [];
      state.hasMoreUsers = true;
    }
    if (!state.hasMoreUsers && !reset) return;
    state.loadingUsers = true;
    try {
      const data = await api("api/users", {
        q: state.q,
        tag: state.tag,
        sort: state.sort,
        offset: state.offset,
        limit: state.limit,
      });
      state.total = data.total;
      state.users = reset ? data.users : state.users.concat(data.users);
      state.offset = state.users.length;
      state.hasMoreUsers = state.users.length < data.total;
      renderUsers(!reset);
    } catch (e) {
      el.list.innerHTML = `<div class="muted">Ошибка: ${esc(e.message)}</div>`;
    } finally {
      state.loadingUsers = false;
    }
  }

  function renderHead(u) {
    if (!u) {
      el.head.innerHTML = `<div class="empty-hint">Выбери человека слева</div>`;
      return;
    }
    const uname = u.username ? `@${u.username}` : "";
    const lic =
      u.license_status === "active"
        ? `подписка до ${fmtDT(u.license_expires_at)}`
        : u.license_status === "expired"
          ? `просрочена ${fmtDT(u.license_expires_at)}`
          : "без подписки";
    const blocked = u.bot_blocked_at
      ? ` · блок бота с ${fmtDT(u.bot_blocked_at)}`
      : u.is_active === false
        ? " · бот недоступен (is_active=false)"
        : "";
    el.head.innerHTML = `<div class="name">${esc(u.display_name)} ${esc(uname)}</div>
      <div class="info">${esc(botLabel())} · id ${u.user_id} · ${esc(lic)}${esc(blocked)} · сообщений в личке: ${u.dm_count}</div>`;
  }

  function renderMessages(keepScroll) {
    const prevHeight = el.body.scrollHeight;
    const prevTop = el.body.scrollTop;

    let lastDay = "";
    const parts = [];
    for (const m of state.messages) {
      const day = fmtDay(m.created_at);
      if (day !== lastDay) {
        parts.push(`<div class="day">${esc(day)}</div>`);
        lastDay = day;
      }
      parts.push(
        `<div class="row ${m.sender}"><div class="bubble">${esc(m.content)}<span class="time">${esc(fmtTime(m.created_at))}</span></div></div>`
      );
    }

    const olderLabel = state.loadingMsg
      ? "загрузка…"
      : state.hasMoreMsg
        ? "↑ ещё старше"
        : "";
    el.loadOlder.hidden = !state.hasMoreMsg && !state.loadingMsg;
    el.loadOlder.textContent = olderLabel || "↑ ещё старше";

    [...el.bubbles.children].forEach((node) => {
      if (node !== el.loadOlder) node.remove();
    });
    if (parts.length) {
      el.loadOlder.insertAdjacentHTML("afterend", parts.join(""));
    } else if (!state.loadingMsg) {
      el.loadOlder.insertAdjacentHTML(
        "afterend",
        `<div class="muted">В личке пока пусто</div>`
      );
    }

    if (keepScroll) {
      el.body.scrollTop = el.body.scrollHeight - prevHeight + prevTop;
    } else {
      el.body.scrollTop = el.body.scrollHeight;
    }
  }

  async function loadMessages(older) {
    if (!state.selected || state.loadingMsg) return;
    if (older && !state.hasMoreMsg) return;
    state.loadingMsg = true;
    el.loadOlder.hidden = false;
    el.loadOlder.textContent = "загрузка…";
    try {
      const params = { limit: 20 };
      if (older && state.nextBeforeId) params.before_id = state.nextBeforeId;
      const data = await api(`api/users/${state.selected.user_id}/messages`, params);
      if (older) {
        state.messages = data.messages.concat(state.messages);
      } else {
        state.messages = data.messages;
      }
      state.hasMoreMsg = data.has_more;
      state.nextBeforeId = data.next_before_id;
      renderMessages(Boolean(older));
    } catch (e) {
      [...el.bubbles.children].forEach((node) => {
        if (node !== el.loadOlder) node.remove();
      });
      el.loadOlder.insertAdjacentHTML(
        "afterend",
        `<div class="muted">Ошибка: ${esc(e.message)}</div>`
      );
    } finally {
      state.loadingMsg = false;
      el.loadOlder.hidden = !state.hasMoreMsg;
      el.loadOlder.textContent = "↑ ещё старше";
    }
  }

  async function selectUser(userId) {
    let u = state.users.find((x) => x.user_id === userId);
    if (!u) u = await api(`api/users/${userId}`);
    state.selected = u;
    renderUsers(false);
    renderHead(u);
    state.messages = [];
    state.hasMoreMsg = false;
    state.nextBeforeId = null;
    await loadMessages(false);
  }

  function switchBot(bot) {
    if (bot === state.bot) return;
    state.bot = bot;
    state.selected = null;
    state.messages = [];
    state.hasMoreMsg = false;
    state.nextBeforeId = null;
    state.tag = "all";
    syncBotUi();
    renderChips();
    renderHead(null);
    [...el.bubbles.children].forEach((node) => {
      if (node !== el.loadOlder) node.remove();
    });
    el.loadOlder.hidden = true;
    loadUsers(true);
  }

  syncBotUi();
  renderChips();
  el.chips.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-tag]");
    if (!btn) return;
    state.tag = btn.dataset.tag;
    renderChips();
    loadUsers(true);
  });

  el.botSwitch.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-bot]");
    if (!btn) return;
    switchBot(btn.dataset.bot);
  });

  let searchTimer = null;
  el.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = el.search.value.trim();
      loadUsers(true);
    }, 250);
  });

  el.sort.addEventListener("change", () => {
    state.sort = el.sort.value;
    loadUsers(true);
  });

  el.list.addEventListener("click", (e) => {
    const row = e.target.closest(".user");
    if (!row) return;
    selectUser(Number(row.dataset.id));
  });

  el.list.addEventListener("scroll", () => {
    if (el.list.scrollTop + el.list.clientHeight > el.list.scrollHeight - 80) {
      loadUsers(false);
    }
  });

  el.body.addEventListener("scroll", () => {
    if (el.body.scrollTop < 40 && state.hasMoreMsg && !state.loadingMsg) {
      loadMessages(true);
    }
  });

  el.loadOlder.addEventListener("click", () => loadMessages(true));

  loadUsers(true);
})();
