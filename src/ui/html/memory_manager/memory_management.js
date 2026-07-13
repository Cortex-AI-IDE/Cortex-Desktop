/* CORTEX SETTINGS — JS: Navigation, Modals, Memory Bridge, Live Settings Hydration */
(function () {
  "use strict";

  let bridge = null, bridgeInitAttempts = 0, bridgeInitScheduled = false;
  let state = { enabled: true, activeScope: "project", scopes: { project: { name: "Current Project", projectRoot: "", memoryDir: "", memories: [] }, global: { name: "Global", memoryDir: "", memories: [] } } };
  let uiState = { query: "", type: "all", isSearchMode: false, searchQuery: "" };
  const $ = (id) => document.getElementById(id);

  /* ═══════ Simple Markdown Renderer ═══════ */
  function renderMarkdown(text) {
    if (!text) return '';
    var html = text;
    // Escape HTML first
    html = html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // Code blocks (```)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="md-code-block"><code>$2</code></pre>');
    // Inline code (`)
    html = html.replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>');
    // Headers
    html = html.replace(/^### (.+)$/gm, '<h4 class="md-h4">$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3 class="md-h3">$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h2 class="md-h2">$1</h2>');
    // Bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Italic
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    // Unordered lists
    html = html.replace(/^- (.+)$/gm, '<li class="md-li">$1</li>');
    // Wrap consecutive li elements in ul
    html = html.replace(/((?:<li class="md-li">.*<\/li>\s*)+)/g, '<ul class="md-ul">$1</ul>');
    // Ordered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li class="md-li">$1</li>');
    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" class="md-link">$1</a>');
    // Horizontal rules
    html = html.replace(/^---$/gm, '<hr class="md-hr">');
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    return html;
  }

  /* ═══════════════════════════════════════════════════════════════
     SETTINGS MAP — HTML control ID → Python settings dotted path
     Maps every <input/select/textarea/toggle> ID in the HTML to
     the corresponding key in ~/.cortex/settings.json.
     ═══════════════════════════════════════════════════════════════ */
  const SETTINGS_MAP = {
    /* General */
    restoreSession: "memory.restore_session",
    checkUpdates: "ui.check_updates",
    notifications: "notifications.task_complete_enabled",
    soundAlerts: "notifications.sound_alerts",
    telemetry: "ui.telemetry",

    /* Appearance — Editor */
    theme: "theme",
    editorFontSize: "editor.font_size",
    editorFont: "editor.font_family",
    tabSize: "editor.tab_size",
    wordWrap: "editor.word_wrap",
    minimap: "editor.minimap",

    /* Appearance — Interface */
    uiScale: "ui.ui_scale",
    sidebarPosition: "layout.sidebar_position",

    /* Models & Providers */
    defaultModel: "ai.model",
    openaiKey: "ai.openai_key",
    deepseekKey: "ai.deepseek_key",
    mimoKey: "ai.mimo_key",
    openrouterKey: "ai.openrouter_key",
    alibabaKey: "ai.alibaba_key",
    kimiKey: "ai.kimi_key",
    mistralKey: "ai.mistral_key",
    siliconflowKey: "ai.siliconflow_key",
    /* Personalization */
    systemInstructions: "ai.system_instructions",
    verbosity: "ai.verbosity",
    codeStyle: "ai.code_style",
    rememberConvos: "memory.remember_conversations",
    contextWindow: "ai.context_window",

    /* Safety & Permissions */
    allowFileCreate: "safety.allow_file_create",
    allowFileDelete: "safety.allow_file_delete",
    allowTerminal: "safety.allow_terminal",
    requireApproval: "safety.require_approval",
    privacyMode: "safety.privacy_mode",
    localOnly: "safety.local_only",
    agenticLoopMode: "ai.agentic_loop_mode",

    /* Git */
    autoCommit: "git.auto_commit",
    commitPrefix: "git.commit_prefix",
    defaultBranch: "git.default_branch",

    /* Terminal */
    defaultShell: "terminal.default_shell",
    shellArgs: "terminal.shell_args",
    termFontSize: "terminal.font_size",
    scrollback: "terminal.scrollback",
    cursorStyle: "terminal.cursor_style",
    copyOnSelect: "terminal.copy_on_select",

    /* Network */
    requestTimeout: "ai.request_timeout",
    proxy: "network.proxy",
  };

  /* Reverse map: dotted path → control ID (for quick lookup) */
  const _pathToId = {};
  for (const [id, path] of Object.entries(SETTINGS_MAP)) _pathToId[path] = id;

  /* ═══════ Bridge helpers ═══════ */
  function resolveBridgeMethod(obj, names) { for (const n of names) { if (obj && typeof obj[n] === "function") return obj[n].bind(obj); } return null; }
  function callBridge(methodNames, args = [], timeoutMs = 6000) {
    const names = Array.isArray(methodNames) ? methodNames : [methodNames];
    return new Promise((resolve, reject) => {
      const fn = resolveBridgeMethod(bridge, names);
      if (!fn) return reject(new Error("Bridge unavailable: " + names.join(", ")));
      let settled = false;
      const timer = setTimeout(() => { if (!settled) { settled = true; reject(new Error("Timeout: " + names[0])); } }, timeoutMs);
      try { fn(...args, (r) => { if (!settled) { settled = true; clearTimeout(timer); resolve(r); } }); } catch (e) { if (!settled) { settled = true; clearTimeout(timer); reject(e); } }
    });
  }

  /* Run fn once the QWebChannel bridge is connected. `bridge` is assigned
     asynchronously by the QWebChannel callback, and in compiled builds
     QtWebEngine can take longer than any fixed delay to connect — a missed
     one-shot retry left saved API keys looking empty ("Paste key...") and
     provider toggles at defaults even though Credential Manager had the
     keys. Poll 200ms up to 20s, then run fn anyway so the no-bridge
     fallbacks (standalone/demo mode) still apply. */
  function whenBridgeReady(fn, attempt = 0) {
    if (bridge || attempt >= 100) { fn(); return; }
    setTimeout(() => whenBridgeReady(fn, attempt + 1), 200);
  }

  /* ═══════ Section Nav ═══════ */
  function switchSection(id) {
    document.querySelectorAll(".settings-section").forEach(s => s.classList.remove("active"));
    document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
    const sec = document.querySelector('.settings-section[data-section="' + id + '"]');
    const nav = document.querySelector('.nav-item[data-section="' + id + '"]');
    if (sec) sec.classList.add("active");
    if (nav) nav.classList.add("active");
    // Fetch version when About section is shown
    if (id === 'about' && bridge) {
      callBridge(["getVersion"], []).then(v => {
        const el = $("appVersion");
        if (el && v) el.textContent = "Version " + v;
      }).catch(() => {});
    }
  }

  /* ═══════ Upgrade Modal ═══════ */
  function showUpgradeModal() { $("upgradeModal").classList.remove("hidden"); }
  function hideUpgradeModal() { $("upgradeModal").classList.add("hidden"); }

  /* ═══════ Toast ═══════ */
  function showToast(msg, ms = 2500) {
    const host = $("toastHost"), t = document.createElement("div");
    t.textContent = msg;
    Object.assign(t.style, { background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "12px", padding: "10px 18px", color: "var(--text)", fontSize: "13px", marginTop: "8px", boxShadow: "0 8px 24px rgba(0,0,0,.3)", animation: "fadeInSection .2s ease", fontFamily: "var(--font-ui)" });
    host.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 300); }, ms);
  }

  /* ═══════ Helpers ═══════ */
  function esc(s) { const d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }
  function timeAgo(ts) { const d = Date.now() - new Date(ts).getTime(); if (d < 60000) return "just now"; if (d < 3600000) return Math.floor(d / 60000) + "m ago"; if (d < 86400000) return Math.floor(d / 3600000) + "h ago"; return Math.floor(d / 86400000) + "d ago"; }

  /* Flatten a nested dict into dotted-path keys: {a:{b:1}} → {"a.b":1} */
  function flattenObj(obj, prefix) {
    prefix = prefix || "";
    const out = {};
    for (const [k, v] of Object.entries(obj || {})) {
      const path = prefix ? prefix + "." + k : k;
      if (v !== null && typeof v === "object" && !Array.isArray(v)) {
        Object.assign(out, flattenObj(v, path));
      } else {
        out[path] = v;
      }
    }
    return out;
  }

  /* ═══════ LIVE SETTINGS HYDRATION ═══════
     Called once on bridge connect with the full nested settings dict
     from Python's getSettings(). Iterates SETTINGS_MAP and sets every
     matching HTML control to its saved value. */
  function applySettingsFromBridge(data) {
    if (!data || typeof data !== "object") return;
    const flat = flattenObj(data);          // e.g. {"editor.font_size": 14, "ai.model": "gpt-4o", ...}

    /* Walk every mapped control */
    for (const [ctrlId, settingsPath] of Object.entries(SETTINGS_MAP)) {
      const el = $(ctrlId);
      if (!el) continue;
      const val = flat[settingsPath];
      if (val === undefined || val === null) continue;

      if (el.type === "checkbox") {
        el.checked = !!val;
      } else if (el.type === "range") {
        el.value = val;
        /* Update the sibling <span class="range-value"> */
        const valEl = $(ctrlId + "Val");
        if (valEl) {
          const unit = valEl.textContent.replace(/[\d.]+/, "");
          valEl.textContent = val + unit;
        }
      } else if (el.tagName === "TEXTAREA") {
        el.value = val;
      } else {
        el.value = val;
      }
    }

    /* Theme picker — special case (button group, not a single control) */
    const theme = flat["theme"];
    if (theme) {
      document.querySelectorAll(".theme-option").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.theme === theme);
      });
    }

    /* AI model — highlight the matching option if it exists */
    const model = flat["ai.model"];
    if (model) {
      const sel = $("defaultModel");
      if (sel) {
        const match = Array.from(sel.options).find(o => o.value === model);
        if (match) sel.value = model;
      }
    }

    console.info("[SETTINGS] Hydrated", Object.keys(SETTINGS_MAP).length, "controls from bridge");
  }

  /* ═══════ Memory List ═══════ */
  function renderMemoryList() {
    const scope = state.scopes[state.activeScope]; if (!scope) return;
    const list = $("listView"), empty = $("emptyState"), memories = scope.memories || [];
    console.log('[Memory] renderMemoryList: scope=' + state.activeScope + ', count=' + memories.length);
    if (memories.length > 0) {
      console.log('[Memory] First memory:', JSON.stringify(memories[0]).substring(0, 200));
    }
    let filtered = memories;
    if (uiState.query) { const q = uiState.query.toLowerCase(); filtered = filtered.filter(m => (m.title || m.name || "").toLowerCase().includes(q) || (m.content || m.body || "").toLowerCase().includes(q) || (m.source_file || m.filename || "").toLowerCase().includes(q)); }
    $("countLabel").textContent = filtered.length + " memor" + (filtered.length === 1 ? "y" : "ies");
    if (filtered.length === 0) { list.innerHTML = ""; if (empty) empty.classList.remove("hidden"); return; }
    if (empty) empty.classList.add("hidden");
    list.innerHTML = filtered.map((m, i) => {
      const title = m.title || m.name || "Untitled";
      const content = m.content || m.body || "";
      const type = m.type || "general";
      const sourceFile = m.source_file || m.filename || "";
      const age = m.created_at ? timeAgo(m.created_at) : (m.age || "");
      const sim = m._similarity != null ? '<span style="display:inline-block;background:linear-gradient(135deg,var(--accent-2),var(--accent));color:#0e1116;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;margin-right:6px;">' + Math.round(m._similarity * 100) + '%</span>' : "";
      const keywords = (m.keywords || []).slice(0, 3).map(k => '<span class="memory-keyword">' + esc(k) + '</span>').join("");
      // Render markdown content as HTML instead of raw text
      const renderedContent = renderMarkdown(content.slice(0, 2000));
      const hasMore = content.length > 2000;
      return `<div class="memory-card">
        <div class="memory-header">
          <div class="memory-info">
            <div class="memory-title">${sim}${esc(title)}</div>
            <div class="memory-meta">
              <span class="memory-type">${esc(type)}</span>
              ${sourceFile ? '<span class="memory-file">' + esc(sourceFile.split(/[\\\/]/).pop()) + '</span>' : ''}
              ${age ? '<span class="memory-age">' + esc(age) + '</span>' : ''}
            </div>
            ${keywords ? '<div class="memory-keywords">' + keywords + '</div>' : ''}
          </div>
          <button class="setting-btn danger-btn memory-delete-btn" onclick="window._deleteMemory(${i})">Delete</button>
        </div>
        <div class="memory-content">${renderedContent}${hasMore ? '<div class="memory-show-more" onclick="this.previousElementSibling.style.maxHeight=\'none\';this.remove();">Show more...</div>' : ''}</div>
      </div>`;
    }).join("");
  }

  /* ═══════ State Apply (Memory) ═══════ */
  function applyState(data) {
    if (!data) return;
    state.enabled = data.enabled !== false;
    if (data.scopes) { for (const k of Object.keys(data.scopes)) { if (!state.scopes[k]) state.scopes[k] = { name: k, projectRoot: "", memoryDir: "", memories: [] }; Object.assign(state.scopes[k], data.scopes[k]); } }
    const toggle = $("enabledToggle"); if (toggle) toggle.checked = state.enabled;
    const dot = $("statusDot"); if (dot) dot.classList.toggle("enabled", state.enabled);
    if (data.activeScope) state.activeScope = data.activeScope;
    document.querySelectorAll(".scope-tab").forEach(t => t.classList.toggle("active", t.dataset.scope === state.activeScope));
    renderMemoryList();
  }

  window.receiveMemoryState = function (data) { try { applyState(typeof data === "string" ? JSON.parse(data) : data); window.__memMgrDebug.loaded = true; } catch (e) { console.error("[SETTINGS] parse error:", e); } };

  /* ═══════ Bridge Init ═══════ */
  function scheduleBridgeInitRetry() { if (bridge || bridgeInitScheduled) return; bridgeInitScheduled = true; setTimeout(() => { bridgeInitScheduled = false; initBridge(); }, 200); }
  function initBridge() {
    const transport = (typeof qt !== "undefined" && qt.webChannelTransport) || (typeof window !== "undefined" && window.qt && window.qt.webChannelTransport) || null;
    if (!transport) { if (++bridgeInitAttempts < 40) scheduleBridgeInitRetry(); return; }
    try {
      new QWebChannel(transport, (ch) => {
        bridge = ch.objects.bridge || ch.objects.cortex_bridge || null;
        if (!bridge) { const keys = Object.keys(ch.objects || {}); if (keys.length) bridge = ch.objects[keys[0]]; }
        if (bridge) {
          console.info("[SETTINGS] Bridge connected");
          /* Fresh server data (fetched off the GUI thread by Python's
             refreshServerData) — re-run the loaders, which now read the
             updated cache instantly. refresh=false prevents a loop. */
          if (bridge.server_data_ready && bridge.server_data_ready.connect) {
            bridge.server_data_ready.connect(function (kind) {
              try {
                if (kind === 'usage') loadUsageStats(false);
                else loadProfile(false);
              } catch (e) { console.warn('[SETTINGS] server_data_ready handler:', e); }
            });
          }
          /* Load memory state */
          const loadFn = bridge.loadInitialData || bridge.getState || bridge.refresh;
          if (typeof loadFn === "function") loadFn.call(bridge, (s) => window.receiveMemoryState(s));
          /* Load ALL settings and hydrate every control */
          if (typeof bridge.getSettings === "function") {
            bridge.getSettings((raw) => {
              if (!raw) return;
              try {
                const data = typeof raw === "string" ? JSON.parse(raw) : raw;
                applySettingsFromBridge(data);
              } catch (e) { console.error("[SETTINGS] getSettings parse error:", e); }
            });
          }
          /* Apply current theme from Python.
             getTheme() returns the RAW setting (dark/light/system) — used
             only to highlight which button is active. "system" is not a
             CSS state and must never be written to data-theme directly: it
             matches no CSS rule, so the page silently fell back to
             whatever the default look was regardless of actual OS
             preference (the "System picks dark even in light mode" bug).
             getResolvedTheme() always returns the real dark/light
             appearance to draw, resolving "system" via the OS preference
             the same way every other panel does. */
          if (typeof bridge.getTheme === "function") {
            bridge.getTheme((theme) => {
              if (theme) {
                document.querySelectorAll(".theme-option").forEach(b => {
                  b.classList.toggle("active", b.dataset.theme === theme);
                });
                console.info("[SETTINGS] Theme setting:", theme);
              }
            });
          }
          if (typeof bridge.getResolvedTheme === "function") {
            bridge.getResolvedTheme((resolved) => {
              if (resolved === "dark" || resolved === "light") {
                document.documentElement.setAttribute("data-theme", resolved);
                console.info("[SETTINGS] Theme appearance:", resolved);
              }
            });
          } else if (typeof bridge.getTheme === "function") {
            /* Fallback for older bridges without getResolvedTheme */
            bridge.getTheme((theme) => {
              if (theme === "dark" || theme === "light") {
                document.documentElement.setAttribute("data-theme", theme);
              }
            });
          }
          /* Load profile and usage data after bridge connects */
          loadProfile();
          loadUsageStats();
        }
      });
    } catch (e) { if (++bridgeInitAttempts < 20) scheduleBridgeInitRetry(); }
  }

  /* ═══════ Persist a setting via bridge ═══════
     Sends the dotted path (e.g. "editor.font_size") to Python's setSetting(). */
  function persistSetting(ctrlId, value) {
    const settingsPath = SETTINGS_MAP[ctrlId];
    if (!settingsPath) return; // unmapped control — skip
    if (bridge && typeof bridge.setSetting === "function") {
      bridge.setSetting(settingsPath, String(value));
    }
  }

  window._deleteMemory = function (idx) {
    const scope = state.scopes[state.activeScope]; if (!scope || !scope.memories[idx]) return;
    const mem = scope.memories[idx];
    // Prefer full absolute path (mem.path) over relative filename
    const deletePath = mem.path || mem.filename || mem.source_file || mem.id || idx;
    console.log('[Memory] _deleteMemory: idx=' + idx + ', deletePath=' + deletePath);
    callBridge(["deleteMemory", "delete_memory"], [state.activeScope, deletePath])
      .then((newState) => {
        // Use the state returned by the bridge (re-reads from disk)
        if (newState) {
          try {
            window.receiveMemoryState(typeof newState === "string" ? JSON.parse(newState) : newState);
          } catch (e) { console.error('[Memory] delete refresh parse error:', e); }
        }
        showToast("Memory deleted");
      })
      .catch((err) => {
        console.error('[Memory] _deleteMemory bridge error:', err);
        showToast("Delete failed");
        // Refresh from bridge to sync state
        if (bridge) {
          callBridge(["refresh", "refreshMemories", "loadInitialData"], [])
            .then((s) => { if (s) window.receiveMemoryState(typeof s === "string" ? JSON.parse(s) : s); })
            .catch(() => {});
        }
      });
  };

  /* ═══════════ DOM READY ═══════════ */
  document.addEventListener("DOMContentLoaded", () => {

    /* ── Nav ── */
    document.querySelectorAll(".nav-item[data-section]").forEach(btn => btn.addEventListener("click", () => switchSection(btn.dataset.section)));
    $("backBtn")?.addEventListener("click", () => { if (bridge && typeof bridge.onSettingsClosed === "function") bridge.onSettingsClosed(); else if (bridge && typeof bridge.closeSettings === "function") bridge.closeSettings(); else { window.history.back(); } });

    /* ── Nav search ── */
    $("settingsSearch")?.addEventListener("input", (e) => { const q = e.target.value.toLowerCase().trim(); document.querySelectorAll(".nav-item").forEach(btn => { btn.style.display = (!q || btn.textContent.toLowerCase().includes(q)) ? "" : "none"; }); });

    /* ── Theme picker (special — button group, not a single input) ── */
    document.querySelectorAll(".theme-option").forEach(btn => btn.addEventListener("click", () => {
      const theme = btn.dataset.theme;
      if (!theme) return;

      /* Update button states */
      document.querySelectorAll(".theme-option").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");

      /* Apply theme to this page immediately */
      document.documentElement.setAttribute("data-theme", theme);

      /* Notify Python to switch the entire IDE theme. setTheme() persists
         AND applies — do not also call setSetting("theme", ...): "theme"
         has no dot, so setSetting's dotted-path parser stored it under the
         orphaned key ui.theme (nothing ever reads that), while setTheme()
         separately persists the real top-level "theme" key. Every click
         was writing two divergent copies to settings.json. */
      if (bridge && typeof bridge.setTheme === "function") {
        bridge.setTheme(theme);
      } else if (bridge && typeof bridge.setSetting === "function") {
        /* Fallback only when setTheme is unavailable: at least persist. */
        bridge.setSetting("theme", theme);
      }

      /* Show theme switch notification with restart prompt */
      const themeName = theme === "light" ? "Light Mode" : "Dark Mode";
      const notification = document.createElement('div');
      notification.className = 'theme-notification';
      notification.innerHTML = `
        <div class="theme-notification-content">
          <div class="theme-notification-icon">✓</div>
          <div class="theme-notification-text">
            <div class="theme-notification-title">Theme Changed to ${themeName}</div>
            <div class="theme-notification-desc">For the best experience, restart Cortex to apply theme changes to all UI elements.</div>
          </div>
          <div class="theme-notification-actions">
            <button class="theme-notification-dismiss" onclick="this.closest('.theme-notification').remove()">Dismiss</button>
            <button class="theme-notification-restart" onclick="window._restartIDEForTheme && window._restartIDEForTheme()">Restart Now</button>
          </div>
        </div>
      `;
      document.body.appendChild(notification);
      
      /* Auto-dismiss after 8 seconds if not interacted */
      setTimeout(() => {
        if (notification.parentNode) notification.remove();
      }, 8000);
    }));
    
    /* Restart IDE for theme change */
    window._restartIDEForTheme = function() {
      if (bridge && typeof bridge.restartIDE === 'function') {
        bridge.restartIDE();
      } else if (bridge && typeof bridge.restart === 'function') {
        bridge.restart();
      } else {
        showToast('Restart IDE manually for theme changes to take full effect');
      }
    };

    /* ── Range sliders — update display label ── */
    [["editorFontSize", "editorFontSizeVal", "px"], ["uiScale", "uiScaleVal", "%"], ["termFontSize", "termFontSizeVal", "px"], ["watcherDebounce", "watcherDebounceVal", "ms"], ["requestTimeout", "requestTimeoutVal", "s"]].forEach(([i, v, u]) => { const inp = $(i), val = $(v); if (inp && val) inp.addEventListener("input", () => { val.textContent = inp.value + u; persistSetting(i, inp.value); }); });

    /* ── All toggles, selects, inputs → live persist ── */
    document.querySelectorAll(".switch input").forEach(t => t.addEventListener("change", () => persistSetting(t.id, t.checked)));
    document.querySelectorAll(".setting-select").forEach(s => s.addEventListener("change", () => persistSetting(s.id, s.value)));
    document.querySelectorAll(".setting-input, .setting-textarea").forEach(i => i.addEventListener("blur", () => persistSetting(i.id, i.value)));

    /* ── Agentic Loop Mode special handler (show/hide warning) ── */
    const agenticLoopToggle = $("agenticLoopMode");
    if (agenticLoopToggle) {
      agenticLoopToggle.addEventListener("change", () => {
        persistSetting("agenticLoopMode", agenticLoopToggle.checked);
        const warning = $("loopModeWarning");
        if (warning) {
          warning.style.display = agenticLoopToggle.checked ? "block" : "none";
        }
        showToast(agenticLoopToggle.checked ? "Agentic Loop Mode: ON — AI will autonomously iterate" : "Agentic Loop Mode: OFF — Normal mode (explicit prompts)");
      });
      /* Show warning on load if already enabled */
      const warning = $("loopModeWarning");
      if (warning && agenticLoopToggle.checked) {
        warning.style.display = "block";
      }
    }

    /* ── Default Model → also sync chat panel model button ── */
    const modelSelect = $("defaultModel");
    if (modelSelect) {
      modelSelect.addEventListener("change", () => {
        const value = modelSelect.value;
        const label = modelSelect.options[modelSelect.selectedIndex].text;
        persistSetting("defaultModel", value);
        /* Notify chat panel to update model button */
        if (bridge && typeof bridge.setDefaultModel === "function") {
          bridge.setDefaultModel(value, label);
        } else if (bridge && typeof bridge.setSetting === "function") {
          bridge.setSetting("ai.model", value);
          bridge.setSetting("ai.model_label", label);
        }
        showToast("Default model: " + label);
      });
    }

    /* ── Memory controls ── */
    $("enabledToggle")?.addEventListener("change", () => { state.enabled = $("enabledToggle").checked; $("statusDot")?.classList.toggle("enabled", state.enabled); callBridge(["setMemoryEnabled", "setEnabled", "toggle_memory"], [state.enabled]).catch(() => { }); });
    document.querySelectorAll(".scope-tab").forEach(tab => tab.addEventListener("click", () => { state.activeScope = tab.dataset.scope; document.querySelectorAll(".scope-tab").forEach(t => t.classList.toggle("active", t === tab)); callBridge(["setActiveScope", "setScope", "switch_scope"], [state.activeScope]).catch(() => { }); renderMemoryList(); }));
    $("searchInput")?.addEventListener("input", (e) => { uiState.query = e.target.value; renderMemoryList(); });
    $("refreshBtn")?.addEventListener("click", () => callBridge(["refresh", "refreshMemories", "loadInitialData"], []).then(s => { if (s) window.receiveMemoryState(s); showToast("Refreshed"); }).catch(() => showToast("Refresh failed")));
    $("clearBtn")?.addEventListener("click", () => { if (confirm("Clear ALL memories? This cannot be undone.")) callBridge(["clearAll", "clear_all_memories"], [state.activeScope]).then(() => { state.scopes.project.memories = []; state.scopes.global.memories = []; renderMemoryList(); showToast("All memories cleared"); }).catch(() => showToast("Clear failed")); });
    $("statsBtn")?.addEventListener("click", () => callBridge(["getMemoryStats", "getStats", "get_memory_stats"], [state.activeScope]).then(s => { if (s) showToast(JSON.stringify(s).slice(0, 200)); }).catch(() => showToast("Stats unavailable")));
    $("consolidateBtn")?.addEventListener("click", () => callBridge(["runConsolidation", "consolidate", "consolidate_memories"], [state.activeScope, true]).then(() => { showToast("Consolidation complete"); $("consolidationModal")?.classList.remove("hidden"); }).catch(() => showToast("Consolidation unavailable")));
    $("closeConsolidationModal")?.addEventListener("click", () => $("consolidationModal")?.classList.add("hidden"));
    $("closeConsolidationModalBtn")?.addEventListener("click", () => $("consolidationModal")?.classList.add("hidden"));
    $("openRulesBtn")?.addEventListener("click", () => {
      // Show rules setup modal with instructions
      const modal = document.createElement('div');
      modal.className = 'modal-overlay';
      modal.innerHTML = `
        <div class="consolidation-modal" style="max-width:600px;">
          <div class="modal-header">
            <h2>Rules Setup</h2>
            <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
          <div class="modal-body" style="max-height:400px;overflow-y:auto;">
            <p style="color:var(--muted);margin-bottom:16px;">Rules define persistent behavior for the AI agent. Create an <code>AGENTS.md</code> file in your rules directory.</p>
            
            <h3 style="margin:16px 0 8px;">File Locations</h3>
            <ul style="color:var(--muted);font-size:13px;line-height:1.8;">
              <li><code>~/.cortex/AGENTS.md</code> — Global rules (all projects)</li>
              <li><code>.cortex/AGENTS.md</code> — Project-specific rules</li>
              <li><code>.cortex/rules/*.md</code> — Per-project rule files</li>
            </ul>
            
            <h3 style="margin:16px 0 8px;">Rule Format</h3>
            <pre style="background:var(--surface-2);padding:12px;border-radius:8px;font-size:12px;overflow-x:auto;"><code>---
name: coding-style
description: Python coding conventions
priority: 10
scope: project
---
Always use type hints in Python functions.
Use f-strings instead of .format().</code></pre>
            
            <h3 style="margin:16px 0 8px;">How Rules Connect</h3>
            <p style="color:var(--muted);font-size:13px;">Rules are automatically injected into the system prompt when the agent starts. Higher priority rules appear first. Enable/disable rules via the toggle.</p>
          </div>
          <div class="modal-footer">
            <button class="setting-btn" onclick="this.closest('.modal-overlay').remove()">Close</button>
            <button class="setting-btn primary-btn" id="openRulesDirBtn">Open Rules Folder</button>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
      
      // Handle open rules folder button
      modal.querySelector('#openRulesDirBtn').addEventListener('click', () => {
        callBridge(["openRulesDir", "openRules", "edit_rules"], [state.activeScope])
          .then(() => modal.remove())
          .catch(() => showToast("Rules editor unavailable"));
      });
    });
    $("syncGlobalBtn")?.addEventListener("click", () => { const root = state.scopes.project.projectRoot || ""; callBridge(["syncGlobalMemoriesToProject", "syncGlobal", "sync_global_memories"], [root, true]).then(() => showToast("Synced")).catch(() => showToast("Sync unavailable")); });

    /* ── Escape ── */
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("consolidationModal")?.classList.add("hidden"); $("modalHost")?.classList.add("hidden"); closeEditProfileModal(); } });

    /* ═══════════════════════════════════════════════════════════════
       PROFILE & USAGE
       ═══════════════════════════════════════════════════════════════ */

    /* ── Token formatting ── */
    function formatTokens(n) {
      if (!n || n === 0) return '0';
      if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
      if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
      if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
      return n.toString();
    }

    function formatDuration(seconds) {
      if (!seconds || seconds === 0) return '0m 0s';
      if (seconds >= 3600) return Math.floor(seconds / 3600) + 'h ' + Math.floor((seconds % 3600) / 60) + 'm';
      if (seconds >= 60) return Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
      return seconds + 's';
    }

    /* ── Cached usage data for chart range switching ── */
    const MODEL_NAMES={'mimo-v2.5':{n:'MiMo V2.5',c:'#f97316'},'mimo-v2.5-pro':{n:'MiMo V2.5 Pro',c:'#f97316'},'deepseek-v4':{n:'DeepSeek V4',c:'#a78bfa'},'deepseek-v4-pro':{n:'DeepSeek V4 Pro',c:'#a78bfa'},'gpt-5.5':{n:'GPT-5.5',c:'#10b981'},'gpt-5.4':{n:'GPT-5.4',c:'#10b981'},'gpt-4o':{n:'GPT-4o',c:'#10b981'},'claude-opus':{n:'Claude Opus',c:'#d77b4a'},'claude-sonnet':{n:'Claude Sonnet',c:'#d77b4a'},'claude-fable':{n:'Claude Fable',c:'#d77b4a'},'qwen3.7-plus':{n:'Qwen 3.7 Plus',c:'#f59e0b'},'qwen3.6-plus':{n:'Qwen 3.6 Plus',c:'#f59e0b'},'gemini-3.5-flash':{n:'Gemini 3.5 Flash',c:'#4285f4'},'gemini-2.5-pro':{n:'Gemini 2.5 Pro',c:'#4285f4'},'glm-5.2':{n:'GLM 5.2',c:'#00bcd4'},'mistral-large-latest':{n:'Mistral Large',c:'#f43f5e'},'grok-4.3':{n:'Grok 4.3',c:'#1da1f2'}};
    const _IM=/^(test|mock|fake|placeholder|unknown|default)/i;
    function isValidModel(id){return id&&typeof id==='string'&&id.length>=2&&id.length<=80&&!_IM.test(id);} function getModelName(id){if(!id)return'Unknown';var m=MODEL_NAMES[id];if(m)return m.n;return id.split(/[-_]/).map(function(w){return w.charAt(0).toUpperCase()+w.slice(1);}).join(' ');} function getModelColor(id){var m=MODEL_NAMES[id];return m?m.c:'#6b7280';}
    let _cachedUsageData = null;

    /* ── Activity heatmap (GitHub-style, no pagination) ── */

    /* ── Load profile data from bridge ── */
    function loadProfile(refresh) {
      /* Instant render from Python's cache (slots are memory-only now);
         a background server fetch then re-invokes us via
         server_data_ready with refresh=false (loop guard). */
      if (refresh !== false) callBridge(["refreshServerData"], ["account"]).catch(() => {});
      /* First check auth status */
      callBridge(["getAuthStatus"], []).then(authData => {
        let auth = {};
        try { auth = typeof authData === 'string' ? JSON.parse(authData) : authData || {}; } catch(e) {}
        
        const isLoggedIn = auth.logged_in || false;
        
        /* Show/hide login card vs profile hero */
        const loginCard = $("loginCard");
        const profileHero = $("profileHero");
        
        if (loginCard) loginCard.style.display = isLoggedIn ? 'none' : 'block';
        if (profileHero) profileHero.style.display = isLoggedIn ? 'flex' : 'none';
        
        /* If logged in, populate profile from auth data */
        if (isLoggedIn && auth.user) {
          const u = auth.user;
          const name = u.display_name || u.email?.split('@')[0] || 'User';
          const email = u.email || '';
          const initials = name.charAt(0).toUpperCase() + (name.split(' ')[1]?.charAt(0) || '').toUpperCase();
          
          /* Plan display — use plan_display from API or fallback */
          const planMap = { 'starter': 'Starter', 'pro': 'Pro' };
          const planText = u.plan_display || planMap[u.plan] || 'Free';
          
          if ($("profileAvatar")) $("profileAvatar").textContent = initials;
          if ($("profileName")) $("profileName").textContent = name;
          if ($("profileEmail")) $("profileEmail").textContent = email;
          if ($("profilePlan")) $("profilePlan").textContent = planText;
          if ($("serverStatus")) $("serverStatus").textContent = 'Connected';
        }
      }).catch(() => {});
      
      /* Also fetch full profile from server for more details */
      callBridge(["getProfile", "get_profile"], []).then(data => {
        if (!data) return;
        try {
          const p = typeof data === 'string' ? JSON.parse(data) : data;
          const profile = p.profile || p;
          const auth = p.auth || {};
          
          /* Use server data if available */
          const displayName = auth.display_name || profile.display_name || '';
          const email = auth.email || profile.email || '';
          
          /* Plan display — use plan_display from API or fallback */
          const planMap = { 'pro': 'Pro', 'pro_yearly': 'Pro (Yearly)' };
          const planText = auth.plan_display || profile.plan_display || planMap[auth.plan || profile.plan] || 'Free';
          
          if (displayName && $("profileName")) $("profileName").textContent = displayName;
          if (email && $("profileEmail")) $("profileEmail").textContent = email;
          if ($("profilePlan")) $("profilePlan").textContent = planText;
          
          /* Update avatar initials */
          if (displayName && $("profileAvatar")) {
            const parts = displayName.split(' ');
            const initials = parts[0].charAt(0).toUpperCase() + (parts[1]?.charAt(0) || '').toUpperCase();
            $("profileAvatar").textContent = initials;
          }
        } catch (e) { console.error('[PROFILE] parse error:', e); }
      }).catch(() => { /* no bridge — use defaults */ });
    }

    /* ── Auth functions ── */
    function startBrowserLogin() {
      const status = $("loginStatus");
      const error = $("loginError");
      if (status) status.textContent = 'Checking server connection...';
      if (error) error.style.display = 'none';
      
      callBridge(["startLogin"], []).then(ok => {
        if (ok) {
          if (status) status.textContent = 'Browser opened. Complete login there, then return here.';
        } else {
          if (status) status.textContent = '';
          if (error) {
            error.textContent = 'Cannot connect to server. Make sure Django is running.';
            error.style.display = 'block';
          }
        }
      }).catch(() => {
        if (status) status.textContent = '';
        if (error) {
          error.textContent = 'Login failed. Check server connection.';
          error.style.display = 'block';
        }
      });
    }
    
    function logoutUser() {
      callBridge(["logout"], []).then(() => {
        loadProfile();
        loadUsageStats();
      }).catch(() => {});
    }
    
    /* Expose auth functions to global scope for inline onclick handlers */
    window.startBrowserLogin = startBrowserLogin;
    window.logoutUser = logoutUser;
    window.openUpgradePage = openUpgradePage;
    window.loadProfile = loadProfile;
    window.loadUsageStats = loadUsageStats;

    /* ── Apply usage data to UI (shared by bridge + demo) ── */
    function applyUsageData(u) {
      if (!u) return;
      const life = u.lifetime || {};
      const peak = u.peak || {};
      const streaks = u.streaks || {};
      const period = u.current_period || {};
      const models = u.model_usage || {};
      const insights = u.insights || {};

      /* Update stat cards */
      if ($("lifetimeTokens")) $("lifetimeTokens").textContent = formatTokens(life.total_tokens);
      if ($("peakTokens")) $("peakTokens").textContent = formatTokens(peak.peak_tokens_single_session);
      if ($("longestTask")) $("longestTask").textContent = formatDuration(life.longest_task_seconds);
      if ($("currentStreak")) $("currentStreak").textContent = (streaks.current_streak_days || 0) + ' days';
      if ($("longestStreak")) $("longestStreak").textContent = (streaks.longest_streak_days || 0) + ' days';

      /* Update usage meters — BYOK: no limit, show raw count only */
      const tokensUsed = period.tokens_used || 0;
      if ($("monthlyPercent")) $("monthlyPercent").textContent = formatTokens(tokensUsed);
      if ($("monthlyFill")) { $("monthlyFill").style.width = '0%'; $("monthlyFill").className = 'meter-fill'; }
      if ($("monthlyDetail")) $("monthlyDetail").textContent = formatTokens(tokensUsed) + ' tokens this month (BYOK — no limit)';
      if ($("monthlyReset") && period.end_date) $("monthlyReset").textContent = '';

      const dailyPct = 0;
      if ($("dailyPercent")) $("dailyPercent").textContent = formatTokens(period.tokens_used || 0);
      if ($("dailyFill")) { $("dailyFill").style.width = '0%'; $("dailyFill").className = 'meter-fill'; }
      if ($("dailyDetail")) $("dailyDetail").textContent = formatTokens(period.tokens_used || 0) + ' tokens (BYOK)';

      /* Update insights — skills only (no fake bars) */
      const skillsExplored = insights.skills_explored || [];
      if ($("skillsExplored")) $("skillsExplored").textContent = skillsExplored.length > 0 ? skillsExplored.slice(0, 5).join(', ') + (skillsExplored.length > 5 ? '...' : '') : 'None yet';
      if ($("totalSkills")) $("totalSkills").textContent = insights.total_skills_used || 0;

      /* Service usage — simple number counters */
      const ocrUsed = period.ocr_pages_used || 0;
      const embedUsed = period.embedding_tokens_used || 0;
      const searchUsed = period.web_searches_used || 0;
      if ($("ocrPages")) $("ocrPages").textContent = ocrUsed;
      if ($("embeddingsCount")) $("embeddingsCount").textContent = embedUsed;
      if ($("webSearches")) $("webSearches").textContent = searchUsed;

      /* Update model usage list — token pills */
      const modelList = $("modelUsageList");
      if (modelList && Object.keys(models).length > 0) {
        const sorted = Object.entries(models).filter(function(e){return isValidModel(e[0]);}).sort((a, b) => (b[1].total_tokens || 0) - (a[1].total_tokens || 0));
        modelList.innerHTML = sorted.map(([name, info]) => {
          return '<div class="model-token-pill" style="margin-bottom:6px;">' +
            '<span class="dot" style="background:' + getModelColor(name) + '"></span>' +
            '<span>' + esc(getModelName(name)) + '</span>' +
            '<strong>' + formatTokens(info.total_tokens || 0) + '</strong>' +
            '</div>';
        }).join('');
      }

      /* Update local usage list (AI Model Usage) */
      const localList = $("localUsageList");
      if (localList && Object.keys(models).length > 0) {
        const sorted = Object.entries(models).filter(function(e){return isValidModel(e[0]);}).sort((a, b) => (b[1].total_tokens || 0) - (a[1].total_tokens || 0));
        const totalAll = sorted.reduce(function(s, e) { return s + (e[1].total_tokens || 0); }, 0);
        localList.innerHTML =
          '<div style="font-size:12px;color:#6b7280;margin-bottom:8px;">Total: <strong style="color:#e5e7eb;">' + formatTokens(totalAll) + '</strong> across ' + sorted.length + ' models</div>' +
          sorted.map(([name, info], i) => {
          return '<div class="model-token-pill" style="margin-bottom:6px;">' +
            '<span class="dot" style="background:' + getModelColor(name) + '"></span>' +
            '<span>' + esc(getModelName(name)) + '</span>' +
            '<strong>' + formatTokens(info.total_tokens || 0) + '</strong>' +
            '</div>';
        }).join('');
      } else if (localList) {
        localList.innerHTML = '<div class="empty-state-small"><p>No model usage data yet</p></div>';
      }

      /* Update model breakdown — token pills (BYOK: no limits, just counts) */
      const breakdown = $("modelBreakdown");
      if (breakdown && Object.keys(models).length > 0) {
        const sorted = Object.entries(models).filter(function(e){return isValidModel(e[0]);}).sort((a, b) => (b[1].total_tokens || 0) - (a[1].total_tokens || 0));
        const totalAll = sorted.reduce(function(s, e) { return s + (e[1].total_tokens || 0); }, 0);
        breakdown.innerHTML =
          '<div style="font-size:12px;color:#6b7280;margin-bottom:10px;width:100%;">Total across all models: <strong style="color:#e5e7eb;">' + formatTokens(totalAll) + '</strong></div>' +
          sorted.map(([name, info]) => {
          return '<div class="model-token-pill">' +
            '<span class="dot" style="background:' + getModelColor(name) + '"></span>' +
            '<span>' + esc(getModelName(name)) + '</span>' +
            '<strong>' + formatTokens(info.total_tokens || 0) + '</strong>' +
            '</div>';
        }).join('');
      }

      /* Render GitHub-style activity heatmap */
      renderActivityChart(u.daily_usage || {});
    }

    /* ── Load usage stats from bridge ── */
    function loadUsageStats(refresh) {
      /* Instant render from Python's cache; background fetch re-invokes
         via server_data_ready with refresh=false (loop guard). */
      if (refresh !== false) callBridge(["refreshServerData"], ["usage"]).catch(() => {});
      callBridge(["getUsageStats", "get_usage_stats"], []).then(data => {
        if (!data) return;
        try {
          const u = typeof data === 'string' ? JSON.parse(data) : data;
          _cachedUsageData = u;
          applyUsageData(u);
          
          /* Update server data if available */
          const server = u.server || {};
          const sub = server.subscription || {};
          const credits = server.credits || {};
          const usage = server.usage || {};
          
          /* Update plan card with server data */
          const hasPlan = sub.plan && sub.plan !== 'none' && sub.plan !== 'free';
          const planNames = { 'pro': 'Pro', 'pro_yearly': 'Pro (Yearly)', 'starter': 'Starter', 'free': 'Free' };
          
          if ($("planName")) {
            $("planName").textContent = hasPlan ? (planNames[sub.plan] || sub.plan) : 'Free';
          }
          // Hide price display
          if ($("planPrice")) {
            $("planPrice").textContent = '';
            $("planPrice").style.display = 'none';
          }
          
          // Show/hide upgrade button and features
          const upgradeBtn = $("upgradePlanBtn");
          const planFeatures = $("planFeatures");
          const serviceCard = $("serviceUsageCard");
          
          if (hasPlan) {
            if (upgradeBtn) upgradeBtn.style.display = 'none';
            if (planFeatures) planFeatures.style.display = 'block';
            if (serviceCard) serviceCard.style.display = 'block';
            // Show real counters, hide subscribe prompt
            if ($("serviceUsageCounters")) $("serviceUsageCounters").style.display = 'flex';
            if ($("serviceUsageSubscribe")) $("serviceUsageSubscribe").style.display = 'none';
          } else {
            if (upgradeBtn) upgradeBtn.style.display = 'inline-block';
            if (planFeatures) planFeatures.style.display = 'none';
            if (serviceCard) serviceCard.style.display = 'none';
            // Hide counters, show subscribe prompt
            if ($("serviceUsageCounters")) $("serviceUsageCounters").style.display = 'none';
            if ($("serviceUsageSubscribe")) $("serviceUsageSubscribe").style.display = 'block';
          }
          
          /* Show credits if available */
          if (credits.monthly_allocation > 0) {
            const creditsInfo = $("creditsInfo");
            if (creditsInfo) creditsInfo.style.display = 'block';
            if ($("creditBalance")) $("creditBalance").textContent = '$' + (credits.balance || 0).toFixed(2);
            if ($("creditsUsed")) $("creditsUsed").textContent = '$' + (credits.used_this_month || 0).toFixed(2);
          }
          
          /* Show server usage card */
          if (usage.tokens_this_month > 0) {
            const serverCard = $("serverUsageCard");
            if (serverCard) serverCard.style.display = 'block';
            const serverInfo = $("serverUsageInfo");
            if (serverInfo) {
              serverInfo.innerHTML = 
                'Tokens this month: <strong>' + formatTokens(usage.tokens_this_month) + '</strong><br>' +
                'Requests this month: <strong>' + (usage.requests_this_month || 0) + '</strong>';
            }
          }
        } catch (e) { console.error('[USAGE] parse error:', e); }
      }).catch(() => { /* no bridge — use defaults */ });
    }
    
    function openUpgradePage() {
      var url = 'https://cortex-ide.app/pricing/';
      if (bridge && typeof bridge.openExternal === 'function') {
        bridge.openExternal(url);
      } else {
        window.open(url, '_blank');
      }
    }

    /* ── Render GitHub-style contribution heatmap ── */
    function renderActivityChart(dailyUsage) {
      var grid = $("heatmapGrid");
      if (!grid) return;
      var tokenMap = {};
      Object.keys(dailyUsage || {}).forEach(function(d) {
        var v = dailyUsage[d];
        tokenMap[d] = typeof v === 'number' ? v : (v && v.tokens || 0);
      });
      var allVals = Object.values(tokenMap), maxVal = 0;
      allVals.forEach(function(v) { if (v > maxVal) maxVal = v; });
      var totalTokens = allVals.reduce(function(a,b){return a+b;}, 0);
      if ($("heatmapTotal")) $("heatmapTotal").textContent = formatTokens(totalTokens) + ' tokens this month';

      if (allVals.length === 0) {
        grid.innerHTML = '<div style="color:#6b7280;font-size:13px;padding:16px 0;">No activity yet — daily token usage appears here.</div>';
        return;
      }

      function getLevel(tokens) {
        if (tokens <= 0) return 0;
        var r = tokens / maxVal;
        if (r > 0.75) return 4;
        if (r > 0.5) return 3;
        if (r > 0.25) return 2;
        return 1;
      }

      function isLeapYear(y) { return (y % 4 === 0 && y % 100 !== 0) || (y % 400 === 0); }
      function daysInMonth(y, m) { return [31, isLeapYear(y) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m]; }

      var now = new Date();
      var year = now.getFullYear(), month = now.getMonth();
      var lastDay = daysInMonth(year, month);
      var firstDow = new Date(year, month, 1).getDay(); // 0=Sun
      var todayStr = now.toISOString().slice(0, 10);

      // Grid: columns = dates 1..31, rows = Sun..Sat
      var html = '<div style="display:grid;grid-template-columns:36px repeat(' + lastDay + ',1fr);gap:2px;">';
      // Header row: empty corner + date numbers
      html += '<div></div>';
      for (var d = 1; d <= lastDay; d++) {
        html += '<div style="font-size:7px;color:#484f58;text-align:center;line-height:14px;">' + d + '</div>';
      }
      // 7 rows: Sun..Sat
      var weekdays = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
      for (var row = 0; row < 7; row++) {
        html += '<div style="font-size:10px;color:#8b949e;display:flex;align-items:center;">' + weekdays[row] + '</div>';
        for (var d = 1; d <= lastDay; d++) {
          var date = new Date(year, month, d);
          var dow = date.getDay(); // 0=Sun
          if (dow !== row) {
            html += '<div class="cell empty"></div>';
          } else {
            var ds = year + '-' + String(month+1).padStart(2,'0') + '-' + String(d).padStart(2,'0');
            var tokens = tokenMap[ds] || 0;
            var cls = tokens > 0 ? ('l' + getLevel(tokens)) : 'l0';
            if (ds === todayStr) cls += ' today';
            html += '<div class="cell ' + cls + '" title="' + ds + ': ' + formatTokens(tokens) + '"></div>';
          }
        }
      }
      html += '</div>';
      grid.innerHTML = html;
    }

    /* ── Edit Profile modal (using CSS classes) ── */
    function closeEditProfileModal() {
      const existing = document.querySelector('.edit-profile-modal');
      if (existing) existing.remove();
    }

    const editBtn = $("editProfileBtn");
    if (editBtn) {
      editBtn.addEventListener('click', () => {
        closeEditProfileModal(); /* close any existing */
        const currentName = $("profileName")?.textContent || 'User';
        const currentUsername = $("profileUsername")?.textContent?.replace('@', '') || 'user';
        const currentInitials = $("profileAvatar")?.textContent || 'HA';
        /* Extract the first #hex color from the background style */
        const bgStyle = $("profileAvatar")?.style.background || '';
        const colorMatch = bgStyle.match(/#[0-9a-fA-F]{6}/);
        const currentColor = colorMatch ? colorMatch[0] : '#f97316';
        const colors = ['#f97316', '#3b82f6', '#8b5cf6', '#10b981', '#ef4444', '#f59e0b', '#ec4899', '#06b6d4'];

        const overlay = document.createElement('div');
        overlay.className = 'edit-profile-modal';
        overlay.innerHTML =
          '<div class="ep-card">' +
          '<h2>Edit Profile</h2>' +
          '<div class="ep-avatar-wrap">' +
          '<div class="ep-avatar" id="editAvatarPreview" style="background:linear-gradient(135deg,' + currentColor + ',' + currentColor + ')">' + currentInitials + '</div>' +
          '<div class="ep-colors">' +
          colors.map(c => '<button class="ep-color-dot' + (c === currentColor ? ' selected' : '') + '" data-color="' + c + '" style="background:' + c + '"></button>').join('') +
          '</div></div>' +
          '<div class="ep-field"><label>Display Name</label><input type="text" id="editDisplayName" value="' + esc(currentName) + '"></div>' +
          '<div class="ep-field"><label>Username</label><input type="text" id="editUsername" value="' + esc(currentUsername) + '"></div>' +
          '<div class="ep-actions">' +
          '<button class="setting-btn" id="epCancelBtn">Cancel</button>' +
          '<button class="setting-btn primary-btn" id="epSaveBtn">Save</button>' +
          '</div></div>';

        document.body.appendChild(overlay);

        /* Color dot selection */
        overlay.querySelectorAll('.ep-color-dot').forEach(dot => {
          dot.addEventListener('click', () => {
            overlay.querySelectorAll('.ep-color-dot').forEach(d => d.classList.remove('selected'));
            dot.classList.add('selected');
            const c = dot.dataset.color;
            const avatar = overlay.querySelector('#editAvatarPreview');
            if (avatar) avatar.style.background = 'linear-gradient(135deg,' + c + ',' + c + ')';
          });
        });

        /* Cancel */
        overlay.querySelector('#epCancelBtn')?.addEventListener('click', closeEditProfileModal);

        /* Backdrop click */
        overlay.addEventListener('click', (e) => { if (e.target === overlay) closeEditProfileModal(); });

        /* Save */
        overlay.querySelector('#epSaveBtn')?.addEventListener('click', () => {
          const name = $("editDisplayName")?.value || 'User';
          const username = $("editUsername")?.value || 'user';
          const selectedDot = overlay.querySelector('.ep-color-dot.selected') || overlay.querySelector('.ep-color-dot');
          const color = selectedDot?.dataset?.color || '#f97316';
          const initials = name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);

          /* Update UI */
          if ($("profileAvatar")) { $("profileAvatar").textContent = initials; $("profileAvatar").style.background = 'linear-gradient(135deg, ' + color + ', ' + color + ')'; }
          if ($("profileName")) $("profileName").textContent = name;
          if ($("profileUsername")) $("profileUsername").textContent = '@' + username;

          /* Save to bridge */
          callBridge(["setProfile", "set_profile"], [JSON.stringify({ display_name: name, username: username, avatar_color: color, avatar_initials: initials })]).catch(() => {});
          closeEditProfileModal();
          showToast('Profile saved');
        });
      });
    }

    /* ── Browse plugins link ── */
    $("browsePluginsLink")?.addEventListener('click', (e) => {
      e.preventDefault();
      switchSection('extensions');
    });

    /* ── Upgrade plan button ── */
    $("upgradePlanBtn")?.addEventListener('click', () => {
      showToast('Upgrade plan — coming soon!');
    });

    /* ── Load profile/usage when bridge connects ── */
    whenBridgeReady(() => { loadProfile(); loadUsageStats(); });

    /* ── Refresh profile/usage when switching to those sections ── */
    document.querySelectorAll('.nav-item[data-section="profile"], .nav-item[data-section="usage"]').forEach(btn => {
      btn.addEventListener('click', () => { loadProfile(); loadUsageStats(); });
    });

    /* ── Init bridge ── */
    initBridge();
    /* Profile/usage load on init is handled by whenBridgeReady above. */

    /* ═══════════════════════════════════════════════════════════════
       API KEY MANAGEMENT
       ═══════════════════════════════════════════════════════════════ */
    
    const PROVIDER_CONFIG = {
      mimo:      { input: 'mimoKey',      mask: 'mimoKeyMask',      eye: 'mimoEye',      test: 'mimoTest',      remove: 'mimoRemove',      settingsKey: 'ai.mimo_key',      kmName: 'mimo' },
      deepseek:  { input: 'deepseekKey',  mask: 'deepseekKeyMask',  eye: 'deepseekEye',  test: 'deepseekTest',  remove: 'deepseekRemove',  settingsKey: 'ai.deepseek_key',  kmName: 'deepseek' },
      anthropic: { input: 'anthropicKey', mask: 'anthropicKeyMask', eye: 'anthropicEye', test: 'anthropicTest', remove: 'anthropicRemove', settingsKey: 'ai.anthropic_key', kmName: 'anthropic' },
      openai:    { input: 'openaiKey',    mask: 'openaiKeyMask',    eye: 'openaiEye',    test: 'openaiTest',    remove: 'openaiRemove',    settingsKey: 'ai.openai_key',    kmName: 'openai' },
      openrouter:{ input: 'openrouterKey',mask: 'openrouterKeyMask',eye: 'openrouterEye',test: 'openrouterTest',remove: 'openrouterRemove',settingsKey: 'ai.openrouter_key',kmName: 'openrouter' },
      alibaba:   { input: 'alibabaKey',  mask: 'alibabaKeyMask',   eye: 'alibabaEye',   test: 'alibabaTest',   remove: 'alibabaRemove',   settingsKey: 'ai.alibaba_key',   kmName: 'alibaba' },
    };

    /* Track which providers have keys stored */
    const _providerHasKey = {};
    /* Track which providers were explicitly removed (don't reload from .env) */
    const _providerRemoved = {};

    /* Mask API key for display: show first 6 + last 4 chars, asterisks in between */
    function _maskApiKey(key) {
      if (!key || typeof key !== 'string') return '••••••••••••••••';
      if (key.length <= 12) return key.charAt(0) + '•'.repeat(key.length - 2) + key.charAt(key.length - 1);
      return key.substring(0, 6) + '•'.repeat(Math.min(key.length - 10, 16)) + key.substring(key.length - 4);
    }

    /* Show masked key when a key is stored */
    function _showMaskedState(provider, fullKey) {
      const cfg = PROVIDER_CONFIG[provider];
      if (!cfg) return;
      const input = $(cfg.input);
      const mask = $(cfg.mask);
      const row = input?.closest('.provider-row');
      if (input && mask) {
        input.style.display = 'none';
        mask.style.display = 'inline-block';
        /* If fullKey provided, show partial mask; otherwise generic dots */
        mask.textContent = fullKey ? _maskApiKey(fullKey) : '••••••••••••••••••••••••••••';
        if (row) row.setAttribute('data-has-key', 'true');
        _providerHasKey[provider] = true;
      }
    }

    /* Show input field when editing */
    function _showEditState(provider) {
      const cfg = PROVIDER_CONFIG[provider];
      if (!cfg) return;
      const input = $(cfg.input);
      const mask = $(cfg.mask);
      const row = input?.closest('.provider-row');
      if (input && mask) {
        input.style.display = '';
        input.type = 'password';
        mask.style.display = 'none';
        if (row) row.setAttribute('data-has-key', 'false');
      }
    }

    /* Toggle eye icon */
    function _toggleEye(provider) {
      const cfg = PROVIDER_CONFIG[provider];
      if (!cfg) return;
      const eyeBtn = $(cfg.eye);
      const input = $(cfg.input);
      const mask = $(cfg.mask);
      if (!eyeBtn || !input) return;

      const isActive = eyeBtn.classList.contains('active');
      if (isActive) {
        /* Hide: show masked state */
        eyeBtn.classList.remove('active');
        if (_providerHasKey[provider]) {
          _showMaskedState(provider);
        } else {
          input.type = 'password';
        }
      } else {
        /* Show: reveal partially masked key for security */
        eyeBtn.classList.add('active');
        if (_providerHasKey[provider]) {
          /* Load the actual key and show partially masked version */
          if (bridge && typeof bridge.getApiKey === 'function') {
            bridge.getApiKey(cfg.kmName, (key) => {
              if (key) {
                /* Show masked key in the mask element (not full key) */
                mask.textContent = _maskApiKey(key);
                mask.style.display = 'inline-block';
                input.style.display = 'none';
              }
            });
          } else {
            /* No bridge — just show generic mask */
            mask.textContent = '••••••••••••••••••••••••••••';
            mask.style.display = 'inline-block';
            input.style.display = 'none';
          }
        } else {
          input.type = 'text';
        }
      }
    }

    /* Test connection */
    function _testConnection(provider) {
      const cfg = PROVIDER_CONFIG[provider];
      if (!cfg) return;
      const testBtn = $(cfg.test);
      if (!testBtn) return;

      testBtn.classList.add('testing');
      testBtn.classList.remove('success', 'error');
      testBtn.title = 'Testing...';

      if (bridge && typeof bridge.testApiKey === 'function') {
        bridge.testApiKey(cfg.kmName, (raw) => {
          // QWebChannel returns result=str as a JSON string — parse it
          let result;
          try {
            result = typeof raw === 'string' ? JSON.parse(raw) : raw;
          } catch(e) { result = raw; }
          testBtn.classList.remove('testing');
          if (result && result.success) {
            testBtn.classList.add('success');
            testBtn.title = 'Connected ✓';
            showToast(`${provider} connected successfully`);
          } else {
            testBtn.classList.add('error');
            testBtn.title = 'Failed ✗';
            showToast(`${provider} connection failed: ${result?.error || 'Unknown error'}`);
          }
          setTimeout(() => {
            testBtn.classList.remove('success', 'error');
            testBtn.title = 'Test connection';
          }, 3000);
        });
      } else {
        /* No bridge — simulate */
        testBtn.classList.remove('testing');
        testBtn.classList.add('success');
        testBtn.title = 'Connected ✓';
        setTimeout(() => {
          testBtn.classList.remove('success');
          testBtn.title = 'Test connection';
        }, 2000);
      }
    }

    /* Remove key */
    function _removeKey(provider) {
      const cfg = PROVIDER_CONFIG[provider];
      if (!cfg) return;
      
      if (!confirm(`Remove ${provider} API key? You'll need to re-enter it to use this provider.`)) return;

      /* Mark as removed FIRST (prevents re-appearance from .env on page reload) */
      _providerRemoved[provider] = true;
      _providerHasKey[provider] = false;
      
      /* Always update UI immediately */
      _showEditState(provider);
      const input = $(cfg.input);
      if (input) input.value = '';
      persistSetting(cfg.settingsKey, '');
      showToast(`${provider} key removed`);
      
      /* Try to delete from backend (non-blocking) */
      if (bridge && typeof bridge.removeApiKey === 'function') {
        bridge.removeApiKey(cfg.kmName, (ok) => {
          console.log(`[KEY] removeApiKey(${provider}) backend result:`, ok);
        });
      }
    }

    /* Save key on blur */
    function _saveKey(provider) {
      const cfg = PROVIDER_CONFIG[provider];
      if (!cfg) return;
      const input = $(cfg.input);
      if (!input) return;
      const value = input.value.trim();
      
      /* Don't save empty or placeholder values */
      if (!value || value === '***' || value === '••••••••••••••••') return;

      /* Clear removed flag when saving a new key */
      _providerRemoved[provider] = false;

      /* Save via bridge */
      if (bridge && typeof bridge.setApiKey === 'function') {
        bridge.setApiKey(cfg.kmName, value, (ok) => {
          if (ok) {
            _showMaskedState(provider, value);
            showToast(`${provider} key saved`);
          } else {
            showToast(`Failed to save ${provider} key`);
          }
        });
      } else {
        /* Fallback: save via setSetting */
        persistSetting(cfg.settingsKey, value);
        _showMaskedState(provider, value);
        showToast(`${provider} key saved`);
      }
    }

    /* Bind events for all providers */
    Object.entries(PROVIDER_CONFIG).forEach(([provider, cfg]) => {
      const input = $(cfg.input);
      const eyeBtn = $(cfg.eye);
      const testBtn = $(cfg.test);
      const removeBtn = $(cfg.remove);

      /* Eye toggle */
      if (eyeBtn) {
        eyeBtn.addEventListener('click', () => _toggleEye(provider));
      }

      /* Test connection */
      if (testBtn) {
        testBtn.addEventListener('click', () => _testConnection(provider));
      }

      /* Remove key */
      if (removeBtn) {
        removeBtn.addEventListener('click', () => _removeKey(provider));
      }

      /* Save on blur */
      if (input) {
        input.addEventListener('blur', () => _saveKey(provider));
        /* Also save on Enter */
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            input.blur();
          }
        });
      }

      /* Check if key exists and show masked state */
      /* Will be populated when bridge connects */
      _providerHasKey[provider] = false;
    });

    /* Load stored key status when bridge connects */
    function _loadKeyStatus() {
      Object.entries(PROVIDER_CONFIG).forEach(([provider, cfg]) => {
        if (bridge && typeof bridge.getApiKey === 'function') {
          bridge.getApiKey(cfg.kmName, (key) => {
            /* Only show masked state if key is valid (not empty, not placeholder) */
            if (key && key.length > 8 && key !== '***' && key !== '***') {
              _showMaskedState(provider, key);
            } else {
              /* No valid key — show empty input */
              _showEditState(provider);
              const input = $(cfg.input);
              if (input) input.value = '';
              _providerHasKey[provider] = false;
            }
          });
        } else {
          /* No bridge — check settings for placeholder */
          const settingsVal = state?.settings?.[cfg.settingsKey];
          if (settingsVal && settingsVal !== '***' && settingsVal !== '') {
            _showMaskedState(provider);
          } else {
            _showEditState(provider);
          }
        }
      });
    }

    /* Load key status once the bridge is actually connected (see
       whenBridgeReady — a fixed-delay one-shot missed in compiled builds). */
    whenBridgeReady(_loadKeyStatus);

    /* ═══════════════════════════════════════════════════════════════
       PROVIDER ACTIVATION TOGGLES
       Which providers show their models in the chat dropdown.
       Default: MiMo + DeepSeek only. Persisted in ai.enabled_providers.
       ═══════════════════════════════════════════════════════════════ */
    const DEFAULT_ENABLED_PROVIDERS = ['mimo', 'deepseek'];

    function _applyEnabledProviders(list) {
      document.querySelectorAll('.provider-enable').forEach((cb) => {
        cb.checked = list.includes(cb.dataset.provider);
      });
    }

    function _loadEnabledProviders() {
      if (bridge && typeof bridge.getEnabledProviders === 'function') {
        bridge.getEnabledProviders((raw) => {
          let list;
          try { list = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (e) { list = null; }
          _applyEnabledProviders(Array.isArray(list) ? list : DEFAULT_ENABLED_PROVIDERS);
        });
      } else {
        _applyEnabledProviders(DEFAULT_ENABLED_PROVIDERS);
      }
    }

    document.querySelectorAll('.provider-enable').forEach((cb) => {
      cb.addEventListener('change', () => {
        const provider = cb.dataset.provider;
        if (bridge && typeof bridge.setProviderEnabled === 'function') {
          bridge.setProviderEnabled(provider, cb.checked, (raw) => {
            let list;
            try { list = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (e) { list = null; }
            if (Array.isArray(list)) _applyEnabledProviders(list);
            showToast(cb.checked
              ? `${provider} models shown in chat dropdown`
              : `${provider} models hidden from chat dropdown`);
          });
        } else {
          showToast('Not connected — provider setting not saved');
        }
      });
    });

    whenBridgeReady(_loadEnabledProviders);

    /* ═══════════════════════════════════════════════════════════════
       MCP SERVERS
       ═══════════════════════════════════════════════════════════════ */

    function _esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    const _MCP_STATUS_COLORS = {
      connected: '#3fb950', connecting: '#d29922',
      error: '#f85149', disabled: '#6b7280', stopped: '#6b7280',
      subscription: '#d29922',
    };

    let _mcpSubscribed = false;

    function _renderMcpLockCard() {
      const list = $('mcpServerList');
      const empty = $('mcpEmptyState');
      if (!list) return;
      list.querySelectorAll('.mcp-server-row, .mcp-lock-card').forEach(el => el.remove());
      if (empty) empty.style.display = 'none';
      const lock = document.createElement('div');
      lock.className = 'mcp-lock-card';
      lock.style.cssText = 'text-align:center;padding:28px 16px;';
      lock.innerHTML =
        '<div style="font-size:28px;margin-bottom:8px;">🔒</div>' +
        '<h3 style="margin:0 0 6px;">MCP Servers — Subscription Required</h3>' +
        '<p style="color:#8b949e;max-width:460px;margin:0 auto 12px;">Connect databases, web search, GitHub and hundreds of external tools to the AI agent. Available on the Pro plan ($10/month or $80/year) with a signed-in account.</p>' +
        '<a href="https://cortex-ide.app/pricing/" onclick="openUpgradePage(); return false;" style="color:#4da3ff;text-decoration:none;font-weight:600;cursor:pointer;">View plans →</a>';
      list.appendChild(lock);
    }

    function _renderMcpServers(servers) {
      const list = $('mcpServerList');
      const empty = $('mcpEmptyState');
      if (!list) return;
      list.querySelectorAll('.mcp-server-row').forEach(el => el.remove());
      if (!servers || !servers.length) {
        if (empty) empty.style.display = '';
        return;
      }
      if (empty) empty.style.display = 'none';
      servers.forEach(srv => {
        const color = _MCP_STATUS_COLORS[srv.status] || '#6b7280';
        const row = document.createElement('div');
        row.className = 'mcp-server-row';
        row.style.cssText = 'display:flex;align-items:center;gap:12px;padding:12px 4px;' +
                            'border-bottom:1px solid rgba(128,128,128,0.15);';
        const toolsLabel = srv.status === 'connected'
          ? `${srv.tools.length} tool${srv.tools.length === 1 ? '' : 's'}` +
            (srv.tools.length ? ` — ${srv.tools.slice(0, 5).join(', ')}${srv.tools.length > 5 ? '…' : ''}` : '')
          : (srv.status === 'error' ? _esc(srv.error) : srv.status);
        row.innerHTML =
          `<span title="${_esc(srv.status)}" style="width:10px;height:10px;border-radius:50%;flex-shrink:0;background:${color};"></span>` +
          `<div style="flex:1;min-width:0;">` +
            `<div style="font-weight:600;">${_esc(srv.name)} <span style="font-weight:400;color:#6b7280;font-size:11px;">(${_esc(srv.scope)})</span></div>` +
            `<div style="color:#8b949e;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_esc(srv.command)}">${_esc(srv.command)}</div>` +
            `<div style="color:${srv.status === 'error' ? '#f85149' : '#6b7280'};font-size:12px;">${toolsLabel}</div>` +
          `</div>` +
          `<button class="setting-btn mcp-toggle" data-name="${_esc(srv.name)}" data-enabled="${srv.enabled ? '1' : '0'}">${srv.enabled ? 'Disable' : 'Enable'}</button>` +
          `<button class="setting-btn icon-btn mcp-reconnect" data-name="${_esc(srv.name)}" title="Reconnect">↻</button>` +
          `<button class="setting-btn icon-btn mcp-remove" data-name="${_esc(srv.name)}" title="Remove" style="color:#f85149;">✕</button>`;
        list.appendChild(row);
      });

      list.querySelectorAll('.mcp-toggle').forEach(b => b.addEventListener('click', () => {
        const enable = b.dataset.enabled !== '1';
        if (bridge && typeof bridge.toggleMcpServer === 'function') {
          bridge.toggleMcpServer(b.dataset.name, enable, () => setTimeout(_loadMcpStatus, 400));
        }
      }));
      list.querySelectorAll('.mcp-reconnect').forEach(b => b.addEventListener('click', () => {
        if (bridge && typeof bridge.reconnectMcpServer === 'function') {
          bridge.reconnectMcpServer(b.dataset.name, () => setTimeout(_loadMcpStatus, 1200));
          showToast(`Reconnecting ${b.dataset.name}...`);
        }
      }));
      list.querySelectorAll('.mcp-remove').forEach(b => b.addEventListener('click', () => {
        if (bridge && typeof bridge.removeMcpServer === 'function') {
          bridge.removeMcpServer(b.dataset.name, (ok) => {
            showToast(ok ? `${b.dataset.name} removed` : 'Remove failed');
            _loadMcpStatus();
          });
        }
      }));
    }

    function _setMcpFormEnabled(enabled) {
      /* Enable/disable the Add Server form and Import JSON section */
      const addBtn = $('mcpAddBtn');
      const addName = $('mcpAddName');
      const addCmd = $('mcpAddCommand');
      const addEnv = $('mcpAddEnv');
      const importBtn = $('mcpImportBtn');
      const importJson = $('mcpImportJson');

      /* Locked state must stay READABLE. Bug history: controls were dimmed
         to 0.4 AND their parent card to 0.5 — opacities MULTIPLY, so the
         text inside rendered at ~20% ("hashed", unreadable). Dim only the
         card, moderately; controls keep full opacity inside it. */
      [addBtn, addName, addCmd, addEnv, importBtn, importJson].forEach(el => {
        if (!el) return;
        el.disabled = !enabled;
        el.style.opacity = '';
        el.style.pointerEvents = enabled ? '' : 'none';
      });

      const addCard = addBtn?.closest('.settings-card');
      const importCard = importBtn?.closest('.settings-card');
      [addCard, importCard].forEach(card => {
        if (!card) return;
        card.style.opacity = enabled ? '' : '0.75';
        card.style.pointerEvents = enabled ? '' : 'none';
      });
    }

    function _loadMcpStatus() {
      if (bridge && typeof bridge.getMcpStatus === 'function') {
        bridge.getMcpStatus((raw) => {
          try {
            const data = typeof raw === 'string' ? JSON.parse(raw) : raw;
            _mcpSubscribed = !!data.subscribed;
            if (!_mcpSubscribed) {
              _renderMcpLockCard();
              _setMcpFormEnabled(false);
            } else {
              const list = $('mcpServerList');
              if (list) list.querySelectorAll('.mcp-lock-card').forEach(el => el.remove());
              _renderMcpServers(data.servers || []);
              _setMcpFormEnabled(true);
            }
          } catch (e) { console.error('[MCP] status parse error:', e); }
        });
      }
    }

    $('mcpRefreshBtn')?.addEventListener('click', () => { _loadMcpStatus(); showToast('Refreshing MCP status...'); });

    $('mcpAddBtn')?.addEventListener('click', () => {
      if (!_mcpSubscribed) { showToast('MCP requires a Cortex subscription — cortex-ide.app/pricing'); return; }
      const name = ($('mcpAddName')?.value || '').trim();
      const cmd = ($('mcpAddCommand')?.value || '').trim();
      const env = ($('mcpAddEnv')?.value || '').trim();
      if (!name || !cmd) { showToast('Name and command are required'); return; }
      if (bridge && typeof bridge.addMcpServer === 'function') {
        bridge.addMcpServer(name, cmd, env, (raw) => {
          let r = {}; try { r = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (e) {}
          if (r.success) {
            showToast(`${name} added — connecting...`);
            $('mcpAddName').value = ''; $('mcpAddCommand').value = ''; $('mcpAddEnv').value = '';
            setTimeout(_loadMcpStatus, 1500);
          } else {
            showToast(`Add failed: ${r.error || 'unknown error'}`);
          }
        });
      }
    });

    $('mcpImportBtn')?.addEventListener('click', () => {
      if (!_mcpSubscribed) { showToast('MCP requires a Cortex subscription — cortex-ide.app/pricing'); return; }
      const text = ($('mcpImportJson')?.value || '').trim();
      if (!text) { showToast('Paste a JSON config first'); return; }
      if (bridge && typeof bridge.importMcpJson === 'function') {
        bridge.importMcpJson(text, (raw) => {
          let r = {}; try { r = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (e) {}
          if (r.success) {
            showToast(`Imported ${r.added} server(s) — connecting...`);
            $('mcpImportJson').value = '';
            setTimeout(_loadMcpStatus, 1500);
          } else {
            showToast(`Import failed: ${r.error || 'invalid JSON'}`);
          }
        });
      }
    });

    whenBridgeReady(_loadMcpStatus);
    /* Servers connect asynchronously — refresh status a few times after open */
    whenBridgeReady(() => { setTimeout(_loadMcpStatus, 2500); setTimeout(_loadMcpStatus, 6000); });
    /* Exported for Python (runJavaScript) — the whole file is an IIFE, so
       without this the login-complete handler can't refresh the MCP panel
       and the "Subscription Required" lock card stays until a manual
       Refresh even though the user is now signed in with Pro. */
    window.refreshMcpStatus = _loadMcpStatus;

    /* ── Standalone demo data (only when no bridge) ── */
    setTimeout(() => {
      if (!bridge && !window.__memMgrDebug.loaded) {
        console.info("[SETTINGS] Standalone mode — loading demo data");
        state.scopes.project.memories = [
          { id: "1", title: "Project uses Django 4.2", content: "This project is built on Django 4.2 with PostgreSQL. REST API uses DRF.", type: "architecture", created_at: new Date(Date.now() - 3600000).toISOString() },
          { id: "2", title: "Prefer pytest over unittest", content: "User prefers pytest for all testing. Fixtures in conftest.py.", type: "preference", created_at: new Date(Date.now() - 86400000).toISOString() },
          { id: "3", title: "Database: PostgreSQL 16", content: "Production uses PostgreSQL 16 on AWS RDS. Connection pooling via pgBouncer.", type: "infrastructure", created_at: new Date(Date.now() - 172800000).toISOString() },
        ];
        renderMemoryList();

        /* Demo profile data */
        const demoProfile = { display_name: 'hakeemph', username: 'hakeemph', avatar_color: '#f97316', avatar_initials: 'HA', plan: 'Free' };
        if ($("profileAvatar")) { $("profileAvatar").textContent = demoProfile.avatar_initials; $("profileAvatar").style.background = 'linear-gradient(135deg, ' + demoProfile.avatar_color + ', ' + demoProfile.avatar_color + ')'; }
        if ($("profileName")) $("profileName").textContent = demoProfile.display_name;
        if ($("profileUsername")) $("profileUsername").textContent = '@' + demoProfile.username;
        if ($("profilePlan")) $("profilePlan").textContent = demoProfile.plan;

        /* Demo usage data — generate realistic daily usage for last 30 days */
        const demoDaily = {};
        for (let i = 30; i >= 0; i--) {
          const d = new Date(); d.setDate(d.getDate() - i);
          const key = d.toISOString().slice(0, 10);
          /* Simulate: some days heavy, some light, some zero */
          const base = [0, 0, 8000, 12000, 25000, 45000, 32000, 0, 5000, 18000, 35000, 42000, 28000, 0, 0, 15000, 22000, 38000, 50000, 31000, 0, 9000, 20000, 27000, 41000, 33000, 0, 0, 11000, 19000, 45000];
          demoDaily[key] = { tokens: base[i] || 0, requests: Math.floor((base[i] || 0) / 3500), tool_calls: Math.floor((base[i] || 0) / 1800), models: {} };
        }

        const demoUsage = {
          lifetime: { total_tokens: 209100000, total_requests: 15420, total_tool_calls: 8230, total_sessions: 342, longest_task_seconds: 614 },
          current_period: { start_date: '2026-06-19', end_date: '2026-07-19', tokens_used: 134000, tokens_limit: 200000, requests_used: 32, requests_limit: 100, tool_calls_used: 440, tool_calls_limit: 0 },
          streaks: { current_streak_days: 0, longest_streak_days: 2 },
          daily_usage: demoDaily,
          model_usage: {
            'deepseek-v4': { total_tokens: 72000, total_requests: 180 },
            'gpt-5.4': { total_tokens: 38000, total_requests: 95 },
            'qwen3.7-plus': { total_tokens: 21000, total_requests: 52 },
            'claude-opus': { total_tokens: 12000, total_requests: 30 }
          },
          insights: { fast_mode_percent: 56, most_reasoning_level: 'medium', reasoning_percent: 53, skills_explored: [], total_skills_used: 0, plugins_used: [] },
          peak: { peak_tokens_single_session: 58100000 }
        };
        _cachedUsageData = demoUsage;
        applyUsageData(demoUsage);
      }
    }, 1000);
  });
})();
