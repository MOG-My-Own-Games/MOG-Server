// MOG Server - minimal vanilla-JS web UI. No build step, no framework: this
// is a Phase-1 "can actually click through it" UI, not the Steam-like
// redesign planned for later (see docs/TODO.md) - kept intentionally plain
// so it ships inside the single server image with zero extra tooling.
//
// Routing: hash-based, one persistent shell (topbar + sidebar) with three
// pages swapped inside <main> - #/ (games grid), #/game/<id>, #/settings -
// rather than popup modals, so each has its own URL/back-button/reload
// behavior like a normal page.

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
let games = [];
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
    throw new Error(`${resp.status} ${detail}`);
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

function initTabs(navSelector, dataAttr, panelPrefix) {
  document.querySelectorAll(`${navSelector} .tab-btn`).forEach((btn) => {
    btn.addEventListener("click", () => activateTab(navSelector, dataAttr, panelPrefix, btn.dataset[dataAttr]));
  });
}
initTabs("#view-game .tabs", "tab", "tab");
initTabs("#view-settings .tabs", "settingsTab", "settings-tab");

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

async function tryLogin(user, pass) {
  creds = { user, pass };
  currentUser = await api("/api/users/me"); // throws on bad creds
  sessionStorage.setItem("mog_user", user);
  sessionStorage.setItem("mog_pass", pass);
  applyTopbarAvatar();
  showScreen("app-screen");
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
    const id = parseInt(hash.slice("game/".length), 10);
    document.getElementById("view-game").hidden = false;
    openGamePage(id);
  } else if (hash === "profile") {
    document.getElementById("view-profile").hidden = false;
    openProfilePage();
  } else if (hash.startsWith("settings")) {
    document.getElementById("view-settings").hidden = false;
    const subTab = hash.includes("/") ? hash.slice("settings/".length) : null;
    openSettingsPage(subTab);
  } else {
    document.getElementById("view-games").hidden = false;
    refreshGames();
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
}

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
    return { autoMode: d.auto_mode, manualMode: d.manual_mode };
  } catch (_) {
    return { autoMode: false, manualMode: false };
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
  document.getElementById("setting-auto-mode").onchange = saveInstallDefaults;
  document.getElementById("setting-manual-mode").onchange = saveInstallDefaults;
  document.getElementById("api-keys-saved").textContent = "";
  document.getElementById("cache-ttl-saved").textContent = "";

  renderLibraryList("settings-library-list", { clickable: false, showScrape: true, allowDelete: true });

  try {
    const apiSettings = await api("/api/settings");
    document.getElementById("setting-igdb-id").value = apiSettings.igdb_client_id || "";
    document.getElementById("setting-igdb-secret").value = apiSettings.igdb_client_secret || "";
    document.getElementById("setting-sgdb-key").value = apiSettings.steamgriddb_api_key || "";
    document.getElementById("setting-cache-ttl").value = apiSettings.install_cache_ttl_days ?? "";
    document.getElementById("proton-default-saved").textContent = "";
    await refreshProtonBuildsTable(apiSettings.install_default_proton_build || "");
  } catch (_) {
    // Not fatal - fields just stay blank until a successful load.
  }

  await refreshCacheTable();
  await refreshProviderValidityBadges();
  await refreshUsersTable();
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

async function refreshProviderValidityBadges() {
  const igdbBadge = document.getElementById("igdb-valid-badge");
  const sgdbBadge = document.getElementById("sgdb-valid-badge");
  try {
    const result = await api("/api/settings/validate");
    applyValidityBadge(igdbBadge, result.igdb_valid);
    applyValidityBadge(sgdbBadge, result.steamgriddb_valid);
  } catch (_) {
    applyValidityBadge(igdbBadge, null);
    applyValidityBadge(sgdbBadge, null);
  }
}

// --- Users (Settings > Users, admin only) ---

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
      const select = document.createElement("select");
      select.multiple = true;
      select.size = Math.min(4, Math.max(2, libraries.length || 2));
      for (const lib of libraries) {
        const opt = document.createElement("option");
        opt.value = lib.id;
        opt.textContent = lib.name;
        opt.selected = (u.hidden_library_ids || []).includes(lib.id);
        select.appendChild(opt);
      }
      select.addEventListener("change", async () => {
        const ids = Array.from(select.selectedOptions).map((o) => parseInt(o.value, 10));
        try {
          await api(`/api/users/${u.id}`, { method: "PUT", body: JSON.stringify({ hidden_library_ids: ids }) });
        } catch (err) {
          alert(`Could not update: ${err.message}`);
        }
      });
      hiddenTd.appendChild(select);
      tr.appendChild(hiddenTd);

      const actionTd = document.createElement("td");
      const delBtn = document.createElement("button");
      delBtn.textContent = "Delete";
      delBtn.className = "danger";
      delBtn.addEventListener("click", async () => {
        if (!confirm(`Delete user "${u.username}"?`)) return;
        try {
          await api(`/api/users/${u.id}`, { method: "DELETE" });
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
      btn.className = "ghost";
      if (b.installed) {
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

document.getElementById("save-api-keys-btn").addEventListener("click", async () => {
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
    await refreshProviderValidityBadges();
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

async function refreshCacheTable() {
  const body = document.getElementById("cache-table-body");
  body.innerHTML = "<tr><td colspan='5' class='muted'>Loading...</td></tr>";
  try {
    const data = await api("/api/games/install/cache");
    if (data.entries.length === 0) {
      body.innerHTML = "<tr><td colspan='5' class='muted'>No cached installs.</td></tr>";
      return;
    }
    body.innerHTML = "";
    for (const entry of data.entries) {
      const tr = document.createElement("tr");
      const game = games.find((g) => g.id === entry.game_id);
      tr.innerHTML = `
        <td>${entry.session_id}</td>
        <td>${escapeHtml(game ? game.name : entry.game_id ?? "?")}</td>
        <td>${escapeHtml(entry.state || "")}</td>
        <td>${fmtBytes(entry.size_bytes)}</td>
      `;
      const actionTd = document.createElement("td");
      const delBtn = document.createElement("button");
      delBtn.textContent = "Delete";
      delBtn.className = "ghost";
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
    body.innerHTML = `<tr><td colspan="5" class="error">${escapeHtml(err.message)}</td></tr>`;
  }
}

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

// --- Sidebar widgets: active installs, total cache size ---

async function refreshSidebarWidgets() {
  try {
    const sessions = await api("/api/games/install/active");
    const widget = document.getElementById("active-installs-widget");
    const list = document.getElementById("active-installs-list");
    widget.hidden = sessions.length === 0;
    list.innerHTML = "";
    for (const s of sessions) {
      const game = games.find((g) => g.id === s.game_id);
      const li = document.createElement("li");
      const pct = s.bytes_total ? (s.bytes_written / s.bytes_total) * 100 : 0;
      li.innerHTML = `
        <span class="ai-name">${escapeHtml(game ? game.name : `Game ${s.game_id}`)}</span>
        <span class="muted small">${escapeHtml(s.state)}</span>
        <div class="ai-progress"><div class="ai-progress-fill" style="width:${pct}%"></div></div>
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
    allItem.textContent = "All games";
    allItem.className = selectedLibraryId === null ? "active" : "";
    allItem.addEventListener("click", () => {
      selectedLibraryId = null;
      location.hash = "";
      refreshGames();
    });
    list.appendChild(allItem);
  }

  for (const lib of libraries) {
    const li = document.createElement("li");
    li.className = clickable && selectedLibraryId === lib.id ? "active" : "";

    const info = document.createElement("div");
    info.innerHTML = `<span class="lib-name">${escapeHtml(lib.name)}</span><span class="lib-path">${escapeHtml(lib.root_path)}</span>`;
    if (clickable) {
      info.addEventListener("click", () => {
        selectedLibraryId = lib.id;
        location.hash = "";
        refreshGames();
      });
    }
    li.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "library-actions";

    const scanBtn = document.createElement("button");
    scanBtn.textContent = "Scan";
    scanBtn.className = "ghost";
    scanBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      scanBtn.textContent = "...";
      try {
        await api(`/api/libraries/${lib.id}/scan`, { method: "POST" });
        await refreshGames();
      } finally {
        scanBtn.textContent = "Scan";
      }
    });
    actions.appendChild(scanBtn);

    if (showScrape) {
      const scrapeBtn = document.createElement("button");
      scrapeBtn.textContent = "Scrape";
      scrapeBtn.className = "ghost";
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

async function refreshGames() {
  const qs = selectedLibraryId !== null ? `?library_id=${selectedLibraryId}` : "";
  games = await api(`/api/games${qs}`);
  const libName = selectedLibraryId === null ? "All Games" : (libraries.find((l) => l.id === selectedLibraryId) || {}).name || "Games";
  document.getElementById("games-heading").textContent = `${libName} (${games.length})`;
  renderGameGrid(document.getElementById("game-search").value.trim().toLowerCase());
}

// Corner badge on a cover: the game has a finished install on the server.
const INSTALLED_BADGE =
  '<span class="installed-badge" title="Installed"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg></span>';

function renderGameGrid(filterText) {
  const grid = document.getElementById("game-grid");
  grid.innerHTML = "";
  const visible = filterText ? games.filter((g) => g.name.toLowerCase().includes(filterText)) : games;
  for (const game of visible) {
    const card = document.createElement("div");
    card.className = "game-card";
    card.title = game.name;
    card.innerHTML = `
      <div class="cover${game.cover_path ? "" : " no-cover"}">${game.cover_path ? `<img src="${escapeHtml(game.cover_path)}" />` : "\u{1F3AE}"}${game.installed ? INSTALLED_BADGE : ""}</div>
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
  renderGameGrid(e.target.value.trim().toLowerCase());
});

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

async function openGamePage(id) {
  resetGameTabs();
  let game;
  try {
    game = await api(`/api/games/${id}`);
  } catch (err) {
    document.getElementById("game-title").textContent = "Not found";
    document.getElementById("game-summary").textContent = err.message;
    return;
  }
  activeGame = game;
  selectedCandidate = null;

  document.getElementById("game-title").textContent = game.name;
  document.getElementById("game-id-display").textContent = game.id;
  const libName = (libraries.find((l) => l.id === game.library_id) || {}).name || "";
  document.getElementById("game-library").textContent = libName;

  const coverImg = document.getElementById("game-cover-img");
  const coverPlaceholder = document.getElementById("game-cover-placeholder");
  if (game.cover_path) {
    coverImg.src = game.cover_path;
    coverImg.hidden = false;
    coverPlaceholder.hidden = true;
  } else {
    coverImg.hidden = true;
    coverPlaceholder.hidden = false;
  }

  renderOverview(game);

  document.getElementById("scrape-status").textContent = "";
  document.getElementById("igdb-results").innerHTML = "";
  document.getElementById("sgdb-results").innerHTML = "";
  document.getElementById("edit-metadata-form").hidden = true;

  document.getElementById("install-status").hidden = true;
  document.getElementById("vnc-container").hidden = true;
  document.getElementById("vnc-placeholder").hidden = false;
  document.getElementById("cancel-install-btn").hidden = true;
  document.getElementById("clear-game-cache-btn").hidden = true;
  document.getElementById("start-install-btn").hidden = false;
  document.getElementById("files-empty").hidden = false;
  document.getElementById("files-cache-path").textContent = "";
  document.getElementById("files-table-body").innerHTML = "";

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

function renderOverview(game) {
  const meta = game.igdb_metadata || {};

  document.getElementById("game-summary").textContent = meta.summary || game.summary || meta.storyline || "";

  const genresEl = document.getElementById("game-genres");
  genresEl.innerHTML = (meta.genres || []).map((g) => `<span class="chip">${escapeHtml(g.name)}</span>`).join("");

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

  const shotsEl = document.getElementById("game-screenshots");
  shotsEl.innerHTML = (meta.screenshots || [])
    .map((s, i) => `<img src="${escapeHtml(s.url)}" loading="lazy" data-index="${i}" />`)
    .join("");
  shotsEl.onclick = (e) => {
    const idx = e.target.dataset?.index;
    if (idx !== undefined) openGallery((meta.screenshots || []).map((s) => s.url), Number(idx));
  };

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
    (meta.genres || []).length ||
    (meta.screenshots || []).length ||
    (meta.age_ratings || []).length;
  document.getElementById("overview-empty").hidden = !!hasAnything;
}

// --- Screenshot gallery ---

let galleryUrls = [];
let galleryIndex = 0;

function showGallery() {
  document.getElementById("gallery-img").src = galleryUrls[galleryIndex];
  document.getElementById("gallery-count").textContent = `${galleryIndex + 1} / ${galleryUrls.length}`;
}

function openGallery(urls, index) {
  galleryUrls = urls.map((u) => u.replace("t_screenshot_big", "t_1080p"));
  galleryIndex = index;
  showGallery();
  document.getElementById("gallery").hidden = false;
}

function stepGallery(delta) {
  galleryIndex = (galleryIndex + delta + galleryUrls.length) % galleryUrls.length;
  showGallery();
}

document.getElementById("gallery-prev").addEventListener("click", () => stepGallery(-1));
document.getElementById("gallery-next").addEventListener("click", () => stepGallery(1));
document.getElementById("gallery-close").addEventListener("click", () => (document.getElementById("gallery").hidden = true));
document.getElementById("gallery").addEventListener("click", (e) => {
  if (e.target.id === "gallery") e.target.hidden = true;
});
document.addEventListener("keydown", (e) => {
  if (document.getElementById("gallery").hidden) return;
  if (e.key === "ArrowLeft") stepGallery(-1);
  else if (e.key === "ArrowRight") stepGallery(1);
  else if (e.key === "Escape") document.getElementById("gallery").hidden = true;
});

// --- Metadata (IGDB / SteamGridDB) ---

document.getElementById("scrape-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("scrape-status");
  statusEl.textContent = "Searching...";
  try {
    const game = await api(`/api/games/${activeGame.id}/scrape`, { method: "POST" });
    activeGame = game;
    document.getElementById("game-title").textContent = game.name;
    renderOverview(game);
    const coverImg = document.getElementById("game-cover-img");
    const coverPlaceholder = document.getElementById("game-cover-placeholder");
    if (game.cover_path) {
      coverImg.src = game.cover_path;
      coverImg.hidden = false;
      coverPlaceholder.hidden = true;
    }
    statusEl.textContent = `Matched: ${game.name}`;
    await refreshGames();
  } catch (err) {
    statusEl.textContent = `Failed: ${err.message}`;
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
  } catch (err) {
    alert(`Could not save: ${err.message}`);
  }
});

document.getElementById("igdb-search-btn").addEventListener("click", async () => {
  const list = document.getElementById("igdb-results");
  list.innerHTML = "Searching...";
  try {
    const results = await api(`/api/games/${activeGame.id}/metadata/igdb/search`);
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
      applyBtn.className = "ghost";
      applyBtn.textContent = "Apply";
      applyBtn.addEventListener("click", async () => {
        const game = await api(`/api/games/${activeGame.id}/metadata/igdb/${r.id}`, { method: "POST" });
        activeGame = game;
        document.getElementById("game-title").textContent = game.name;
        renderOverview(game);
        document.getElementById("scrape-status").textContent = `Matched: ${game.name}`;
        await refreshGames();
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
    const urls = await api(`/api/games/${activeGame.id}/metadata/sgdb/search`);
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

async function loadCandidates(gameId) {
  const el = document.getElementById("game-candidates");
  el.innerHTML = "Loading installer candidates...";
  try {
    const data = await api(`/api/games/${gameId}/install/candidates`);
    const chainable = data.candidates.filter((c) => CHAINABLE_KINDS.has(c.kind));
    if (data.candidates.length === 0) {
      el.innerHTML = '<p class="muted">No installer detected automatically - start the install anyway to pick one by hand through the installer display.</p>';
      return;
    }
    el.innerHTML = "";
    const list = document.createElement("div");
    list.className = "candidate-list";
    chainable.forEach((c, i) => {
      const label = document.createElement("label");
      label.className = "candidate-row";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = i === 0;
      if (i === 0) installQueue = [c];
      check.addEventListener("change", () => {
        installQueue = chainable.filter((_, j) => list.children[j].querySelector("input").checked);
      });
      label.appendChild(check);
      const text = document.createElement("span");
      text.textContent = `${c.path} (${fmtBytes(c.file_size_bytes)})`;
      label.appendChild(text);
      list.appendChild(label);
    });
    el.appendChild(list);
    // An archive/disc image with nothing directly executable alongside it
    // (e.g. the whole game ships as one .zip) - not chainable, but still
    // worth surfacing so Install has something to run.
    if (chainable.length === 0 && data.candidates.length > 0) {
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

// --- Install flow ---

const ARCHIVE_SOURCE_KINDS = new Set(["disc image", "archive"]);

async function startInstall(candidate) {
  const body = {
    auto_mode: document.getElementById("auto-mode-check").checked,
    manual_mode: document.getElementById("manual-mode-check").checked,
    proton_build: document.getElementById("proton-build-select").value || null,
  };
  const ttlRaw = document.getElementById("ttl-days-input").value;
  if (ttlRaw !== "") body.ttl_seconds = parseInt(ttlRaw, 10) * 86400;

  if (candidate) {
    if (ARCHIVE_SOURCE_KINDS.has(candidate.kind)) {
      body.source_path = candidate.path;
    } else {
      body.installer_path = candidate.path;
    }
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
  document.getElementById("install-detail").textContent =
    session.phase_detail || (session.state === "installing" ? "Installer is running, interact with it in the display" : "");
  renderAutoIndicator(session);
  document.getElementById("install-error").textContent = session.error || "";

  const pct = session.bytes_total ? (session.bytes_written / session.bytes_total) * 100 : 0;
  document.getElementById("install-progress").style.width = `${pct}%`;

  document.getElementById("cancel-install-btn").hidden = !ACTIVE_INSTALL_STATES.includes(session.state);
  document.getElementById("clear-game-cache-btn").hidden = ACTIVE_INSTALL_STATES.includes(session.state);
  document.getElementById("start-install-btn").hidden = ACTIVE_INSTALL_STATES.includes(session.state);

  const vncContainer = document.getElementById("vnc-container");
  const vncFrame = document.getElementById("vnc-frame");
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
    document.getElementById("vnc-placeholder").hidden = false;
    vncFrame.removeAttribute("src");
    delete vncFrame.dataset.src;
  }

  if (session.state === "done") {
    loadFilesTab(session.game_id, session.cache_path);
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

async function loadFilesTab(gameId, cachePath) {
  document.getElementById("files-cache-path").textContent = cachePath || "";
  document.getElementById("download-cache-btn").href = `/api/games/${gameId}/install/download`;
  const body = document.getElementById("files-table-body");
  const emptyEl = document.getElementById("files-empty");
  try {
    const data = await api(`/api/games/${gameId}/install/files`);
    if (data.files.length === 0) {
      emptyEl.hidden = false;
      body.innerHTML = "";
      return;
    }
    emptyEl.hidden = true;
    body.innerHTML = "";
    for (const f of data.files) {
      const tr = document.createElement("tr");
      const pathTd = document.createElement("td");
      pathTd.textContent = f.path;
      tr.appendChild(pathTd);
      const sizeTd = document.createElement("td");
      sizeTd.textContent = fmtBytes(f.size_bytes);
      tr.appendChild(sizeTd);
      const sha1Td = document.createElement("td");
      sha1Td.innerHTML = `<code>${f.sha1.slice(0, 10)}...</code>`;
      tr.appendChild(sha1Td);
      const linkTd = document.createElement("td");
      const link = document.createElement("a");
      link.href = `/api/games/${gameId}/install/files/${encodeURIComponent(f.path)}`;
      link.textContent = "Download";
      linkTd.appendChild(link);
      tr.appendChild(linkTd);
      body.appendChild(tr);
    }
  } catch (_) {
    emptyEl.hidden = false;
    body.innerHTML = "";
  }
}

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
