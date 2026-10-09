// MOG Server - minimal vanilla-JS web UI. No build step, no framework: this
// is a Phase-1 "can actually click through it" UI, not the Steam-like
// redesign planned for later (see docs/TODO.md) - kept intentionally plain
// so it ships inside the single server image with zero extra tooling.
//
// Routing: hash-based, one persistent shell (topbar + sidebar) with three
// pages swapped inside <main> - #/ (games grid), #/game/<id>, #/settings -
// rather than popup modals, so each has its own URL/back-button/reload
// behavior like a normal page.

// The sidebar sticks right under the top bar, so it needs the bar's real height: the buttons in it and the
// font decide that, not a fixed number. Measured now and whenever the bar changes size.
function watchTopbarHeight() {
  const bar = document.querySelector(".topbar");
  if (!bar || !("ResizeObserver" in window)) return;
  const apply = () => {
    const height = bar.offsetHeight;
    if (height > 0) document.documentElement.style.setProperty("--topbar-h", `${height}px`);
  };
  new ResizeObserver(apply).observe(bar);
  apply();
}
watchTopbarHeight();

const POLL_INTERVAL_MS = 2000;
const ACTIVE_INSTALL_STATES = ["detecting", "awaiting_installer", "installing", "streaming"];

// IGDB's age_ratings category/rating enums (confirmed against the official
// Game endpoint docs - https://api-docs.igdb.com/#age-rating). Only the
// values actually seen in practice are mapped; anything else shows its raw
// numeric code rather than guessing.
const IGDB_RATING_CATEGORY = { 1: "ESRB", 2: "PEGI" };
const IGDB_RATING_VALUE = {
  1: "3", 2: "7", 3: "12", 4: "16", 5: "18", // PEGI
  6: "RP", 7: "EC", 8: "E", 9: "E10+", 10: "T", 11: "M", 12: "AO", 13: "CERO A", // ESRB (+CERO)
};

let creds = null; // {user, pass}
let currentUser = null;
let libraries = [];
let games = []; // what the grid shows: the games of the selected library
let gamesAll = null; // every game this user can see, once loaded
let gamesFetchedAt = 0;
const gameCache = new Map(); // games looked up one by one (an active install of a game not loaded yet)
let selectedLibraryId = null;
let activeGame = null;
let installQueue = []; // candidates checked to run one after another
// True only once this page view has actually started an install itself
// (click, or a queue continuation) - distinguishes that from just resuming
// an already-finished session on page load/reload, where installQueue
// defaults to having its first candidate pre-checked but nothing was
// actually started here, so auto-chaining must not kick in.
let queueActive = false;
let pollTimer = null;

function authHeader() {
  return "Basic " + btoa(`${creds.user}:${creds.pass}`);
}

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts,
    headers: {
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      Authorization: authHeader(),
      ...(opts.headers || {}),
    },
  });
  if (resp.status === 401) throw new Error("UNAUTHORIZED");
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail || detail;
    } catch (_) {
      // body wasn't JSON - keep statusText
    }
    const error = new Error(`${resp.status} ${typeof detail === "string" ? detail : detail.code || JSON.stringify(detail)}`);
    error.status = resp.status;
    error.detail = detail;
    throw error;
  }
  if (resp.status === 204) return null;
  return resp.json();
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(1)} ${units[i]}`;
}

// --- Tabs (shared between the game page and the settings page) ---

function activateTab(navSelector, dataAttr, panelPrefix, name) {
  document.querySelectorAll(`${navSelector} .tab-btn`).forEach((b) => {
    b.classList.toggle("active", b.dataset[dataAttr] === name);
  });
  document.querySelectorAll(`[id^="${panelPrefix}-"]`).forEach((panel) => {
    panel.hidden = panel.id !== `${panelPrefix}-${name}`;
  });
}

// Where the page is goes in the address (without a history entry per tab), so a refresh comes back to it.
function setAddress(hash) {
  if (location.hash.replace(/^#\/?/, "") !== hash) history.replaceState(null, "", `#${hash}`);
}

function initTabs(navSelector, dataAttr, panelPrefix, address) {
  document.querySelectorAll(`${navSelector} .tab-btn`).forEach((btn) => {
    btn.addEventListener("click", () => {
      activateTab(navSelector, dataAttr, panelPrefix, btn.dataset[dataAttr]);
      if (address) setAddress(address(btn.dataset[dataAttr]));
    });
  });
}
initTabs("#view-game .tabs", "tab", "tab", (name) => `game/${(location.hash.match(/game\/(\d+)/) || [])[1]}/${name}`);
initTabs("#view-settings .tabs", "settingsTab", "settings-tab", (name) => `settings/${name}`);

// --- Auth ---

function showScreen(id) {
  document.querySelectorAll(".screen").forEach((el) => (el.hidden = el.id !== id));
}

function applyAvatar(avatarPath, imgId, placeholderId) {
  const img = document.getElementById(imgId);
  const placeholder = document.getElementById(placeholderId);
  if (avatarPath) {
    img.src = avatarPath;
    img.hidden = false;
    placeholder.hidden = true;
  } else {
    img.hidden = true;
    placeholder.hidden = false;
  }
}

function applyTopbarAvatar() {
  if (!currentUser) return;
  document.getElementById("profile-username").textContent = currentUser.username;
  applyAvatar(currentUser.avatar_path, "profile-avatar", "profile-avatar-placeholder");
}

// --- Notifications (bell, menu entry, page) ---

let notificationData = { notifications: [], unread: 0 };
let newestNotificationId = null;
let notificationTimer = null;

function showToast(n) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.innerHTML = `<strong>${escapeHtml(n.title)}</strong>${n.body ? `<span class="muted small">${escapeHtml(n.body)}</span>` : ""}`;
  toast.addEventListener("click", () => {
    toast.remove();
    location.hash = "notifications";
  });
  document.getElementById("toast-area").appendChild(toast);
  setTimeout(() => toast.remove(), 8000);
}

function renderNotificationCounts() {
  const unread = notificationData.unread;
  const badge = document.getElementById("bell-badge");
  badge.textContent = unread > 99 ? "99+" : String(unread);
  badge.hidden = unread === 0;
  const menuCount = document.getElementById("menu-notif-count");
  menuCount.textContent = unread > 99 ? "99+" : String(unread);
  menuCount.hidden = unread === 0;
}

// The library follows the server: a game a scan found, or a scrape changed, shows up in the grid by itself. The server
// gives a short text that changes whenever a game is added, removed or changed, which is cheap to ask for.
let libraryRevision = null;

async function checkLibraryRevision() {
  const { revision } = await api("/api/games/revision");
  const changed = libraryRevision !== null && revision !== libraryRevision;
  libraryRevision = revision;
  if (!changed) return;
  gamesFetchedAt = 0; // whatever page is up, the list is fetched again when the grid comes back
  if (!document.getElementById("view-games").hidden) await refreshGames();
}

async function loadNotifications() {
  const data = await api("/api/notifications");
  const newest = data.notifications.reduce((m, n) => Math.max(m, n.id), 0);
  if (newestNotificationId !== null) {
    const fresh = data.notifications.filter((n) => n.id > newestNotificationId && !n.read);
    fresh.forEach(showToast);
    // The server says games came in: look at the library now instead of at the next poll.
    if (fresh.some((n) => n.kind === "games_added")) checkLibraryRevision().catch(() => {});
  }
  newestNotificationId = newest;
  notificationData = data;
  renderNotificationCounts();
  if (!document.getElementById("view-notifications").hidden) renderNotificationList();
}

function startNotificationPolling() {
  clearInterval(notificationTimer);
  newestNotificationId = null;
  libraryRevision = null;
  const poll = () => {
    loadNotifications().catch(() => {});
    checkLibraryRevision().catch(() => {});
  };
  poll();
  notificationTimer = setInterval(poll, 15000);
}

function stopNotificationPolling() {
  clearInterval(notificationTimer);
  libraryRevision = null;
  notificationData = { notifications: [], unread: 0 };
  renderNotificationCounts();
}

// What the server records about something a client did itself; every other kind comes from the server.
const CLIENT_NOTIFICATION_KINDS = new Set(["save_synced", "save_restored", "mod_downloaded"]);

// A notification about a game shows the game's own icon (its cover when it has no icon); the others show who wrote
// them: the client's icon for what a client did, the server's for the rest.
function notificationIcon(n, game) {
  const own = game && (gameIconUrl(game) || game.cover_path);
  if (own) return own;
  return CLIENT_NOTIFICATION_KINDS.has(n.kind) ? "assets/mog-client.png" : "assets/icon.png";
}

let notificationRender = 0;

async function renderNotificationList() {
  const token = ++notificationRender;
  const items = notificationData.notifications;
  const games = await Promise.all(items.map((n) => (n.game_id ? gameById(n.game_id) : null)));
  if (token !== notificationRender) return; // a newer list is being drawn
  const list = document.getElementById("notifications-list");
  list.innerHTML = "";
  document.getElementById("notifications-empty").hidden = items.length > 0;
  for (const [index, n] of items.entries()) {
    const li = document.createElement("li");
    li.className = `notification-item${n.read ? "" : " unread"}`;
    const game = n.game_id ? `<a href="#game/${n.game_id}">Open game</a> &middot; ` : "";
    li.innerHTML = `
      <div class="notification-main">
        <img class="notification-icon" src="${escapeHtml(notificationIcon(n, games[index]))}" alt="" />
        <div>
          <h4 class="notification-title">${escapeHtml(n.title)}</h4>
          ${n.body ? `<p class="notification-body">${escapeHtml(n.body)}</p>` : ""}
          <div class="notification-meta">${game}${escapeHtml(new Date(n.created_at).toLocaleString())}</div>
        </div>
      </div>
    `;
    const actions = document.createElement("div");
    actions.className = "notification-actions";
    if (!n.read) {
      const readBtn = document.createElement("button");
      readBtn.textContent = "Mark read";
      readBtn.addEventListener("click", async () => {
        await api(`/api/notifications/${n.id}/read`, { method: "POST" });
        await loadNotifications();
      });
      actions.appendChild(readBtn);
    }
    const delBtn = document.createElement("button");
    delBtn.className = "danger";
    delBtn.textContent = "Delete";
    delBtn.addEventListener("click", async () => {
      await api(`/api/notifications/${n.id}`, { method: "DELETE" });
      await loadNotifications();
    });
    actions.appendChild(delBtn);
    li.appendChild(actions);
    list.appendChild(li);
  }
}

document.getElementById("bell-btn").addEventListener("click", () => {
  location.hash = "notifications";
});

document.getElementById("notif-read-all-btn").addEventListener("click", async () => {
  await api("/api/notifications/read", { method: "POST" });
  await loadNotifications();
});

document.getElementById("notif-clear-btn").addEventListener("click", async () => {
  if (!confirm("Delete every notification?")) return;
  await api("/api/notifications", { method: "DELETE" });
  await loadNotifications();
});

function isAdmin() {
  return currentUser?.role === "admin";
}

// Controls the server refuses to anyone but an admin stay out of a plain user's way.
function applyRole() {
  document.querySelectorAll(".admin-only").forEach((el) => (el.hidden = !isAdmin()));
}

async function tryLogin(user, pass) {
  creds = { user, pass };
  currentUser = await api("/api/users/me"); // throws on bad creds
  sessionStorage.setItem("mog_user", user);
  sessionStorage.setItem("mog_pass", pass);
  applyTopbarAvatar();
  applyRole();
  showScreen("app-screen");
  startNotificationPolling();
  await refreshLibraries();
  router();
}

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const user = document.getElementById("login-user").value;
  const pass = document.getElementById("login-pass").value;
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = "";
  try {
    await tryLogin(user, pass);
  } catch (err) {
    errorEl.textContent = "Sign-in failed - check your username and password.";
    creds = null;
  }
});

document.getElementById("logout-btn").addEventListener("click", () => {
  sessionStorage.removeItem("mog_user");
  sessionStorage.removeItem("mog_pass");
  creds = null;
  currentUser = null;
  gamesAll = null;
  gameCache.clear();
  document.getElementById("games-loading").hidden = false;
  stopNotificationPolling();
  showScreen("login-screen");
});

document.getElementById("user-menu-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const dropdown = document.getElementById("user-menu-dropdown");
  dropdown.hidden = !dropdown.hidden;
});
document.addEventListener("click", () => {
  document.getElementById("user-menu-dropdown").hidden = true;
});

// --- Router ---

function router() {
  stopPolling();
  const hash = location.hash.replace(/^#\/?/, "");
  document.querySelectorAll(".view").forEach((v) => (v.hidden = true));

  if (hash.startsWith("game/")) {
    const [idText, tab] = hash.slice("game/".length).split("/");
    document.getElementById("view-game").hidden = false;
    gamesFetchedAt = 0; // the page may change the game: the list is fetched again on the way back
    openGamePage(parseInt(idText, 10));
    if (tab && document.querySelector(`#view-game .tab-btn[data-tab="${tab}"]`)) {
      activateTab("#view-game .tabs", "tab", "tab", tab);
    }
  } else if (hash === "notifications") {
    document.getElementById("view-notifications").hidden = false;
    loadNotifications().catch(() => {});
    renderNotificationList();
  } else if (hash === "profile") {
    document.getElementById("view-profile").hidden = false;
    openProfilePage();
  } else if (hash.startsWith("settings") && !isAdmin()) {
    location.hash = "";
    return;
  } else if (hash.startsWith("settings")) {
    document.getElementById("view-settings").hidden = false;
    const subTab = hash.includes("/") ? hash.slice("settings/".length) : null;
    openSettingsPage(subTab);
  } else {
    document.getElementById("view-games").hidden = false;
    const wanted = hash.startsWith("library/") ? parseInt(hash.slice("library/".length), 10) : null;
    selectedLibraryId = Number.isInteger(wanted) ? wanted : null;
    renderLibraryList("library-list", { clickable: true });
    if (gamesAll) showGames();
    if (!gamesAll || Date.now() - gamesFetchedAt > 15000) refreshGames().catch(showGamesError);
  }
  refreshSidebarWidgets();
}
window.addEventListener("hashchange", router);

// --- Profile page ---

async function openProfilePage() {
  try {
    currentUser = await api("/api/users/me");
  } catch (_) {
    return;
  }
  document.getElementById("profile-name-display").textContent = currentUser.username;
  document.getElementById("profile-role-display").textContent = currentUser.role;
  applyAvatar(currentUser.avatar_path, "profile-avatar-lg", "profile-avatar-lg-placeholder");
  document.getElementById("avatar-upload-status").textContent = "";
  document.getElementById("avatar-upload-input").value = "";
  document.getElementById("password-form").reset();
  document.getElementById("password-status").textContent = "";
  document.getElementById("devices-status").textContent = "";
  loadDevicesTable();
  applyRole();
}

document.getElementById("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const statusEl = document.getElementById("password-status");
  const current = document.getElementById("pw-current").value;
  const next = document.getElementById("pw-new").value;
  if (next !== document.getElementById("pw-repeat").value) {
    statusEl.textContent = "The two new passwords do not match.";
    return;
  }
  try {
    await api("/api/users/me/password", {
      method: "POST",
      body: JSON.stringify({ current_password: current, new_password: next }),
    });
    // The browser's own sign-in follows the new password.
    creds = { user: creds.user, pass: next };
    sessionStorage.setItem("mog_pass", next);
    document.getElementById("password-form").reset();
    statusEl.textContent = "Password changed.";
  } catch (err) {
    statusEl.textContent = `Could not change the password: ${err.message}`;
  }
});

document.getElementById("avatar-upload-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file || !currentUser) return;
  const statusEl = document.getElementById("avatar-upload-status");
  statusEl.textContent = "Uploading...";
  const formData = new FormData();
  formData.append("file", file);
  try {
    const resp = await fetch(`/api/users/${currentUser.id}/avatar`, {
      method: "POST",
      headers: { Authorization: authHeader() },
      body: formData,
    });
    if (!resp.ok) throw new Error(resp.statusText);
    currentUser = await resp.json();
    applyAvatar(currentUser.avatar_path, "profile-avatar-lg", "profile-avatar-lg-placeholder");
    applyTopbarAvatar();
    statusEl.textContent = "Saved.";
  } catch (err) {
    statusEl.textContent = `Upload failed: ${err.message}`;
  }
});

// --- Default install-mode settings (per-browser, see localStorage) ---

async function getInstallDefaults() {
  try {
    const d = await api("/api/games/install/defaults");
    return { autoMode: d.auto_mode, manualMode: d.manual_mode, downloadWorkers: d.download_workers };
  } catch (_) {
    return { autoMode: false, manualMode: false, downloadWorkers: 4 };
  }
}

// --- Settings page ---

async function openSettingsPage(subTab) {
  activateTab("#view-settings .tabs", "settingsTab", "settings-tab", subTab || "libraries");

  api("/api/health")
    .then((h) => { document.getElementById("server-version").textContent = h.version; })
    .catch(() => {});

  const s = await getInstallDefaults();
  document.getElementById("setting-auto-mode").checked = s.autoMode;
  document.getElementById("setting-manual-mode").checked = s.manualMode;
  document.getElementById("setting-download-workers").value = s.downloadWorkers;
  document.getElementById("download-workers-saved").textContent = "";
  document.getElementById("setting-auto-mode").onchange = saveInstallDefaults;
  document.getElementById("setting-manual-mode").onchange = saveInstallDefaults;
  const groupToggle = document.getElementById("setting-group-games");
  groupToggle.checked = groupGamesEnabled();
  groupToggle.onchange = () => {
    try {
      localStorage.setItem("mog_group_games", groupToggle.checked ? "1" : "0");
    } catch (_) {
      // Preference just won't persist.
    }
    refreshGames();
  };
  document.getElementById("api-keys-saved").textContent = "";
  document.getElementById("cache-ttl-saved").textContent = "";

  renderLibraryList("settings-library-list", { clickable: false, showScrape: true, allowDelete: true });

  try {
    const apiSettings = await api("/api/settings");
    document.getElementById("setting-igdb-id").value = apiSettings.igdb_client_id || "";
    document.getElementById("setting-igdb-secret").value = apiSettings.igdb_client_secret || "";
    document.getElementById("setting-sgdb-key").value = apiSettings.steamgriddb_api_key || "";
    document.getElementById("setting-cache-ttl").value = apiSettings.install_cache_ttl_days ?? "";
    applyProviderToggles(apiSettings);
    document.getElementById("setting-watch-libraries").checked = apiSettings.watch_libraries !== false;
    document.getElementById("proton-default-saved").textContent = "";
    await refreshProtonBuildsTable(apiSettings.install_default_proton_build || "");
  } catch (_) {
    // Not fatal - fields just stay blank until a successful load.
  }

  await refreshCacheTable();
  await refreshMissingTable();
  await refreshProviderValidityBadges();
  await refreshUsersTable();
}

// --- Watching the library folders (Settings > Libraries) ---

document.getElementById("setting-watch-libraries").addEventListener("change", async (e) => {
  const box = e.target;
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ watch_libraries: box.checked }) });
  } catch (err) {
    box.checked = !box.checked;
    alert(`Could not save: ${err.message}`);
  }
});

// --- Metadata provider switches ---

// Each provider has its own switch, saved as it is flipped. A provider that is off keeps its keys, which stay editable.
function applyProviderToggles(settings) {
  for (const box of document.querySelectorAll("input[data-provider]")) {
    box.checked = settings[box.dataset.provider] !== false;
    box.closest(".provider-card").classList.toggle("provider-off", !box.checked);
  }
}

for (const box of document.querySelectorAll("input[data-provider]")) {
  box.addEventListener("change", async () => {
    const card = box.closest(".provider-card");
    card.classList.toggle("provider-off", !box.checked);
    try {
      const saved = await api("/api/settings", { method: "PUT", body: JSON.stringify({ [box.dataset.provider]: box.checked }) });
      applyProviderToggles(saved);
      const provider = { igdb_enabled: "igdb", steamgriddb_enabled: "steamgriddb" }[box.dataset.provider];
      if (provider) await refreshProviderValidityBadges(provider); // HowLongToBeat has no key to check
    } catch (err) {
      box.checked = !box.checked;
      card.classList.toggle("provider-off", !box.checked);
      alert(`Could not save: ${err.message}`);
    }
  });
}

// --- Metadata provider key validation badges ---

function applyValidityBadge(el, valid) {
  if (valid === null || valid === undefined) {
    el.textContent = "";
    el.className = "validity-badge";
  } else if (valid) {
    el.textContent = "✓";
    el.className = "validity-badge valid";
  } else {
    el.textContent = "✗";
    el.className = "validity-badge invalid";
  }
}

const PROVIDER_BADGES = { igdb: "igdb-valid-badge", steamgriddb: "sgdb-valid-badge" };
// What each provider needs typed in before it can be asked anything: IGDB wants both values.
const PROVIDER_FIELDS = { igdb: ["setting-igdb-id", "setting-igdb-secret"], steamgriddb: ["setting-sgdb-key"] };

function providerHasItsKeys(provider) {
  return PROVIDER_FIELDS[provider].every((id) => document.getElementById(id).value.trim() !== "");
}

// Checks the keys against the provider, for one provider (`only`) or both. A provider with a key still missing is
// not asked, and when nothing is left to ask nothing is sent: a call to a provider is made only to find something out.
async function refreshProviderValidityBadges(only = null) {
  const wanted = only ? [only] : Object.keys(PROVIDER_BADGES);
  const asked = wanted.filter(providerHasItsKeys);
  for (const provider of wanted) {
    if (!asked.includes(provider)) applyValidityBadge(document.getElementById(PROVIDER_BADGES[provider]), null);
  }
  if (asked.length === 0) return;
  try {
    const result = await api(`/api/settings/validate${asked.length === 1 ? `?provider=${asked[0]}` : ""}`);
    for (const provider of asked) {
      applyValidityBadge(document.getElementById(PROVIDER_BADGES[provider]), result[provider === "igdb" ? "igdb_valid" : "steamgriddb_valid"]);
    }
  } catch (_) {
    for (const provider of asked) applyValidityBadge(document.getElementById(PROVIDER_BADGES[provider]), null);
  }
}

// --- Users (Settings > Users, admin only) ---

// An admin sets another user's password: the cell turns into a field with Save and Cancel.
function showPasswordPrompt(cell, user) {
  cell.innerHTML = "";
  const row = document.createElement("div");
  row.className = "password-inline";
  const input = document.createElement("input");
  input.type = "password";
  input.placeholder = "New password (8+)";
  input.autocomplete = "new-password";
  input.minLength = 8;
  const save = document.createElement("button");
  save.textContent = "Save";
  save.addEventListener("click", async () => {
    if (input.value.length < 8) {
      alert("The password must have at least 8 characters.");
      return;
    }
    try {
      await api(`/api/users/${user.id}`, { method: "PUT", body: JSON.stringify({ password: input.value }) });
      if (user.id === currentUser?.id) {
        creds = { user: creds.user, pass: input.value };
        sessionStorage.setItem("mog_pass", input.value);
      }
      await refreshUsersTable();
    } catch (err) {
      alert(`Could not change the password: ${err.message}`);
    }
  });
  const cancel = document.createElement("button");
  cancel.className = "danger";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", refreshUsersTable);
  row.append(input, save, cancel);
  cell.appendChild(row);
  input.focus();
}

// Hidden libraries of a user: removable tags and a + that lists the libraries still visible to them.
function renderHiddenLibraries(td, user) {
  let ids = [...(user.hidden_library_ids || [])];

  async function save(next) {
    try {
      await api(`/api/users/${user.id}`, { method: "PUT", body: JSON.stringify({ hidden_library_ids: next }) });
      ids = next;
      draw();
    } catch (err) {
      alert(`Could not update: ${err.message}`);
    }
  }

  function draw() {
    td.innerHTML = "";
    const row = document.createElement("div");
    row.className = "tag-list";
    for (const id of ids) {
      const lib = libraries.find((l) => l.id === id);
      const chip = document.createElement("span");
      chip.className = "chip chip-removable";
      chip.textContent = lib ? lib.name : `#${id}`;
      const x = document.createElement("span");
      x.className = "chip-x";
      x.setAttribute("role", "button");
      x.setAttribute("aria-label", `Show ${chip.textContent} again`);
      x.textContent = "\u00d7";
      x.addEventListener("click", () => save(ids.filter((i) => i !== id)));
      chip.appendChild(x);
      row.appendChild(chip);
    }
    const available = libraries.filter((l) => !ids.includes(l.id));
    const wrap = document.createElement("span");
    wrap.className = "tag-add";
    const plus = document.createElement("button");
    plus.type = "button";
    plus.className = "tag-plus";
    plus.textContent = "+";
    plus.title = "Hide a library from this user";
    plus.disabled = available.length === 0;
    const menu = document.createElement("div");
    menu.className = "dropdown-menu tag-menu";
    menu.hidden = true;
    for (const lib of available) {
      const item = document.createElement("a");
      item.className = "dropdown-item";
      item.href = "#";
      item.textContent = lib.name;
      item.addEventListener("click", (e) => {
        e.preventDefault();
        save([...ids, lib.id]);
      });
      menu.appendChild(item);
    }
    plus.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = menu.hidden;
      document.querySelectorAll(".tag-menu").forEach((m) => (m.hidden = true));
      menu.hidden = !open;
    });
    wrap.append(plus, menu);
    row.appendChild(wrap);
    td.appendChild(row);
  }

  draw();
}

document.addEventListener("click", () => {
  document.querySelectorAll(".tag-menu").forEach((m) => (m.hidden = true));
});

async function refreshUsersTable() {
  const body = document.getElementById("users-table-body");
  body.innerHTML = "<tr><td colspan='4' class='muted'>Loading...</td></tr>";
  try {
    const users = await api("/api/users");
    body.innerHTML = "";
    for (const u of users) {
      const tr = document.createElement("tr");

      const nameTd = document.createElement("td");
      nameTd.textContent = u.username;
      tr.appendChild(nameTd);

      const roleTd = document.createElement("td");
      roleTd.textContent = u.role;
      tr.appendChild(roleTd);

      const hiddenTd = document.createElement("td");
      renderHiddenLibraries(hiddenTd, u);
      tr.appendChild(hiddenTd);

      const actionTd = document.createElement("td");
      actionTd.className = "users-actions";
      const pwBtn = document.createElement("button");
      pwBtn.textContent = "Change password";
      pwBtn.addEventListener("click", () => showPasswordPrompt(actionTd, u));
      actionTd.appendChild(pwBtn);
      const delBtn = document.createElement("button");
      delBtn.textContent = "Delete";
      delBtn.className = "danger";
      delBtn.addEventListener("click", async () => {
        if (!confirm(`Delete user "${u.username}"?`)) return;
        try {
          const done = await deleteWarningAboutSaves(
            `/api/users/${u.id}`,
            `User "${u.username}"`,
            "Deleting the user deletes their saved games with them, from every device."
          );
          if (!done) return;
          await refreshUsersTable();
        } catch (err) {
          alert(`Could not delete: ${err.message}`);
        }
      });
      actionTd.appendChild(delBtn);
      tr.appendChild(actionTd);

      body.appendChild(tr);
    }
  } catch (err) {
    body.innerHTML = `<tr><td colspan="4" class="error">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById("show-add-user-btn").addEventListener("click", () => {
  const form = document.getElementById("add-user-form");
  form.hidden = !form.hidden;
});

document.getElementById("add-user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/users", {
      method: "POST",
      body: JSON.stringify({
        username: document.getElementById("new-user-username").value,
        password: document.getElementById("new-user-password").value,
        role: document.getElementById("new-user-role").value,
      }),
    });
    document.getElementById("add-user-form").reset();
    document.getElementById("add-user-form").hidden = true;
    await refreshUsersTable();
  } catch (err) {
    alert(`Could not create user: ${err.message}`);
  }
});

// --- Proton / Wine build management (Settings > Install defaults) ---

async function refreshProtonBuildsTable(currentDefault) {
  const select = document.getElementById("setting-proton-default");
  const body = document.getElementById("proton-builds-table-body");
  select.innerHTML = '<option value="">(server default - CachyOS Proton)</option>';
  body.innerHTML = "<tr><td colspan='4' class='muted'>Loading...</td></tr>";
  try {
    const data = await api("/api/games/install/proton-builds");
    body.innerHTML = "";
    for (const b of data.builds) {
      const opt = document.createElement("option");
      opt.value = b.id;
      opt.textContent = b.label;
      opt.selected = b.id === currentDefault;
      select.appendChild(opt);

      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(b.label)}</td><td>${escapeHtml(b.version || "")}</td><td>${b.installed ? "Installed" : "Not installed"}</td>`;
      const actionTd = document.createElement("td");
      const btn = document.createElement("button");
      if (b.installed) {
        btn.className = "danger";
        btn.textContent = "Remove";
        btn.addEventListener("click", async () => {
          if (!confirm(`Remove ${b.label}?`)) return;
          await api(`/api/games/install/proton/${b.id}`, { method: "DELETE" });
          await refreshProtonBuildsTable(currentDefault);
        });
      } else {
        btn.textContent = "Download";
        btn.addEventListener("click", async () => {
          await api(`/api/games/install/proton/${b.id}/download`, { method: "POST" });
          btn.textContent = "Downloading...";
          btn.disabled = true;
          pollProtonDownload(b.id, () => refreshProtonBuildsTable(currentDefault));
        });
      }
      actionTd.appendChild(btn);
      tr.appendChild(actionTd);
      body.appendChild(tr);
    }
  } catch (err) {
    body.innerHTML = `<tr><td colspan="4" class="error">${escapeHtml(err.message)}</td></tr>`;
  }
}

function pollProtonDownload(buildId, onDone) {
  const timer = setInterval(async () => {
    try {
      const progress = await api(`/api/games/install/proton/${buildId}/progress`);
      if (progress.progress === null && !progress.extracting) {
        clearInterval(timer);
        onDone();
      }
    } catch (_) {
      clearInterval(timer);
    }
  }, 2000);
}

document.getElementById("refresh-proton-builds-btn").addEventListener("click", () => {
  refreshProtonBuildsTable(document.getElementById("setting-proton-default").value);
});

document.getElementById("save-proton-default-btn").addEventListener("click", async () => {
  const savedEl = document.getElementById("proton-default-saved");
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ install_default_proton_build: document.getElementById("setting-proton-default").value || null }),
    });
    savedEl.textContent = "Saved.";
  } catch (err) {
    savedEl.textContent = `Could not save: ${err.message}`;
  }
});

async function saveInstallDefaults() {
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        install_default_auto_mode: document.getElementById("setting-auto-mode").checked,
        install_default_manual_mode: document.getElementById("setting-manual-mode").checked,
      }),
    });
  } catch (_) {
    // The toggles re-read the server's value next time Settings opens.
  }
}

// The keys are saved when a field is left or Enter is pressed in it (the browser's `change`), not by a button.
let apiKeysSavedTimer = null;
async function saveApiKeys(provider) {
  const savedEl = document.getElementById("api-keys-saved");
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        igdb_client_id: document.getElementById("setting-igdb-id").value,
        igdb_client_secret: document.getElementById("setting-igdb-secret").value,
        steamgriddb_api_key: document.getElementById("setting-sgdb-key").value,
      }),
    });
    savedEl.textContent = "Saved.";
    clearTimeout(apiKeysSavedTimer);
    apiKeysSavedTimer = setTimeout(() => (savedEl.textContent = ""), 2500);
    await refreshProviderValidityBadges(provider); // only the provider whose field this is, and only if it has all its keys
  } catch (err) {
    savedEl.textContent = `Could not save: ${err.message}`;
  }
}

for (const [provider, ids] of Object.entries(PROVIDER_FIELDS)) {
  for (const id of ids) document.getElementById(id).addEventListener("change", () => saveApiKeys(provider));
}

document.getElementById("save-download-workers-btn").addEventListener("click", async () => {
  const savedEl = document.getElementById("download-workers-saved");
  const input = document.getElementById("setting-download-workers");
  const value = parseInt(input.value, 10);
  if (!(value >= 1 && value <= 16)) {
    savedEl.textContent = "Enter a number from 1 to 16.";
    return;
  }
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ download_workers: value }) });
    savedEl.textContent = "Saved.";
  } catch (err) {
    savedEl.textContent = `Could not save: ${err.message}`;
  }
});

document.getElementById("save-cache-ttl-btn").addEventListener("click", async () => {
  const savedEl = document.getElementById("cache-ttl-saved");
  const raw = document.getElementById("setting-cache-ttl").value;
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ install_cache_ttl_days: raw === "" ? null : parseInt(raw, 10) }),
    });
    savedEl.textContent = "Saved.";
  } catch (err) {
    savedEl.textContent = `Could not save: ${err.message}`;
  }
});

function fmtExpiry(iso) {
  if (!iso) return "never";
  const at = new Date(iso);
  const date = at.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const days = Math.ceil((at - Date.now()) / 86400000);
  if (days <= 0) return `${date} (due)`;
  return `${date} (in ${days} ${days === 1 ? "day" : "days"})`;
}

async function refreshCacheTable() {
  const body = document.getElementById("cache-table-body");
  body.innerHTML = "<tr><td colspan='6' class='muted'>Loading...</td></tr>";
  try {
    const data = await api("/api/games/install/cache");
    await renderModCacheTable(data.mods || []);
    if (data.entries.length === 0) {
      body.innerHTML = "<tr><td colspan='6' class='muted'>No cached installs.</td></tr>";
      return;
    }
    body.innerHTML = "";
    for (const entry of data.entries) {
      const tr = document.createElement("tr");
      const game = await gameById(entry.game_id);
      const title = escapeHtml(game ? game.name : entry.game_id ?? "?");
      tr.innerHTML = `
        <td>${entry.session_id}</td>
        <td>${entry.game_id ? `<a href="#game/${entry.game_id}">${title}</a>` : title}</td>
        <td>${escapeHtml(entry.state || "")}</td>
        <td>${fmtBytes(entry.size_bytes)}</td>
        <td class="cache-expiry">${entry.state ? fmtExpiry(entry.expires_at) : "-"}</td>
      `;
      const actionTd = document.createElement("td");
      if (entry.state) {
        const resetBtn = document.createElement("button");
        resetBtn.textContent = "Reset";
        resetBtn.addEventListener("click", async () => {
          try {
            const result = await api(`/api/games/install/cache/${entry.session_id}/reset`, { method: "POST" });
            tr.querySelector(".cache-expiry").textContent = fmtExpiry(result.expires_at);
          } catch (err) {
            alert(`Could not reset: ${err.message}`);
          }
        });
        actionTd.appendChild(resetBtn);
      }
      const delBtn = document.createElement("button");
      delBtn.textContent = "Delete";
      delBtn.className = "danger";
      delBtn.addEventListener("click", async () => {
        try {
          await api(`/api/games/install/cache/${entry.session_id}`, { method: "DELETE" });
          await refreshCacheTable();
        } catch (err) {
          alert(`Could not delete: ${err.message}`);
        }
      });
      actionTd.appendChild(delBtn);
      tr.appendChild(actionTd);
      body.appendChild(tr);
    }
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6" class="error">${escapeHtml(err.message)}</td></tr>`;
  }
}

async function renderModCacheTable(mods) {
  const body = document.getElementById("mod-cache-table-body");
  document.getElementById("clear-mod-cache-btn").hidden = mods.length === 0;
  if (mods.length === 0) {
    body.innerHTML = "<tr><td colspan='5' class='muted'>No cached mod zips.</td></tr>";
    return;
  }
  body.innerHTML = "";
  for (const entry of mods) {
    const game = await gameById(entry.game_id);
    const title = escapeHtml(game ? game.name : String(entry.game_id));
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><a href="#game/${entry.game_id}">${title}</a></td>
      <td>${escapeHtml(entry.file_name)}</td>
      <td>${fmtBytes(entry.size_bytes)}</td>
      <td>${escapeHtml(new Date(entry.modified_at * 1000).toLocaleString())}</td>
    `;
    const actionTd = document.createElement("td");
    const delBtn = document.createElement("button");
    delBtn.textContent = "Delete";
    delBtn.className = "danger";
    delBtn.addEventListener("click", async () => {
      try {
        await api(`/api/games/install/cache/mods/${entry.game_id}/${encodeURIComponent(entry.file_name)}`, { method: "DELETE" });
        await refreshCacheTable();
      } catch (err) {
        alert(`Could not delete: ${err.message}`);
      }
    });
    actionTd.appendChild(delBtn);
    tr.appendChild(actionTd);
    body.appendChild(tr);
  }
}

document.getElementById("clear-mod-cache-btn").addEventListener("click", async () => {
  if (!confirm("Delete every cached mod zip? They are made again when a mod is downloaded.")) return;
  try {
    const result = await api("/api/games/install/cache/mods", { method: "DELETE" });
    alert(`Cleared ${result.cleared} zip(s).`);
    await refreshCacheTable();
  } catch (err) {
    alert(`Could not clear: ${err.message}`);
  }
});

document.getElementById("refresh-cache-btn").addEventListener("click", refreshCacheTable);

document.getElementById("clear-all-cache-btn").addEventListener("click", async () => {
  if (!confirm("Clear every install cache that isn't currently running?")) return;
  try {
    const result = await api("/api/games/install/cache", { method: "DELETE" });
    alert(`Cleared ${result.cleared} cache(s).`);
    await refreshCacheTable();
  } catch (err) {
    alert(`Could not clear: ${err.message}`);
  }
});

// A dialog that cannot be mistaken for the usual confirm(): deleting something that cannot be recovered.
function confirmDanger({ title, lines, confirmLabel, cancelLabel = "Cancel" }) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal";
    overlay.innerHTML = `
      <div class="modal-panel danger-panel" role="alertdialog" aria-modal="true">
        <h3 class="danger-title">&#9888; ${escapeHtml(title)}</h3>
        <ul>${lines.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>
        <div class="modal-footer">
          <button type="button" data-answer="no">${escapeHtml(cancelLabel)}</button>
          <button type="button" class="danger" data-answer="yes">${escapeHtml(confirmLabel)}</button>
        </div>
      </div>`;
    const finish = (answer) => {
      overlay.remove();
      resolve(answer);
    };
    overlay.addEventListener("click", (e) => {
      const answer = e.target.closest("button[data-answer]");
      if (answer) finish(answer.dataset.answer === "yes");
      else if (e.target === overlay) finish(false);
    });
    document.body.appendChild(overlay);
    overlay.querySelector('[data-answer="no"]').focus();
  });
}

// DELETE `url`; when the server says it would lose saved games (409 has_saves), say how many and
// delete them too only if confirmed. With `keepLabel`, declining means keeping them (the game stays
// listed as "Saves only"); that is the default answer. Returns whether anything was deleted.
async function deleteWarningAboutSaves(url, what, consequence, keepLabel = "Cancel") {
  try {
    await api(url, { method: "DELETE" });
    return true;
  } catch (err) {
    const found = err.status === 409 && err.detail && err.detail.code === "has_saves" ? err.detail : null;
    if (!found) throw err;
    const sure = await confirmDanger({
      title: keepLabel === "Cancel" ? "Saved games will be permanently deleted" : "This game still has saved games",
      lines: [
        `${what} has ${found.versions} saved version${found.versions === 1 ? "" : "s"} on this server (${fmtBytes(found.size_bytes)}).`,
        consequence,
        "This cannot be undone and there is no way to get them back.",
      ],
      confirmLabel: `Delete ${found.versions} save${found.versions === 1 ? "" : "s"} too`,
      cancelLabel: keepLabel,
    });
    if (!sure) return false;
    await api(`${url}?delete_saves=true`, { method: "DELETE" });
    return true;
  }
}

// --- Missing games (files a scan could no longer find) ---

async function refreshMissingTable() {
  const body = document.getElementById("missing-table-body");
  body.innerHTML = "<tr><td colspan='4' class='muted'>Loading...</td></tr>";
  try {
    const missing = await api("/api/games/missing");
    if (missing.length === 0) {
      body.innerHTML = "<tr><td colspan='4' class='muted'>No missing games.</td></tr>";
      return;
    }
    body.innerHTML = "";
    for (const game of missing) {
      const tr = document.createElement("tr");
      const libName = (libraries.find((l) => l.id === game.library_id) || {}).name || "";
      tr.innerHTML = `
        <td>${escapeHtml(game.name)}${game.saves_only ? ' <span class="chip chip-saves" title="Its saves are kept">Saves only</span>' : ""}</td>
        <td>${escapeHtml(libName)}</td>
        <td>${escapeHtml(game.fs_name)}</td>
      `;
      const actionTd = document.createElement("td");
      const delBtn = document.createElement("button");
      delBtn.textContent = "Clear";
      delBtn.className = "danger";
      delBtn.addEventListener("click", async () => {
        try {
          const done = await deleteWarningAboutSaves(
            `/api/games/${game.id}`,
            `"${game.name}"`,
            "Without the game entry the saves could no longer be listed, downloaded or restored.",
            "Keep the saves (the game stays listed as Saves only)"
          );
          if (!done) {
            alert("Nothing was removed: the game stays listed as Saves only.");
            return;
          }
          await refreshMissingTable();
          await refreshGames();
        } catch (err) {
          alert(`Could not clear: ${err.message}`);
        }
      });
      actionTd.appendChild(delBtn);
      tr.appendChild(actionTd);
      body.appendChild(tr);
    }
  } catch (err) {
    body.innerHTML = `<tr><td colspan="4" class="error">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById("refresh-missing-btn").addEventListener("click", refreshMissingTable);

document.getElementById("clear-all-missing-btn").addEventListener("click", async () => {
  if (!confirm("Remove every missing game from MOG? Games that still have saved games are kept.")) return;
  try {
    const result = await api("/api/games/missing", { method: "DELETE" });
    const kept = result.kept_with_saves ? ` ${result.kept_with_saves} kept because they still have saves: clear them one by one.` : "";
    alert(`Cleared ${result.cleared} game(s).${kept}`);
    await refreshMissingTable();
    await refreshGames();
  } catch (err) {
    alert(`Could not clear: ${err.message}`);
  }
});

// After a scan the server matches new games in the background; reload the
// grid while it works so matches (and the grouping they enable) show up by themselves.
let scrapePoll = null;

function pollWhileScraping() {
  clearInterval(scrapePoll);
  let ticks = 0;
  scrapePoll = setInterval(async () => {
    ticks += 1;
    try {
      await refreshGames();
    } catch (_) {
      // Transient: the next tick retries.
    }
    if (ticks >= 20) clearInterval(scrapePoll);
  }, 6000);
}

// --- Sidebar widgets: active installs, total cache size ---

async function refreshSidebarWidgets() {
  try {
    const sessions = await api("/api/games/install/active");
    const widget = document.getElementById("active-installs-widget");
    const list = document.getElementById("active-installs-list");
    widget.hidden = sessions.length === 0;
    list.innerHTML = "";
    for (const s of sessions) {
      const game = await gameById(s.game_id);
      const li = document.createElement("li");
      const pct = s.bytes_total ? (s.bytes_written / s.bytes_total) * 100 : 0;
      const icon = gameIconUrl(game);
      const state = s.state.charAt(0).toUpperCase() + s.state.slice(1).replace(/_/g, " ");
      li.innerHTML = `
        ${icon ? `<img class="ai-icon" src="${escapeHtml(icon)}" alt="" />` : '<span class="ai-icon ai-icon-none">&#127918;</span>'}
        <div class="ai-text">
          <span class="ai-name">${escapeHtml(game ? game.name : `Game ${s.game_id}`)}</span>
          <span class="muted small">${escapeHtml(state)}...${pct ? ` ${Math.round(pct)}%` : ""}</span>
          <div class="ai-progress"><div class="ai-progress-fill" style="width:${pct}%"></div></div>
        </div>
      `;
      li.addEventListener("click", () => {
        location.hash = `game/${s.game_id}`;
      });
      list.appendChild(li);
    }
  } catch (_) {
    // Not fatal - the widget just stays hidden/stale.
  }

  try {
    const data = await api("/api/games/install/cache");
    const total = data.entries.reduce((sum, e) => sum + e.size_bytes, 0);
    document.getElementById("total-cache-size").textContent = fmtBytes(total);
  } catch (_) {
    document.getElementById("total-cache-size").textContent = "?";
  }
}

// --- Libraries ---

async function refreshLibraries() {
  libraries = await api("/api/libraries");
  renderLibraryList("library-list", { clickable: true });
}

// Shared renderer for both the sidebar (clickable, filters the game grid)
// and the Settings page's list (not clickable; optionally offers Scrape).
function renderLibraryList(containerId, { clickable, showScrape = false, allowDelete = false }) {
  const list = document.getElementById(containerId);
  list.innerHTML = "";

  if (clickable) {
    const allItem = document.createElement("li");
    allItem.className = selectedLibraryId === null ? "active" : "";
    const allName = document.createElement("div");
    allName.innerHTML = '<span class="lib-name">All games</span>';
    allName.addEventListener("click", () => {
      location.hash = "";
    });
    allItem.appendChild(allName);
    if (isAdmin() && libraries.length > 1) {
      const actions = document.createElement("div");
      actions.className = "library-actions";
      const scanAll = document.createElement("button");
      scanAll.textContent = "Scan";
      scanAll.title = "Scan every library";
      scanAll.addEventListener("click", async (e) => {
        e.stopPropagation();
        scanAll.textContent = "...";
        try {
          let scraping = false;
          for (const lib of libraries) {
            const result = await api(`/api/libraries/${lib.id}/scan`, { method: "POST" });
            scraping = scraping || Boolean(result.scraping);
          }
          await refreshGames();
          if (scraping) pollWhileScraping();
        } finally {
          scanAll.textContent = "Scan";
        }
      });
      actions.appendChild(scanAll);
      allItem.appendChild(actions);
    }
    list.appendChild(allItem);
  }

  for (const lib of libraries) {
    const li = document.createElement("li");
    li.className = clickable && selectedLibraryId === lib.id ? "active" : "";

    const info = document.createElement("div");
    info.innerHTML = `<span class="lib-name">${escapeHtml(lib.name)}</span><span class="lib-path">${escapeHtml(lib.root_path)}</span>`;
    if (clickable) {
      info.addEventListener("click", () => {
        location.hash = `library/${lib.id}`;
      });
    }
    li.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "library-actions";

    const scanBtn = document.createElement("button");
    scanBtn.textContent = "Scan";
    scanBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      scanBtn.textContent = "...";
      try {
        const result = await api(`/api/libraries/${lib.id}/scan`, { method: "POST" });
        await refreshGames();
        if (result.scraping) pollWhileScraping();
      } finally {
        scanBtn.textContent = "Scan";
      }
    });
    if (isAdmin()) actions.appendChild(scanBtn);

    if (showScrape) {
      const scrapeBtn = document.createElement("button");
      scrapeBtn.textContent = "Scrape";
      scrapeBtn.title = "Fill in missing metadata (IGDB + SteamGridDB) for every game in this library";
      scrapeBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        scrapeBtn.textContent = "...";
        try {
          const result = await api(`/api/libraries/${lib.id}/scrape`, { method: "POST" });
          scrapeBtn.textContent = "Scrape";
          alert(`Scraped ${result.scraped} of ${result.total} game(s).`);
        } catch (err) {
          scrapeBtn.textContent = "Scrape";
          alert(`Scrape failed: ${err.message}`);
        }
      });
      actions.appendChild(scrapeBtn);
    }

    if (allowDelete) {
      const delBtn = document.createElement("button");
      delBtn.textContent = "Delete";
      delBtn.className = "danger";
      delBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete library "${lib.name}"? This does not delete files on disk.`)) return;
        try {
          await api(`/api/libraries/${lib.id}`, { method: "DELETE" });
          if (selectedLibraryId === lib.id) selectedLibraryId = null;
          await refreshLibraries();
          renderLibraryList("settings-library-list", { clickable: false, showScrape: true, allowDelete: true });
        } catch (err) {
          alert(`Could not delete: ${err.message}`);
        }
      });
      actions.appendChild(delBtn);
    }

    li.appendChild(actions);
    list.appendChild(li);
  }
}

document.getElementById("add-library-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("lib-name").value;
  const root_path = document.getElementById("lib-path").value;
  await api("/api/libraries", { method: "POST", body: JSON.stringify({ name, root_path }) });
  document.getElementById("add-library-form").reset();
  await refreshLibraries();
  renderLibraryList("settings-library-list", { clickable: false, showScrape: true, allowDelete: true });
});

// --- Games grid page ---

// Every game is fetched once and a library only filters them, so switching libraries needs no round trip.
async function refreshGames() {
  gamesAll = await api("/api/games");
  gamesFetchedAt = Date.now();
  showGames();
}

function showGames() {
  games = selectedLibraryId === null ? gamesAll : gamesAll.filter((g) => g.library_id === selectedLibraryId);
  const libName = selectedLibraryId === null ? "All Games" : (libraries.find((l) => l.id === selectedLibraryId) || {}).name || "Games";
  document.getElementById("games-loading").hidden = true;
  document.getElementById("games-heading").textContent = `${libName} (${gridEntries().length})`;
  renderGameGrid(document.getElementById("game-search").value.trim().toLowerCase());
}

function showGamesError(err) {
  const box = document.getElementById("games-loading");
  box.querySelector("p").textContent = `Could not load the library: ${err.message}`;
  box.classList.add("failed");
}

async function gameById(id) {
  const known = (gamesAll || []).find((g) => g.id === id) || gameCache.get(id);
  if (known) return known;
  try {
    const game = await api(`/api/games/${id}`);
    gameCache.set(id, game);
    return game;
  } catch (_) {
    return null;
  }
}

function gameIconUrl(game) {
  return ((game && game.media) || {}).icon?.url || null;
}

// Corner badge on a cover: the game has a finished install on the server.
const INSTALLED_BADGE =
  '<span class="installed-badge" title="Installed"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg></span>';

// Corner badge (top left): a scan could not find this game on disk.
const MISSING_BADGE =
  '<span class="missing-badge" title="Missing from disk"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5l9 9M14 5l-9 9" /></svg></span>';

// Corner badge (top left): the game is gone from disk, but its saves are kept.
const SAVES_BADGE =
  '<span class="saves-badge" title="Saves only: the game is gone from disk, its saves are kept"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h11l3 3v15H5z" /><path d="M8 3v5h7V3" /><path d="M8 21v-6h8v6" /></svg></span>';

// Corner badge (bottom left): the folder has mods or DLC but nothing that installs the game itself.
const ADDONS_BADGE =
  '<span class="addons-badge" title="Add-ons only: no installer for the game itself"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 11H19V7c0-1.1-.9-2-2-2h-4V3.5C13 2.12 11.88 1 10.5 1S8 2.12 8 3.5V5H4c-1.1 0-2 .9-2 2v3.8h1.5c1.49 0 2.7 1.21 2.7 2.7s-1.21 2.7-2.7 2.7H2V20c0 1.1.9 2 2 2h3.8v-1.5c0-1.49 1.21-2.7 2.7-2.7s2.7 1.21 2.7 2.7V22H17c1.1 0 2-.9 2-2v-4h1.5c1.38 0 2.5-1.12 2.5-2.5S21.88 11 20.5 11z" /></svg></span>';

// Games sharing an IGDB id are versions of one title; the grid shows one
// representative (preferring one that is still on disk) per group.
function groupGames(list) {
  const groups = new Map();
  const out = [];
  for (const game of list) {
    if (!game.igdb_id) {
      out.push({ game, versions: 1, lastPlayed: game.last_played || null });
      continue;
    }
    let entry = groups.get(game.igdb_id);
    if (!entry) {
      entry = { game, versions: 0, lastPlayed: null };
      groups.set(game.igdb_id, entry);
      out.push(entry);
    } else if (entry.game.missing_from_fs && !game.missing_from_fs) {
      entry.game = game;
    }
    if (game.last_played && (!entry.lastPlayed || game.last_played > entry.lastPlayed)) entry.lastPlayed = game.last_played;
    entry.versions += 1;
  }
  return out;
}

function groupGamesEnabled() {
  try {
    return localStorage.getItem("mog_group_games") !== "0";
  } catch (_) {
    return true;
  }
}

// --- Sorting (per browser): last played first, then the chosen order ---

const SORT_ORDERS = {
  newest: (a, b) => releaseOf(b) - releaseOf(a) || a.game.name.localeCompare(b.game.name),
  oldest: (a, b) => releaseOf(a, Infinity) - releaseOf(b, Infinity) || a.game.name.localeCompare(b.game.name),
  az: (a, b) => a.game.name.localeCompare(b.game.name),
  za: (a, b) => b.game.name.localeCompare(a.game.name),
};
const DEFAULT_SORT_ORDER = "newest";

function releaseOf(entry, missing = -Infinity) {
  return ((entry.game.igdb_metadata || {}).first_release_date ?? missing);
}

function sortSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem("mog_sort") || "{}");
    return { lastPlayed: Boolean(saved.lastPlayed), order: SORT_ORDERS[saved.order] ? saved.order : DEFAULT_SORT_ORDER };
  } catch (_) {
    return { lastPlayed: false, order: DEFAULT_SORT_ORDER };
  }
}

function saveSortSettings(settings) {
  try {
    localStorage.setItem("mog_sort", JSON.stringify(settings));
  } catch (_) {
    // Not remembered; the choice still applies until the page is reloaded.
  }
}

function sortEntries(entries) {
  const { lastPlayed, order } = sortSettings();
  const byOrder = SORT_ORDERS[order];
  return [...entries].sort((a, b) => {
    if (lastPlayed && (a.lastPlayed || b.lastPlayed)) {
      if (!a.lastPlayed) return 1;
      if (!b.lastPlayed) return -1;
      if (a.lastPlayed !== b.lastPlayed) return a.lastPlayed < b.lastPlayed ? 1 : -1;
    }
    return byOrder(a, b);
  });
}

function gridEntries() {
  const entries = groupGamesEnabled()
    ? groupGames(games)
    : games.map((game) => ({ game, versions: 1, lastPlayed: game.last_played || null }));
  return sortEntries(entries);
}

function renderGameGrid(filterText) {
  const grid = document.getElementById("game-grid");
  grid.innerHTML = "";
  const entries = gridEntries();
  const visible = filterText ? entries.filter((e) => e.game.name.toLowerCase().includes(filterText)) : entries;
  for (const { game, versions } of visible) {
    const card = document.createElement("div");
    card.className = "game-card";
    card.title = game.name;
    card.innerHTML = `
      <div class="cover${game.cover_path ? "" : " no-cover"}">${game.cover_path ? `<img src="${escapeHtml(game.cover_path)}" />` : "\u{1F3AE}"}${game.installed ? INSTALLED_BADGE : ""}${game.saves_only ? SAVES_BADGE : game.missing_from_fs ? MISSING_BADGE : ""}${game.addons_only && !game.missing_from_fs ? ADDONS_BADGE : ""}${versions > 1 ? `<span class="sibling-badge" title="${versions} versions">${versions}</span>` : ""}</div>
      <div class="name">${escapeHtml(game.name)}</div>
    `;
    card.addEventListener("click", () => {
      location.hash = `game/${game.id}`;
    });
    grid.appendChild(card);
  }
}

document.getElementById("refresh-games-btn").addEventListener("click", refreshGames);

document.getElementById("game-search").addEventListener("input", (e) => {
  document.getElementById("game-search-clear").hidden = !e.target.value;
  if (gamesAll) renderGameGrid(e.target.value.trim().toLowerCase());
});

document.getElementById("game-search-clear").addEventListener("click", () => {
  const box = document.getElementById("game-search");
  box.value = "";
  box.dispatchEvent(new Event("input"));
  box.focus();
});

{
  const saved = sortSettings();
  const lastPlayedBox = document.getElementById("sort-last-played");
  const orderSelect = document.getElementById("game-sort");
  lastPlayedBox.checked = saved.lastPlayed;
  orderSelect.value = saved.order;
  const changed = () => {
    saveSortSettings({ lastPlayed: lastPlayedBox.checked, order: orderSelect.value });
    if (gamesAll) showGames();
  };
  lastPlayedBox.addEventListener("change", changed);
  orderSelect.addEventListener("change", changed);
}

// --- Sidebar collapse (per-browser, remembered across reloads) ---

function applySidebarCollapsed(collapsed) {
  document.getElementById("sidebar").classList.toggle("collapsed", collapsed);
  document.getElementById("sidebar-toggle").textContent = collapsed ? "»" : "«";
}

document.getElementById("sidebar-toggle").addEventListener("click", () => {
  const collapsed = !document.getElementById("sidebar").classList.contains("collapsed");
  localStorage.setItem("mog_sidebar_collapsed", collapsed ? "1" : "0");
  applySidebarCollapsed(collapsed);
});

applySidebarCollapsed(localStorage.getItem("mog_sidebar_collapsed") === "1");

// --- Game detail page ---

function resetGameTabs() {
  document.querySelectorAll("#view-game .tab-btn").forEach((b, i) => b.classList.toggle("active", i === 0));
  document.getElementById("tab-overview").hidden = false;
  document.getElementById("tab-install").hidden = true;
  document.getElementById("tab-files").hidden = true;
}

// What the game takes on the server: "40.0 GB (Installer 27.7 GB, Cache 12.3 GB, Saves 1.4 MB)", the empty parts left out. The server
// remembers it, so this is cheap to ask for on every visit.
async function loadGameSizes(gameId) {
  const line = document.getElementById("game-sizes");
  line.hidden = true;
  try {
    const sizes = await api(`/api/games/${gameId}/sizes`);
    if (!activeGame || activeGame.id !== gameId) return;
    const parts = [["Installer", sizes.installer_bytes], ["Cache", sizes.cache_bytes], ["Saves", sizes.saves_bytes]]
      .filter(([, bytes]) => bytes > 0)
      .map(([name, bytes]) => `${name} ${fmtBytes(bytes)}`);
    line.textContent = `Size on server: ${fmtBytes(sizes.total_bytes)}` + (parts.length ? ` (${parts.join(", ")})` : "");
    line.hidden = false;
  } catch (_) {
    // An older server has no such figure; the line just stays out.
  }
}

// "Last played: 8 Oct 2026, 01:40 on karasu": the newest saved version, and the machine that made it. Nothing for a game never played.
function renderLastPlayed(game) {
  const line = document.getElementById("game-last-played");
  line.hidden = !game.last_played;
  if (!game.last_played) return;
  const when = new Date(game.last_played).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  line.textContent = `Last played: ${when}` + (game.last_played_on ? ` on ${game.last_played_on}` : "");
}

async function openGamePage(id) {
  resetGameTabs();
  // Until the game arrives the page still holds the one seen before: keep it out of sight rather than let it flash.
  const view = document.getElementById("view-game");
  if (!activeGame || activeGame.id !== id) view.classList.add("loading");
  let game;
  try {
    game = await api(`/api/games/${id}`);
  } catch (err) {
    view.classList.remove("loading");
    document.getElementById("game-title").textContent = "Not found";
    document.getElementById("game-summary").textContent = err.message;
    return;
  }
  activeGame = game;
  selectedCandidate = null;

  document.getElementById("game-title").textContent = game.name;
  document.getElementById("game-id-display").textContent = game.id;
  loadGameSizes(game.id);
  renderLastPlayed(game);
  document.querySelector("#view-game .back-link").href = selectedLibraryId !== null ? `#library/${selectedLibraryId}` : "#";
  const libName = (libraries.find((l) => l.id === game.library_id) || {}).name || "";
  document.getElementById("game-library").textContent = libName;

  const coverImg = document.getElementById("game-cover-img");
  const coverPlaceholder = document.getElementById("game-cover-placeholder");
  if (game.cover_path) {
    // The old cover stays on screen until the new one has loaded, so it is hidden meanwhile.
    coverPlaceholder.hidden = true;
    coverImg.onerror = () => {
      coverImg.hidden = true;
      coverPlaceholder.hidden = false;
    };
    if (coverImg.getAttribute("src") === game.cover_path && coverImg.complete) {
      coverImg.hidden = false;
    } else {
      coverImg.hidden = true;
      coverImg.onload = () => {
        coverImg.hidden = false;
      };
      coverImg.src = game.cover_path;
    }
  } else {
    coverImg.hidden = true;
    coverPlaceholder.hidden = false;
  }

  renderHeaderArt(game);
  renderVersions(game);
  renderOverview(game);
  loadGameFiles(game.id);
  loadSaves(game.id);

  document.getElementById("scrape-status").textContent = "";
  document.getElementById("client-install-status").textContent = "";
  document.getElementById("igdb-results").innerHTML = "";
  document.getElementById("sgdb-results").innerHTML = "";
  document.getElementById("hltb-results").innerHTML = "";
  document.getElementById("edit-metadata-form").hidden = true;

  document.getElementById("install-status").hidden = true;
  document.getElementById("vnc-container").hidden = true;
  document.getElementById("vnc-placeholder").hidden = false;
  document.getElementById("cancel-install-btn").hidden = true;
  document.getElementById("clear-game-cache-btn").hidden = true;
  document.getElementById("start-install-btn").hidden = false;
  libFiles = null;
  cacheFiles = null;
  gameSaves = null;
  setSavesStatus("");
  filesSubtab = "all";
  renderFilesTab();
  view.classList.remove("loading");

  getInstallDefaults().then((defaults) => {
    document.getElementById("auto-mode-check").checked = defaults.autoMode;
    document.getElementById("manual-mode-check").checked = defaults.manualMode;
  });
  document.getElementById("ttl-days-input").value = "";
  installQueue = [];
  queueActive = false;

  await loadCandidates(game.id);
  await loadProtonBuilds();

  // Resume polling if there's already an active/finished session for this game.
  try {
    const session = await api(`/api/games/${game.id}/install`);
    renderInstallState(session);
    if (ACTIVE_INSTALL_STATES.includes(session.state)) {
      startPolling();
    }
  } catch (_) {
    // No session yet - fine, nothing to show.
  }
}

function relationCard(item) {
  const href =
    item.url || (item.slug ? `https://www.igdb.com/games/${item.slug}` : `https://www.igdb.com/search?type=1&q=${encodeURIComponent(item.name)}`);
  const cover = item.cover?.url ? `<img src="${escapeHtml(item.cover.url)}" loading="lazy" alt="" />` : '<div class="relation-nocover"></div>';
  return `<a class="relation-card" href="${escapeHtml(href)}" target="_blank" rel="noopener">${cover}<span>${escapeHtml(item.name)}</span></a>`;
}

function renderVersions(game) {
  const el = document.getElementById("game-versions");
  const versions = game.igdb_id ? (gamesAll || games).filter((g) => g.igdb_id === game.igdb_id) : [];
  el.hidden = versions.length < 2;
  el.innerHTML = versions
    .map((g) => {
      const lib = (libraries.find((l) => l.id === g.library_id) || {}).name || "";
      const label = libraries.length > 1 ? `${g.fs_name} (${lib})` : g.fs_name;
      return `<a class="chip${g.id === game.id ? " chip-active" : ""}" href="#game/${g.id}">${escapeHtml(label)}</a>`;
    })
    .join("");
}

const HLTB_STYLES = [
  ["main_story", "Main Story"],
  ["main_plus_extra", "Main + Extra"],
  ["completionist", "Completionist"],
];

// Seconds as "45m" under an hour, else hours to the nearest half ("12.5h").
function formatPlaytime(seconds) {
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes}m` : `${Math.round((seconds / 3600) * 2) / 2}h`;
}

// Shown only when HowLongToBeat gave this game a time; otherwise nothing at all.
function renderHltb(game) {
  const el = document.getElementById("game-hltb");
  const times = game.hltb_metadata || {};
  const cells = HLTB_STYLES.filter(([key]) => times[key] > 0).map(([key, label]) => {
    const players = times[`${key}_count`];
    return (
      `<div class="hltb-cell"><span class="hltb-label">${label}</span>` +
      `<span class="hltb-time">${formatPlaytime(times[key])}</span>` +
      (players > 0 ? `<span class="hltb-players">${players} players</span>` : "") +
      "</div>"
    );
  });
  el.innerHTML = cells.length ? `<h4>How Long To Beat</h4><div class="hltb-grid">${cells.join("")}</div>` : "";
  el.hidden = !cells.length;
}

function renderOverview(game) {
  const meta = game.igdb_metadata || {};

  const yearEl = document.getElementById("game-year");
  const released = meta.first_release_date
    ? new Date(meta.first_release_date * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
    : null;
  yearEl.textContent = released ? `Released ${released}` : "";
  yearEl.hidden = !released;

  document.getElementById("game-summary").textContent = meta.summary || game.summary || meta.storyline || "";

  const genresEl = document.getElementById("game-genres");
  genresEl.innerHTML =
    (game.saves_only
      ? '<span class="chip chip-saves" title="The game is gone from disk, its saves are kept">Saves only</span>'
      : game.missing_from_fs
        ? '<span class="chip chip-missing">Missing</span>'
        : "") +
    (game.addons_only && !game.missing_from_fs
      ? '<span class="chip chip-addons" title="No installer for the game itself">Add-ons only</span>'
      : "") +
    (game.fs_tags || []).map((t) => `<span class="chip chip-tag">${escapeHtml(t)}</span>`).join("") +
    (meta.genres || []).map((g) => `<span class="chip">${escapeHtml(g.name)}</span>`).join("");

  const ratingsEl = document.getElementById("game-age-ratings");
  ratingsEl.innerHTML = (meta.age_ratings || [])
    .map((r) => {
      const org = r.organization?.name || IGDB_RATING_CATEGORY[r.category];
      const val = r.rating_category?.rating || IGDB_RATING_VALUE[r.rating];
      if (!org || !val) return "";
      return `<span class="chip chip-rating">${escapeHtml(org)} ${escapeHtml(val)}</span>`;
    })
    .join("");

  const playerEl = document.getElementById("game-player-info");
  const modes = (meta.game_modes || []).map((m) => m.name);
  const mp = (meta.multiplayer_modes || [])[0];
  const mpBits = [];
  if (mp) {
    if (mp.onlinecoop) mpBits.push(`online co-op (up to ${mp.onlinemax || "?"})`);
    if (mp.offlinecoop) mpBits.push(`offline co-op (up to ${mp.offlinemax || "?"})`);
  }
  playerEl.textContent = [...modes, ...mpBits].join(" · ");

  renderHltb(game);

  const shotsEl = document.getElementById("game-screenshots");
  shotsEl.innerHTML = (meta.screenshots || [])
    .map((s, i) => `<img src="${escapeHtml(s.url)}" loading="lazy" data-index="${i}" />`)
    .join("");
  shotsEl.onclick = (e) => {
    const target = e.target.closest?.(".video-tile, img[data-index]");
    if (!target) return;
    // The videos and the screenshots are one gallery, in the order they sit in the row.
    const entries = Array.from(shotsEl.children).filter((el) => el.matches(".video-tile, img[data-index]"));
    const items = entries.map((el) =>
      el.matches(".video-tile")
        ? { type: "video", id: el.dataset.video, title: el.dataset.title, label: el.dataset.label }
        : { type: "image", url: (meta.screenshots || [])[Number(el.dataset.index)].url }
    );
    openGallery(items, entries.indexOf(target));
  };
  loadVideos(game, shotsEl);

  const relEl = document.getElementById("game-relations");
  const sections = [
    ["DLC", meta.dlcs],
    ["Expansions", meta.expansions],
    ["Remakes", meta.remakes],
    ["Remasters", meta.remasters],
  ];
  relEl.innerHTML = sections
    .filter(([, items]) => items && items.length)
    .map(([label, items]) => `<div class="relation-group"><h4>${label}</h4><div class="relation-grid">${items.map(relationCard).join("")}</div></div>`)
    .join("");

  const hasAnything =
    (meta.summary || game.summary || meta.storyline) ||
    released ||
    (meta.genres || []).length ||
    (meta.screenshots || []).length ||
    (meta.age_ratings || []).length;
  document.getElementById("overview-empty").hidden = !!hasAnything;
}

// --- Covers: shown whole, filling the box only when they are about its shape ---

const COVER_FILL_RANGE = [0.62, 0.72]; // width / height; the boxes are 2:3 (0.667), SteamGridDB's own shape

function fitCover(img) {
  if (!img.naturalWidth || !img.naturalHeight) return;
  const ratio = img.naturalWidth / img.naturalHeight;
  img.style.objectFit = ratio >= COVER_FILL_RANGE[0] && ratio <= COVER_FILL_RANGE[1] ? "cover" : "contain";
}

// "load" does not bubble, so one listener in the capture phase sees every cover as it arrives.
document.addEventListener(
  "load",
  (e) => {
    if (e.target.matches && e.target.matches(".game-card .cover img, .game-header-cover img")) fitCover(e.target);
  },
  true
);

// --- Videos: the intro, then the gameplay, ahead of the screenshots ---

const VIDEO_LABELS = { intro: "Intro", gameplay: "Gameplay" };

// The server gives IGDB's videos, or finds them on YouTube; the tiles go in front of the screenshots, and a
// page left for another game meanwhile ignores the answer.
async function loadVideos(game, row) {
  row.querySelectorAll(".video-tile").forEach((el) => el.remove());
  let videos = [];
  try {
    videos = (await api(`/api/games/${game.id}/videos`)).videos || [];
  } catch (_) {
    return; // no videos is not an error worth showing
  }
  if (!activeGame || activeGame.id !== game.id) return;
  row.querySelectorAll(".video-tile").forEach((el) => el.remove());
  const tiles = videos.map((v) => {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "video-tile";
    tile.dataset.video = v.video_id;
    tile.dataset.label = VIDEO_LABELS[v.kind] || "Video";
    tile.dataset.title = v.title || tile.dataset.label;
    tile.title = `${VIDEO_LABELS[v.kind] || "Video"}: ${tile.dataset.title}`;
    tile.innerHTML =
      `<img src="https://i.ytimg.com/vi/${encodeURIComponent(v.video_id)}/hqdefault.jpg" loading="lazy" alt="" />` +
      `<span class="video-play" aria-hidden="true"></span>` +
      `<span class="video-label">${escapeHtml(VIDEO_LABELS[v.kind] || "Video")}</span>`;
    return tile;
  });
  row.prepend(...tiles);
  const hasAnything = document.getElementById("overview-empty");
  if (tiles.length) hasAnything.hidden = true;
}

// --- Screenshot gallery ---

let galleryItems = []; // { type: "image", url } or { type: "video", id, title, label }
let galleryIndex = 0;

// A video's player is only built when it comes up, so a page of games loads nothing from YouTube, and
// replacing it is what stops the one that was playing.
function showGallery() {
  const item = galleryItems[galleryIndex];
  const img = document.getElementById("gallery-img");
  const slot = document.getElementById("gallery-video");
  slot.replaceChildren();
  if (item.type === "video") {
    img.hidden = true;
    img.removeAttribute("src");
    const frame = document.createElement("iframe");
    frame.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(item.id)}?autoplay=1&rel=0`;
    frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    frame.allowFullscreen = true;
    frame.title = item.title || "Video";
    slot.append(frame);
    slot.hidden = false;
  } else {
    slot.hidden = true;
    img.hidden = false;
    img.src = item.url.replace("t_screenshot_big", "t_1080p");
  }
  document.getElementById("gallery-caption").textContent = item.type === "video" ? `${item.label}: ${item.title}` : "";
  document.getElementById("gallery-count").textContent = `${galleryIndex + 1} / ${galleryItems.length}`;
}

function openGallery(items, index) {
  galleryItems = items;
  galleryIndex = index;
  showGallery();
  document.getElementById("gallery").hidden = false;
}

function closeGallery() {
  document.getElementById("gallery-video").replaceChildren();
  document.getElementById("gallery").hidden = true;
}

function stepGallery(delta) {
  galleryIndex = (galleryIndex + delta + galleryItems.length) % galleryItems.length;
  showGallery();
}

document.getElementById("gallery-prev").addEventListener("click", () => stepGallery(-1));
document.getElementById("gallery-next").addEventListener("click", () => stepGallery(1));
document.getElementById("gallery-close").addEventListener("click", closeGallery);
document.getElementById("gallery").addEventListener("click", (e) => {
  if (e.target.id === "gallery") closeGallery();
});
document.addEventListener("keydown", (e) => {
  if (document.getElementById("gallery").hidden) return;
  if (e.key === "ArrowLeft") stepGallery(-1);
  else if (e.key === "ArrowRight") stepGallery(1);
  else if (e.key === "Escape") closeGallery();
});

// --- Metadata (IGDB / SteamGridDB) ---

// --- Scrape dialog: the providers' defaults are applied, then every artwork on offer can be picked ---

const MEDIA_KINDS = [
  ["cover", "Cover", "portrait"],
  ["banner", "Banner", "wide"],
  ["hero", "Hero", "hero"],
  ["logo", "Title (logo)", "logo"],
  ["icon", "Icon", "icon"],
];

// The game's artwork around its header: the hero (else the banner) behind it, the logo in place of the
// title text and the icon beside it. Each is optional, and one that fails to load falls back quietly.
function renderHeaderArt(game) {
  const media = game.media || {};
  const url = (kind) => (media[kind] || {}).url || null;
  const header = document.getElementById("game-header");
  const art = document.getElementById("game-header-art");
  const background = url("hero") || url("banner");
  if (background) art.style.setProperty("--art", `url("${background.replace(/"/g, "%22")}")`);
  else art.style.removeProperty("--art");
  header.classList.toggle("has-art", Boolean(background));

  const show = (id, src, onBroken) => {
    const img = document.getElementById(id);
    img.onerror = () => {
      img.hidden = true;
      if (onBroken) onBroken();
    };
    if (!src) {
      img.hidden = true;
      img.removeAttribute("src");
    } else if (img.getAttribute("src") === src && img.complete) {
      img.hidden = false;
    } else {
      img.hidden = true; // the previous game's image stays until the new one has loaded
      img.onload = () => {
        img.hidden = false;
      };
      img.src = src;
    }
    return img;
  };
  const logo = show("game-logo", url("logo"), () => header.classList.remove("has-logo"));
  logo.alt = game.name;
  header.classList.toggle("has-logo", Boolean(url("logo")));
  show("game-icon", url("icon"));
}

function showCover(game) {
  const coverImg = document.getElementById("game-cover-img");
  const coverPlaceholder = document.getElementById("game-cover-placeholder");
  if (game.cover_path) {
    coverImg.src = game.cover_path;
    coverImg.hidden = false;
    coverPlaceholder.hidden = true;
  }
}

function renderMediaChoices(data, game) {
  const root = document.getElementById("scrape-modal-media");
  root.innerHTML = "";
  for (const [kind, label, shape] of MEDIA_KINDS) {
    const section = document.createElement("section");
    section.className = "media-section";
    section.innerHTML = `<h4>${label}</h4>`;
    const row = document.createElement("div");
    row.className = `media-row media-${shape}`;
    const options = data.candidates[kind] || [];
    const chosen = () => ((game.media || {})[kind] || {}).url || null;

    const pick = async (url, tile) => {
      try {
        const updated = await api(`/api/games/${game.id}/media`, { method: "PUT", body: JSON.stringify({ [kind]: url }) });
        game.media = updated.media;
        game.cover_path = updated.cover_path;
        activeGame = { ...activeGame, media: updated.media, cover_path: updated.cover_path };
        row.querySelectorAll(".media-choice").forEach((el) => el.classList.toggle("selected", el === tile));
        if (kind === "cover") showCover(updated);
        renderHeaderArt(activeGame);
      } catch (err) {
        document.getElementById("scrape-modal-status").textContent = `Could not save: ${err.message}`;
      }
    };

    const none = document.createElement("button");
    none.type = "button";
    none.className = "media-choice media-none";
    none.textContent = "None";
    none.classList.toggle("selected", chosen() === null);
    none.addEventListener("click", () => pick(null, none));
    row.appendChild(none);

    for (const option of options) {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "media-choice";
      tile.title = `${option.source === "igdb" ? "IGDB" : "SteamGridDB"}${option.width ? `, ${option.width}x${option.height}` : ""}`;
      tile.classList.toggle("selected", option.url === chosen());
      const img = document.createElement("img");
      img.src = option.thumb || option.url;
      img.loading = "lazy";
      img.alt = "";
      tile.appendChild(img);
      const source = document.createElement("span");
      source.className = "media-source";
      source.textContent = option.source === "igdb" ? "IGDB" : "SGDB";
      tile.appendChild(source);
      tile.addEventListener("click", () => pick(option.url, tile));
      row.appendChild(tile);
    }
    if (options.length === 0) {
      const empty = document.createElement("span");
      empty.className = "muted small";
      empty.textContent = "Nothing on offer from the providers.";
      row.appendChild(empty);
    }
    section.appendChild(row);
    root.appendChild(section);
  }
}

async function openScrapeModal() {
  const modal = document.getElementById("scrape-modal");
  const status = document.getElementById("scrape-modal-status");
  document.getElementById("scrape-modal-title").textContent = `Scrape: ${activeGame.name}`;
  document.getElementById("scrape-modal-media").innerHTML = "";
  document.getElementById("scrape-modal-done").disabled = true;
  status.textContent = "Fetching metadata and artwork...";
  modal.hidden = false;
  let game = activeGame;
  try {
    game = await api(`/api/games/${activeGame.id}/scrape`, { method: "POST" });
    activeGame = game;
    document.getElementById("game-title").textContent = game.name;
    renderOverview(game);
    showCover(game);
    const igdb = game.igdb_id ? "" : " No IGDB match (check the IGDB keys in Settings).";
    status.textContent = `Matched: ${game.name}. The providers' defaults are applied; pick any other artwork below if you want.${igdb}`;
  } catch (err) {
    status.textContent = `The scrape failed: ${err.message}. You can still pick artwork below.`;
  }
  try {
    const data = await api(`/api/games/${game.id}/media/candidates`);
    renderMediaChoices(data, { ...game, media: data.selected });
  } catch (err) {
    status.textContent += ` Could not list the artwork: ${err.message}`;
  }
  document.getElementById("scrape-modal-done").disabled = false;
}

async function closeScrapeModal() {
  document.getElementById("scrape-modal").hidden = true;
  document.getElementById("scrape-status").textContent = "";
  await refreshGames();
}

document.getElementById("scrape-btn").addEventListener("click", openScrapeModal);
document.getElementById("scrape-modal-done").addEventListener("click", closeScrapeModal);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !document.getElementById("scrape-modal").hidden && !document.getElementById("scrape-modal-done").disabled) {
    closeScrapeModal();
  }
});

document.getElementById("edit-metadata-btn").addEventListener("click", () => {
  document.getElementById("edit-name").value = activeGame.name;
  document.getElementById("edit-summary").value = activeGame.summary || activeGame.igdb_metadata?.summary || "";
  document.getElementById("edit-igdb-id").value = activeGame.igdb_id ?? "";
  document.getElementById("edit-sgdb-id").value = activeGame.sgdb_id ?? "";
  document.getElementById("edit-metadata-form").hidden = false;
});

document.getElementById("edit-metadata-cancel-btn").addEventListener("click", () => {
  document.getElementById("edit-metadata-form").hidden = true;
});

document.getElementById("edit-metadata-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const game = await api(`/api/games/${activeGame.id}`, {
      method: "PUT",
      body: JSON.stringify({
        name: document.getElementById("edit-name").value,
        summary: document.getElementById("edit-summary").value,
        igdb_id: document.getElementById("edit-igdb-id").value === "" ? null : parseInt(document.getElementById("edit-igdb-id").value, 10),
        sgdb_id: document.getElementById("edit-sgdb-id").value === "" ? null : parseInt(document.getElementById("edit-sgdb-id").value, 10),
      }),
    });
    activeGame = game;
    document.getElementById("game-title").textContent = game.name;
    renderOverview(game);
    document.getElementById("edit-metadata-form").hidden = true;
    await refreshGames();
    renderVersions(game);
  } catch (err) {
    alert(`Could not save: ${err.message}`);
  }
});

// Searches use the name as currently typed in the edit form, not the saved one.
function searchQuery() {
  const typed = document.getElementById("edit-name").value.trim();
  return typed ? `?${new URLSearchParams({ query: typed })}` : "";
}

document.getElementById("igdb-search-btn").addEventListener("click", async () => {
  const list = document.getElementById("igdb-results");
  list.innerHTML = "Searching...";
  try {
    const results = await api(`/api/games/${activeGame.id}/metadata/igdb/search${searchQuery()}`);
    if (results.length === 0) {
      list.innerHTML = '<li class="muted">No results (check the IGDB keys in Settings).</li>';
      return;
    }
    list.innerHTML = "";
    for (const r of results) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "match-name";
      name.textContent = r.name;
      li.appendChild(name);
      const applyBtn = document.createElement("button");
      applyBtn.textContent = "Apply";
      applyBtn.addEventListener("click", async () => {
        const game = await api(`/api/games/${activeGame.id}/metadata/igdb/${r.id}`, { method: "POST" });
        activeGame = game;
        document.getElementById("game-title").textContent = game.name;
        renderOverview(game);
        document.getElementById("scrape-status").textContent = `Matched: ${game.name}`;
        await refreshGames();
        renderVersions(game);
      });
      li.appendChild(applyBtn);
      list.appendChild(li);
    }
  } catch (err) {
    list.innerHTML = `<li class="error">${escapeHtml(err.message)}</li>`;
  }
});

document.getElementById("hltb-search-btn").addEventListener("click", async () => {
  const list = document.getElementById("hltb-results");
  list.innerHTML = "Searching...";
  try {
    const results = await api(`/api/games/${activeGame.id}/metadata/hltb/search${searchQuery()}`);
    if (results.length === 0) {
      list.innerHTML = '<li class="muted">No results with a completion time.</li>';
      return;
    }
    list.innerHTML = "";
    for (const r of results) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "match-name";
      const main = r.metadata?.main_story;
      name.textContent = main ? `${r.name} (${formatPlaytime(main)})` : r.name;
      li.appendChild(name);
      const applyBtn = document.createElement("button");
      applyBtn.type = "button";
      applyBtn.textContent = "Apply";
      applyBtn.addEventListener("click", async () => {
        try {
          const game = await api(`/api/games/${activeGame.id}/metadata/hltb/${r.id}`, { method: "POST" });
          activeGame = game;
          renderOverview(game);
          document.getElementById("scrape-status").textContent = `How Long To Beat: ${r.name}`;
          await refreshGames();
        } catch (err) {
          alert(`Could not apply: ${err.message}`);
        }
      });
      li.appendChild(applyBtn);
      list.appendChild(li);
    }
  } catch (err) {
    list.innerHTML = `<li class="error">${escapeHtml(err.message)}</li>`;
  }
});

document.getElementById("sgdb-search-btn").addEventListener("click", async () => {
  const grid = document.getElementById("sgdb-results");
  grid.innerHTML = "Searching...";
  try {
    const urls = await api(`/api/games/${activeGame.id}/metadata/sgdb/search${searchQuery()}`);
    if (urls.length === 0) {
      grid.innerHTML = '<p class="muted">No results (check the SteamGridDB key in Settings).</p>';
      return;
    }
    grid.innerHTML = "";
    for (const url of urls) {
      const img = document.createElement("img");
      img.src = url;
      img.addEventListener("click", async () => {
        const params = new URLSearchParams({ cover_path: url });
        const game = await api(`/api/games/${activeGame.id}/metadata/sgdb?${params}`, { method: "POST" });
        activeGame = game;
        const coverImg = document.getElementById("game-cover-img");
        coverImg.src = game.cover_path;
        coverImg.hidden = false;
        document.getElementById("game-cover-placeholder").hidden = true;
        await refreshGames();
      });
      grid.appendChild(img);
    }
  } catch (err) {
    grid.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
  }
});

// --- Install tab: candidates, proton builds ---

// Only directly-runnable executables belong in the "install several in a
// row" checklist - an archive/disc image needs extraction first (its own
// single-shot flow) and isn't something you'd chain with a patch .exe the
// same way. Non-executable/blacklisted files (bundled redist, DirectX, ...)
// never reach here at all: the server's own candidate detection already
// excludes them (see handler/filesystem/installer_detection.py).
const CHAINABLE_KINDS = new Set(["known installer", "executable (top level)", "executable (nested)"]);

// "Use the files as they are": picked from the list when the folder holds no installer (it is probably the game
// itself). Starting it sends extract_only with no file named.
const EXTRACT_AS_IS = { path: "", kind: "extract as it is", category: "game", extractAsIs: true };

async function loadCandidates(gameId) {
  const el = document.getElementById("game-candidates");
  el.innerHTML = "Loading installer candidates...";
  try {
    const data = await api(`/api/games/${gameId}/install/candidates`);
    const chainable = data.candidates.filter((c) => CHAINABLE_KINDS.has(c.kind));
    const portable = Boolean(data.extract_suggested);
    if (data.candidates.length === 0 && !portable) {
      el.innerHTML = '<p class="muted">No installer detected automatically - start the install anyway to pick one by hand through the installer display.</p>';
      return;
    }
    el.innerHTML = "";
    if (portable) {
      const note = document.createElement("p");
      note.className = "muted small";
      note.textContent =
        "No installer was found in this folder, so it is probably the game itself. Extract it as it is, or pick one of its executables to run as the installer.";
      el.appendChild(note);
    }
    // The "just extract" entry comes first and excludes the executables below it: it is one way to install,
    // they are another (and can be chained).
    const entries = portable ? [EXTRACT_AS_IS, ...chainable] : chainable;
    const list = document.createElement("div");
    list.className = "candidate-list";
    const checks = [];
    const select = () => {
      installQueue = entries.filter((_, j) => checks[j].checked);
    };
    entries.forEach((c, i) => {
      const label = document.createElement("label");
      label.className = "candidate-row";
      const check = document.createElement("input");
      check.type = "checkbox";
      const isBase = c.category === "game";
      check.checked = i === 0 && isBase;
      checks.push(check);
      check.addEventListener("change", () => {
        if (check.checked) {
          // Extracting as it is and running an executable do not mix.
          entries.forEach((other, j) => {
            if (j !== i && (c.extractAsIs || other.extractAsIs)) checks[j].checked = false;
          });
        }
        select();
      });
      label.appendChild(check);
      const text = document.createElement("span");
      text.textContent = c.extractAsIs
        ? "Just extract: use the files as they are, run nothing"
        : `${isBase ? "" : `[${c.category.toUpperCase()}] `}${c.path} (${fmtBytes(c.file_size_bytes)})`;
      label.appendChild(text);
      list.appendChild(label);
    });
    select();
    el.appendChild(list);
    // An archive/disc image with nothing directly executable alongside it
    // (e.g. the whole game ships as one .zip) - not chainable, but still
    // worth surfacing so Install has something to run.
    if (chainable.length === 0 && !portable && data.candidates.length > 0 && data.candidates[0].category === "game") {
      installQueue = [data.candidates[0]];
      const note = document.createElement("p");
      note.className = "muted small";
      note.textContent = `Will unpack and run: ${data.candidates[0].path}`;
      el.appendChild(note);
    }
  } catch (err) {
    el.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
  }
}

async function loadProtonBuilds() {
  const select = document.getElementById("proton-build-select");
  select.innerHTML = '<option value="">(server default)</option>';
  try {
    const data = await api("/api/games/install/proton-builds");
    for (const b of data.builds) {
      const opt = document.createElement("option");
      opt.value = b.id;
      opt.textContent = `${b.label}${b.installed ? "" : " (not installed yet)"}`;
      select.appendChild(opt);
    }
  } catch (_) {
    // Not fatal - server default still works without the dropdown populated.
  }
}

// --- Install with the MOG client ---

const CLIENT_REPO = "MOG-My-Own-Games/MOG-Client";
const CLIENT_RELEASES_URL = `https://github.com/${CLIENT_REPO}/releases`;
const CLIENT_ANSWER_WAIT_MS = 2500;
let clientRelease = null; // the latest release, once looked up

function clientLink(gameId) {
  return `mog://install/${gameId}?server=${encodeURIComponent(location.origin)}`;
}

// A page cannot ask whether a program handles a link. When one does, opening it takes the focus from the page
// (or shows a prompt that does), so the page losing it within a moment is the answer. Resolves true then, and
// false when nothing happened. It has to be called from the click itself: browsers only open programs from one.
function openClient(url) {
  return new Promise((resolve) => {
    const frame = document.createElement("iframe"); // a frame keeps an unhandled link from replacing this page
    let timer = null;
    const finish = (answered) => {
      clearTimeout(timer);
      window.removeEventListener("blur", onLeave);
      window.removeEventListener("pagehide", onLeave);
      document.removeEventListener("visibilitychange", onHidden);
      setTimeout(() => frame.remove(), 1000);
      resolve(answered);
    };
    const onLeave = () => finish(true);
    const onHidden = () => {
      if (document.hidden) finish(true);
    };
    window.addEventListener("blur", onLeave);
    window.addEventListener("pagehide", onLeave);
    document.addEventListener("visibilitychange", onHidden);
    timer = setTimeout(() => finish(false), CLIENT_ANSWER_WAIT_MS);
    frame.hidden = true;
    frame.src = url;
    document.body.appendChild(frame);
  });
}

// The installer starts on the server meanwhile, without leaving the page. The server returns the running one
// if there is one, so the client asking for the same install finds it.
async function startInstallInBackground(game) {
  // A game that is an archive with no installer needs a decision, and the client asks it better: the server
  // starts nothing for it here.
  const archive = await archiveWithoutInstaller(game.id, null);
  if (archive) return { askedByClient: archive.file_name || archive.path };
  // A folder with no installer in it needs the same decision, and the server would only wait for a pick.
  if (await folderWithoutInstaller(game.id)) return { askedByClient: game.name };
  await api(`/api/games/${game.id}/install`, { method: "POST", body: JSON.stringify({}) });
  return {};
}

async function installWithClient() {
  if (!activeGame) return;
  const game = activeGame;
  const status = document.getElementById("client-install-status");
  status.textContent = "Opening the MOG client...";
  const opened = openClient(clientLink(game.id));
  let serverError = null;
  let outcome = {};
  const started = startInstallInBackground(game)
    .then((result) => {
      outcome = result;
    })
    .catch((err) => {
      serverError = err.message;
    });
  const answered = await opened;
  await started;
  const server = serverError
    ? ` The server could not start the installer: ${serverError}`
    : outcome.askedByClient
      ? ` ${outcome.askedByClient} has no installer in it, so MOG will ask which executable to run, or to extract it as it is.`
      : " The installer is running on the server.";
  if (answered) {
    status.textContent = `MOG is installing ${game.name}.${server}`;
  } else {
    status.textContent = `The MOG client did not answer.${server}`;
    showClientModal(serverError || outcome.askedByClient ? "" : "The installer is already running on the server and will wait for the client.");
  }
}

function formatClientSize(bytes) {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MiB` : `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

// The release's file for this computer first, then the rest; macOS has no build.
function renderClientRelease(release) {
  const platform = /Windows/i.test(navigator.userAgent) ? "windows" : /Linux|X11/i.test(navigator.userAgent) ? "linux" : null;
  const matches = { windows: /\.exe$/i, linux: /\.AppImage$/i };
  const assets = (release.assets || []).filter((a) => !/^SHA256SUMS/i.test(a.name));
  const mine = platform ? assets.find((a) => matches[platform].test(a.name)) : null;
  const when = release.published_at
    ? new Date(release.published_at).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })
    : "";
  const label = { windows: "Windows (.exe)", linux: "Linux (.AppImage)" };
  const lines = [`<p><strong>${escapeHtml(release.name || release.tag_name)}</strong>${when ? ` <span class="muted">released ${when}</span>` : ""}</p>`];
  if (mine) {
    lines.push(
      `<p><a class="button-link" href="${escapeHtml(mine.browser_download_url)}">Download for ${label[platform]}</a> ` +
        `<span class="muted small">${formatClientSize(mine.size)}</span></p>`
    );
    lines.push(
      platform === "linux"
        ? '<p class="muted small">Make the file executable and run it once: it sets itself up to open links from this page.</p>'
        : '<p class="muted small">Run it once: it sets itself up to open links from this page.</p>'
    );
  } else {
    lines.push(`<p class="muted">There is no build for this system in the release.</p>`);
  }
  const others = assets.filter((a) => a !== mine);
  if (others.length) {
    lines.push(
      `<p class="muted small">Other downloads: ${others
        .map((a) => `<a href="${escapeHtml(a.browser_download_url)}">${escapeHtml(a.name)}</a>`)
        .join(", ")}</p>`
    );
  }
  lines.push(`<p class="muted small"><a href="${escapeHtml(release.html_url || CLIENT_RELEASES_URL)}" target="_blank" rel="noopener">Release notes and checksums</a></p>`);
  return lines.join("");
}

async function loadClientRelease() {
  const box = document.getElementById("client-release");
  const allReleases = `<a href="${CLIENT_RELEASES_URL}" target="_blank" rel="noopener">all releases</a>`;
  if (clientRelease) {
    box.innerHTML = renderClientRelease(clientRelease);
    return;
  }
  box.textContent = "Looking up the latest release...";
  try {
    const resp = await fetch(`https://api.github.com/repos/${CLIENT_REPO}/releases/latest`, {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (resp.status === 404) {
      box.innerHTML = `<p class="muted">No release has been published yet. See ${allReleases}.</p>`;
      return;
    }
    if (!resp.ok) throw new Error(`GitHub answered ${resp.status}`);
    clientRelease = await resp.json();
    box.innerHTML = renderClientRelease(clientRelease);
  } catch (err) {
    box.innerHTML = `<p class="muted">Could not look up the latest release (${escapeHtml(err.message)}). See ${allReleases}.</p>`;
  }
}

function showClientModal(note) {
  const noteEl = document.getElementById("client-modal-note");
  noteEl.textContent = note || "";
  noteEl.hidden = !note;
  document.getElementById("client-modal-status").textContent = "";
  document.getElementById("client-modal").hidden = false;
  loadClientRelease();
}

function closeClientModal() {
  document.getElementById("client-modal").hidden = true;
}

document.getElementById("client-install-btn").addEventListener("click", installWithClient);
document.getElementById("client-modal-close").addEventListener("click", closeClientModal);
document.getElementById("client-modal").addEventListener("click", (e) => {
  if (e.target.id === "client-modal") closeClientModal();
});
document.getElementById("client-modal-retry").addEventListener("click", async () => {
  if (!activeGame) return;
  const status = document.getElementById("client-modal-status");
  status.textContent = "Opening the MOG client...";
  if (await openClient(clientLink(activeGame.id))) {
    closeClientModal();
    document.getElementById("client-install-status").textContent = `MOG is installing ${activeGame.name}.`;
  } else {
    status.textContent = "Still no answer. Install the client first, then try again.";
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !document.getElementById("client-modal").hidden) closeClientModal();
});

// --- Install flow ---

const ARCHIVE_SOURCE_KINDS = new Set(["disc image", "archive"]);

// An archive that has no installer inside it (most likely a game that needs none) can be extracted as it is
// and taken as the install. The server says so from the archive's listing; null when it is not that case or
// the lookup fails, which never stops an install.
async function archiveWithoutInstaller(gameId, candidate) {
  try {
    let pick = candidate;
    if (!pick) {
      const found = await api(`/api/games/${gameId}/install/candidates`);
      pick = (found.candidates || []).find((c) => (c.category || "game") === "game");
    }
    if (!pick || !ARCHIVE_SOURCE_KINDS.has(pick.kind)) return null;
    const inside = await api(`/api/games/${gameId}/install/candidates?source=${encodeURIComponent(pick.path)}`);
    return inside.extract_suggested ? pick : null;
  } catch (_) {
    return null;
  }
}

// The same for a game's own folder: true when the server finds no installer in it (it is probably the game itself).
async function folderWithoutInstaller(gameId) {
  try {
    const found = await api(`/api/games/${gameId}/install/candidates`);
    return Boolean(found.extract_suggested);
  } catch (_) {
    return false;
  }
}

// Resolves "extract", "install" or null (cancelled).
function askExtractAsIs(pick) {
  return new Promise((resolve) => {
    const modal = document.getElementById("extract-modal");
    document.getElementById("extract-modal-text").textContent =
      `No installer was found in ${pick.file_name || pick.path}: it looks like a game that needs none. ` +
      "Do you want to extract its contents and use them as they are?";
    const finish = (answer) => {
      modal.hidden = true;
      modal.onclick = null;
      document.removeEventListener("keydown", onKey);
      resolve(answer);
    };
    const onKey = (e) => {
      if (e.key === "Escape") finish(null);
    };
    document.getElementById("extract-modal-extract").onclick = () => finish("extract");
    document.getElementById("extract-modal-install").onclick = () => finish("install");
    document.getElementById("extract-modal-cancel").onclick = () => finish(null);
    modal.onclick = (e) => {
      if (e.target === modal) finish(null);
    };
    document.addEventListener("keydown", onKey);
    modal.hidden = false;
  });
}

async function startInstall(candidate) {
  const body = {
    auto_mode: document.getElementById("auto-mode-check").checked,
    manual_mode: document.getElementById("manual-mode-check").checked,
    proton_build: document.getElementById("proton-build-select").value || null,
  };
  const ttlRaw = document.getElementById("ttl-days-input").value;
  if (ttlRaw !== "") body.ttl_seconds = parseInt(ttlRaw, 10) * 86400;

  if (candidate?.extractAsIs) {
    body.extract_only = true;
  } else if (candidate) {
    if (ARCHIVE_SOURCE_KINDS.has(candidate.kind)) {
      body.source_path = candidate.path;
    } else {
      body.installer_path = candidate.path;
    }
  }

  const archive = candidate?.extractAsIs ? null : await archiveWithoutInstaller(activeGame.id, candidate);
  if (archive) {
    const answer = await askExtractAsIs(archive);
    if (answer === null) return;
    // "Try to install" is said outright: left out, the server extracts an archive that has no installer in it.
    body.extract_only = answer === "extract";
  }

  const session = await api(`/api/games/${activeGame.id}/install`, { method: "POST", body: JSON.stringify(body) });
  renderInstallState(session);
  startPolling();
}

document.getElementById("start-install-btn").addEventListener("click", async () => {
  queueActive = true;
  try {
    await startInstall(installQueue[0]);
  } catch (err) {
    alert(`Could not start install: ${err.message}`);
  }
});

document.getElementById("cancel-install-btn").addEventListener("click", async () => {
  if (!activeGame) return;
  await api(`/api/games/${activeGame.id}/install/cancel`, { method: "POST" });
  stopPolling();
  const session = await api(`/api/games/${activeGame.id}/install`);
  renderInstallState(session);
});

document.getElementById("clear-game-cache-btn").addEventListener("click", async () => {
  if (!activeGame) return;
  if (!confirm("Clear this game's install cache?")) return;
  await api(`/api/games/${activeGame.id}/install`, { method: "DELETE" });
  location.hash = "";
});

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    if (!activeGame) return stopPolling();
    try {
      const session = await api(`/api/games/${activeGame.id}/install`);
      renderInstallState(session);
      if (!ACTIVE_INSTALL_STATES.includes(session.state)) {
        stopPolling();
      }
    } catch (_) {
      stopPolling();
    }
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

const AUTO_LABELS = {
  running: "Auto mode: acting",
  scanning: "Auto mode: reading the screen",
  waiting: "Auto mode: waiting",
  needs_manual: "Auto mode stuck: continue by hand",
};

function renderAutoIndicator(session) {
  const el = document.getElementById("auto-indicator");
  const active = session.auto_mode && ACTIVE_INSTALL_STATES.includes(session.state);
  el.hidden = !active;
  if (!active) return;
  const status = session.auto_status || "scanning";
  el.dataset.status = status;
  document.getElementById("auto-label").textContent = AUTO_LABELS[status] || `Auto mode: ${status}`;
  document.getElementById("auto-detail").textContent = session.auto_detail || "";
}

function renderInstallState(session) {
  const statusEl = document.getElementById("install-status");
  statusEl.hidden = false;
  document.getElementById("install-state").textContent = session.state;
  const extracting = session.extract_only && session.phase === "extracting";
  document.getElementById("install-detail").textContent = extracting
    ? `Extracting ${session.phase_detail || "the archive"} as it is`
    : session.phase_detail ||
      (session.state === "installing"
        ? session.extract_only
          ? "Extracting"
          : "Installer is running, interact with it in the display"
        : "");
  renderAutoIndicator(session);
  document.getElementById("install-error").textContent = session.error || "";

  const pct = session.bytes_total ? (session.bytes_written / session.bytes_total) * 100 : 0;
  document.getElementById("install-progress").style.width = `${pct}%`;

  document.getElementById("cancel-install-btn").hidden = !ACTIVE_INSTALL_STATES.includes(session.state);
  document.getElementById("clear-game-cache-btn").hidden = ACTIVE_INSTALL_STATES.includes(session.state);
  document.getElementById("start-install-btn").hidden = ACTIVE_INSTALL_STATES.includes(session.state);

  const vncContainer = document.getElementById("vnc-container");
  const vncFrame = document.getElementById("vnc-frame");
  vncFrame.classList.toggle("vnc-tall", /\.(sh|run)$/i.test(session.installer_path || ""));
  if (session.vnc_url) {
    vncContainer.hidden = false;
    document.getElementById("vnc-placeholder").hidden = true;
    // Token-gated (see models/install_session.py's vnc_token), not Basic
    // auth, specifically so this can be embedded: a real login prompt only
    // ever fires for a top-level navigation, never for an iframe's own
    // request.
    if (vncFrame.dataset.src !== session.vnc_url) {
      vncFrame.src = session.vnc_url;
      vncFrame.dataset.src = session.vnc_url;
    }
  } else {
    vncContainer.hidden = true;
    const placeholder = document.getElementById("vnc-placeholder");
    placeholder.hidden = false;
    placeholder.textContent = session.extract_only
      ? "Nothing is run: the archive is extracted as it is."
      : "Starts automatically once the installer is running.";
    vncFrame.removeAttribute("src");
    delete vncFrame.dataset.src;
  }

  if (session.state === "done") {
    loadCacheFiles(session.game_id, session.cache_path);
    // Checklist: this candidate is done - if more were checked, run the
    // next one straight into the same cache (see loadCandidates's own
    // note). Only when this page view actually drove the install itself
    // (queueActive) - never on a plain page-load resume of an
    // already-finished session, which would otherwise auto-start whatever
    // the candidate list happens to default to.
    if (queueActive && installQueue.length > 0) {
      installQueue.shift();
      if (installQueue.length > 0) {
        setTimeout(() => {
          startInstall(installQueue[0]).catch((err) => alert(`Could not start next install: ${err.message}`));
        }, 500);
      } else {
        queueActive = false;
      }
    }
  }
}

// Files tab: a subtab list (All files, Root, Installer cache, one per folder
// in the game directory) on the left, the selected subtab's files on the right.
let libFiles = null; // { root_path, files } from /api/games/{id}/files
let cacheFiles = null; // { gameId, cachePath, files } of a finished install
let gameSaves = null; // { devices, keep_versions } from /api/games/{id}/saves
let userDevices = []; // the signed-in user's devices, for the upload picker
let filesSubtab = "all";

const ROOT_SUBTAB = "__root__";
const CACHE_SUBTAB = "__cache__";
const SAVES_SUBTAB = "__saves__";

function topFolder(path) {
  const slash = path.indexOf("/");
  return slash < 0 ? ROOT_SUBTAB : path.slice(0, slash);
}

function folderSubtabLabel(folder, files) {
  const category = files[0].category;
  return category === "game" ? folder : category.charAt(0).toUpperCase() + category.slice(1);
}

function filesSubtabs() {
  const files = libFiles ? libFiles.files : [];
  const tabs = [{ id: "all", label: "All files", count: files.length }];
  const byFolder = new Map();
  for (const f of files) {
    const key = topFolder(f.path);
    if (!byFolder.has(key)) byFolder.set(key, []);
    byFolder.get(key).push(f);
  }
  if (byFolder.has(ROOT_SUBTAB)) tabs.push({ id: ROOT_SUBTAB, label: "Root", count: byFolder.get(ROOT_SUBTAB).length });
  if (cacheFiles && cacheFiles.files.length > 0) {
    tabs.push({ id: CACHE_SUBTAB, label: "Installer cache", count: cacheFiles.files.length });
  }
  tabs.push({ id: SAVES_SUBTAB, label: "Saves", count: savesCount() });
  const folders = [...byFolder.keys()]
    .filter((k) => k !== ROOT_SUBTAB)
    .map((k) => ({ id: k, label: folderSubtabLabel(k, byFolder.get(k)), count: byFolder.get(k).length }))
    .sort((a, b) => a.label.localeCompare(b.label));
  return [...tabs, ...folders];
}

function renderFilesTab() {
  const tabs = filesSubtabs();
  if (!tabs.some((t) => t.id === filesSubtab)) filesSubtab = "all";

  const nav = document.getElementById("files-subtabs");
  nav.innerHTML = "";
  for (const t of tabs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `subtab-btn${t.id === filesSubtab ? " active" : ""}`;
    btn.innerHTML = `<span>${escapeHtml(t.label)}</span><span class="subtab-count">${t.count}</span>`;
    btn.addEventListener("click", () => {
      filesSubtab = t.id;
      renderFilesTab();
    });
    nav.appendChild(btn);
  }

  const showCache = filesSubtab === CACHE_SUBTAB;
  const showSaves = filesSubtab === SAVES_SUBTAB;
  document.getElementById("files-panel-cache").hidden = !showCache;
  document.getElementById("files-panel-saves").hidden = !showSaves;
  document.getElementById("files-panel-library").hidden = showCache || showSaves;
  if (showCache) {
    renderCachePanel();
    return;
  }
  if (showSaves) {
    renderSavesPanel();
    return;
  }

  const files = libFiles ? libFiles.files : [];
  const shown = filesSubtab === "all" ? files : files.filter((f) => topFolder(f.path) === filesSubtab);
  const prefix = filesSubtab === "all" || filesSubtab === ROOT_SUBTAB ? "" : `${filesSubtab}/`;
  document.getElementById("files-panel-title").textContent = tabs.find((t) => t.id === filesSubtab).label;
  const root = libFiles ? libFiles.root_path : "";
  document.getElementById("game-files-root").textContent = prefix ? `${root}/${prefix}` : root;
  document.getElementById("game-files-empty").hidden = shown.length > 0;
  document.getElementById("game-files-table-body").innerHTML = shown
    .map((f) => {
      const tag = f.category === "game" || filesSubtab !== "all" ? "" : ` <span class="chip chip-tag">${escapeHtml(f.category)}</span>`;
      return `<tr><td>${escapeHtml(f.path.slice(prefix.length))}${tag}</td><td>${fmtBytes(f.size_bytes)}</td></tr>`;
    })
    .join("");
}

async function loadGameFiles(gameId) {
  try {
    libFiles = await api(`/api/games/${gameId}/files`);
  } catch (_) {
    libFiles = null;
  }
  renderFilesTab();
}

async function loadCacheFiles(gameId, cachePath) {
  try {
    const data = await api(`/api/games/${gameId}/install/files`);
    cacheFiles = { gameId, cachePath: cachePath || "", files: data.files };
  } catch (_) {
    cacheFiles = null;
  }
  renderFilesTab();
}

function renderCachePanel() {
  document.getElementById("files-cache-path").textContent = cacheFiles.cachePath;
  document.getElementById("download-cache-btn").href = `/api/games/${cacheFiles.gameId}/install/download`;
  const body = document.getElementById("files-table-body");
  body.innerHTML = "";
  for (const f of cacheFiles.files) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(f.path)}</td>
      <td>${fmtBytes(f.size_bytes)}</td>
      <td><code>${escapeHtml(f.sha1.slice(0, 10))}...</code></td>
      <td><a href="/api/games/${cacheFiles.gameId}/install/files/${encodeURIComponent(f.path)}">Download</a></td>
    `;
    body.appendChild(tr);
  }
}

// --- Saves ---

function parseDetail(detail) {
  return typeof detail === "string" ? detail : (detail && detail.code) || JSON.stringify(detail);
}

async function errorMessage(resp) {
  try {
    return parseDetail((await resp.json()).detail) || resp.statusText;
  } catch (_) {
    return resp.statusText;
  }
}

// A plain <a href> would not carry the Basic credentials, so the file is fetched with them.
async function downloadWithAuth(url, fallbackName) {
  const resp = await fetch(url, { headers: { Authorization: authHeader() } });
  if (!resp.ok) throw new Error(await errorMessage(resp));
  const blob = await resp.blob();
  const match = /filename\*=utf-8''([^;]+)/i.exec(resp.headers.get("Content-Disposition") || "");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = match ? decodeURIComponent(match[1]) : fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}

function savesCount() {
  return gameSaves ? gameSaves.devices.reduce((n, d) => n + d.versions.length, 0) : 0;
}

async function loadSaves(gameId) {
  try {
    const [saves, devices] = await Promise.all([api(`/api/games/${gameId}/saves`), api("/api/devices")]);
    if (!activeGame || activeGame.id !== gameId) return;
    gameSaves = saves;
    userDevices = devices;
  } catch (_) {
    if (!activeGame || activeGame.id !== gameId) return;
    gameSaves = null;
  }
  renderFilesTab();
}

function fmtWhen(iso) {
  return iso ? new Date(iso).toLocaleString() : "never";
}

// The newest version of the game from any machine, with the machine that made it.
function newestSave(devices) {
  let best = null;
  for (const { device, versions } of devices) {
    for (const v of versions) {
      if (!best || v.created_at > best.version.created_at || (v.created_at === best.version.created_at && v.id > best.version.id)) {
        best = { version: v, device };
      }
    }
  }
  return best;
}

function renderSavesPanel() {
  document.getElementById("saves-keep").textContent = gameSaves ? gameSaves.keep_versions : 3;
  const devices = gameSaves ? gameSaves.devices : [];
  const latest = newestSave(devices);
  const latestBtn = document.getElementById("saves-download-latest");
  latestBtn.hidden = !latest;
  if (latest) {
    latestBtn.dataset.version = latest.version.id;
    latestBtn.title = `Saved ${fmtWhen(latest.version.created_at)} on ${latest.device.name}`;
  }
  document.getElementById("saves-empty").hidden = devices.length > 0;
  document.getElementById("saves-devices").innerHTML = devices
    .map(({ device, versions }) => {
      const platform = device.platform ? ` <span class="chip">${escapeHtml(device.platform)}</span>` : "";
      const rows = versions
        .map(
          (v) => `
        <tr>
          <td>${escapeHtml(fmtWhen(v.created_at))}</td>
          <td><span class="chip chip-tag">${escapeHtml(v.trigger)}</span></td>
          <td>
            <button type="button" class="link-button" data-action="files" data-version="${v.id}" aria-expanded="false">
              <span class="caret" aria-hidden="true"></span>${v.file_count} file${v.file_count === 1 ? "" : "s"}
            </button>
          </td>
          <td>${fmtBytes(v.size_bytes)}</td>
          <td class="saves-actions">
            <button type="button" data-action="download" data-version="${v.id}">Download</button>
            <button type="button" class="danger" data-action="delete" data-version="${v.id}">Delete</button>
          </td>
        </tr>
        <tr class="saves-files-row" data-files-for="${v.id}" hidden>
          <td colspan="5"><ul class="saves-files"><li class="muted">Loading...</li></ul></td>
        </tr>`
        )
        .join("");
      return `
        <div class="saves-device">
          <div class="main-header">
            <h4>${escapeHtml(device.name)}${platform}</h4>
          </div>
          <p class="muted small">${escapeHtml(device.hostname || "")} - last seen ${escapeHtml(fmtWhen(device.last_seen))}</p>
          <table class="data-table">
            <thead><tr><th>Saved</th><th>Trigger</th><th>Files</th><th>Size</th><th></th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    })
    .join("");

  const picker = document.getElementById("saves-upload-device");
  const previous = picker.value;
  picker.innerHTML =
    userDevices.map((d) => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join("") +
    '<option value="web">This browser</option>';
  if ([...picker.options].some((o) => o.value === previous)) picker.value = previous;
}

// The outcome of the last action in the Saves panel, where it cannot be missed: green when it worked,
// red when it did not, plain for the rest (uploading, nothing new to store).
function setSavesStatus(text, kind = "") {
  const el = document.getElementById("saves-status");
  el.textContent = text;
  el.className = `saves-status ${kind}`.trim();
}

document.getElementById("saves-devices").addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-action]");
  if (!button || !activeGame) return;
  const id = button.dataset.version;
  try {
    if (button.dataset.action === "download") {
      await downloadWithAuth(`/api/saves/${id}/download`, "save.zip");
    } else if (button.dataset.action === "files") {
      await toggleSaveFiles(button);
    } else if (button.dataset.action === "delete") {
      if (!confirm("Delete this saved version from the server?")) return;
      await api(`/api/saves/${id}`, { method: "DELETE" });
      setSavesStatus("Deleted.", "success");
      await loadSaves(activeGame.id);
    }
  } catch (err) {
    setSavesStatus(`Failed: ${err.message}`, "error");
  }
});

document.getElementById("saves-download-latest").addEventListener("click", async (e) => {
  try {
    await downloadWithAuth(`/api/saves/${e.currentTarget.dataset.version}/download`, "save.zip");
  } catch (err) {
    setSavesStatus(`Failed: ${err.message}`, "error");
  }
});

// The file list of a version is fetched the first time it is opened.
async function toggleSaveFiles(button) {
  const row = button.closest("tbody").querySelector(`tr[data-files-for="${button.dataset.version}"]`);
  const open = row.hidden;
  row.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
  if (!open || row.dataset.loaded) return;
  row.dataset.loaded = "1";
  const list = row.querySelector(".saves-files");
  try {
    const version = await api(`/api/saves/${button.dataset.version}`);
    const more = version.file_count - version.manifest.length;
    list.innerHTML =
      version.manifest
        .map((f) => `<li><span class="saves-path">${escapeHtml(f.path)}</span><span class="saves-size">${fmtBytes(f.size)}</span></li>`)
        .join("") + (more > 0 ? `<li class="muted">and ${more} more</li>` : "");
  } catch (err) {
    row.dataset.loaded = "";
    list.innerHTML = `<li class="muted">${escapeHtml(err.message)}</li>`;
  }
}

// Uploads from the browser are attributed to one device of its own, created on first use.
async function webDevice() {
  let uid = localStorage.getItem("mog_web_device_uid");
  if (!uid) {
    uid = `web-${crypto.randomUUID()}`;
    localStorage.setItem("mog_web_device_uid", uid);
  }
  const register = (extra) =>
    fetch("/api/devices/register", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: authHeader() },
      body: JSON.stringify({ client_uid: uid, hostname: "Web upload", platform: "web", ...extra }),
    });
  let resp = await register({});
  if (resp.status === 409) {
    // This browser's uid was lost (cleared storage): take the earlier web device over.
    const detail = (await resp.json()).detail;
    const previous = detail && (detail.devices || []).find((d) => d.platform === "web");
    if (previous) resp = await register({ adopt_device_id: previous.id });
  }
  if (!resp.ok) throw new Error(await errorMessage(resp));
  return resp.json();
}

document.getElementById("saves-upload-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file || !activeGame) return;
  setSavesStatus("Uploading...");
  try {
    let deviceId = document.getElementById("saves-upload-device").value;
    if (deviceId === "web") deviceId = (await webDevice()).id;
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch(`/api/games/${activeGame.id}/saves?device_id=${deviceId}&trigger=manual`, {
      method: "POST",
      headers: { Authorization: authHeader() },
      body: form,
    });
    if (!resp.ok) throw new Error(await errorMessage(resp));
    const result = await resp.json();
    setSavesStatus(
      result.created
        ? `Uploaded ${file.name} (${result.version.file_count} file${result.version.file_count === 1 ? "" : "s"}).`
        : `${file.name} has the same files as the newest version of this device, so nothing new was stored.`,
      result.created ? "success" : ""
    );
    await loadSaves(activeGame.id);
  } catch (err) {
    setSavesStatus(`Upload failed: ${err.message}`, "error");
  }
});

// --- Devices (profile page) ---

async function loadDevicesTable() {
  let devices = [];
  try {
    devices = await api("/api/devices");
  } catch (_) {
    // leave the table empty
  }
  document.getElementById("devices-empty").hidden = devices.length > 0;
  document.getElementById("devices-table-body").innerHTML = devices
    .map(
      (d) => `
      <tr>
        <td>${escapeHtml(d.name)}</td>
        <td>${escapeHtml(d.hostname || "")}</td>
        <td>${escapeHtml(d.platform || "")}</td>
        <td>${escapeHtml(fmtWhen(d.last_seen))}</td>
        <td><button type="button" data-device="${d.id}" data-name="${escapeHtml(d.name)}">Rename</button></td>
      </tr>`
    )
    .join("");
}

document.getElementById("devices-table-body").addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-device]");
  if (!button) return;
  const name = prompt("New name for this device", button.dataset.name);
  if (!name || !name.trim() || name.trim() === button.dataset.name) return;
  const status = document.getElementById("devices-status");
  try {
    await api(`/api/devices/${button.dataset.device}`, {
      method: "PATCH",
      body: JSON.stringify({ name: name.trim() }),
    });
    status.textContent = "Renamed.";
    loadDevicesTable();
  } catch (err) {
    status.textContent = /^409/.test(err.message)
      ? "Another of your devices already has that name."
      : `Could not rename: ${err.message}`;
  }
});

// --- Boot ---

(function boot() {
  const user = sessionStorage.getItem("mog_user");
  const pass = sessionStorage.getItem("mog_pass");
  if (user && pass) {
    tryLogin(user, pass).catch(() => showScreen("login-screen"));
  } else {
    showScreen("login-screen");
  }
})();
