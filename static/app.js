(function () {
  "use strict";

  const STORAGE_KEY = "wiki-ai-agent-chat-sessions-v1";
  const SEARCH_SOURCE_STORAGE = "wiki-ai-search-source-v1";

  function getSearchSource() {
    const el = document.querySelector('input[name="search_source"]:checked');
    return el && el.value ? el.value : "both";
  }

  function initSearchSourceRadios() {
    const allowed = ["both", "wiki", "glpi"];
    try {
      const saved = localStorage.getItem(SEARCH_SOURCE_STORAGE);
      if (saved && allowed.indexOf(saved) !== -1) {
        const inp = document.querySelector(
          'input[name="search_source"][value="' + saved + '"]'
        );
        if (inp) inp.checked = true;
      }
    } catch (e) {
      /* storage bloqueado */
    }
    document.querySelectorAll('input[name="search_source"]').forEach(function (inp) {
      inp.addEventListener("change", function () {
        try {
          localStorage.setItem(SEARCH_SOURCE_STORAGE, inp.value);
        } catch (e2) {
          /* noop */
        }
      });
    });
  }

  const layoutShell = document.getElementById("layout-shell");
  const sidebar = document.getElementById("sidebar");
  const sidebarBackdrop = document.getElementById("sidebar-backdrop");
  const btnSidebarToggle = document.getElementById("btn-sidebar-toggle");
  const btnNewChat = document.getElementById("btn-new-chat");
  const sessionListEl = document.getElementById("session-list");
  const messagesEl = document.getElementById("messages");
  let welcomeEl = document.getElementById("welcome");
  const form = document.getElementById("f");
  const input = document.getElementById("q");
  const sendBtn = document.getElementById("send");
  const settingsBtn = document.getElementById("btn-settings");
  const settingsPanel = document.getElementById("settings-panel");
  const settingsClose = document.getElementById("settings-close");
  const envFieldsEl = document.getElementById("env-fields");
  const btnSaveEnv = document.getElementById("btn-save-env");
  const btnSavePersona = document.getElementById("btn-save-persona");
  const personaPathEl = document.getElementById("persona-path");
  const constraintsPathEl = document.getElementById("constraints-path");
  const personaTextEl = document.getElementById("persona-text");
  const constraintsTextEl = document.getElementById("constraints-text");
  const dotenvPathEl = document.getElementById("dotenv-path");
  const settingsStatusEl = document.getElementById("settings-status");
  const revealSecretsEl = document.getElementById("reveal-secrets");
  const lightboxEl = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  const lightboxClose = document.querySelector(".lightbox-close");

  const askParamsEls = {
    debug: () => document.getElementById("ap-debug"),
    no_vision: () => document.getElementById("ap-no-vision"),
    no_pdf: () => document.getElementById("ap-no-pdf"),
    no_assets: () => document.getElementById("ap-no-assets"),
    no_gallery: () => document.getElementById("ap-no-gallery"),
    no_text_attach: () => document.getElementById("ap-no-text-attach"),
    quality: () => document.getElementById("ap-quality"),
  };

  let secretKeys = new Set();
  let stateEnv = {};
  let sessions = [];
  let activeSessionId = null;

  if (
    !layoutShell ||
    !sidebar ||
    !sessionListEl ||
    !messagesEl ||
    !form ||
    !input ||
    !sendBtn ||
    !btnSidebarToggle ||
    !sidebarBackdrop ||
    !btnNewChat ||
    !settingsBtn ||
    !settingsPanel ||
    !settingsClose ||
    !envFieldsEl ||
    !btnSaveEnv ||
    !btnSavePersona ||
    !personaPathEl ||
    !constraintsPathEl ||
    !personaTextEl ||
    !constraintsTextEl ||
    !dotenvPathEl ||
    !settingsStatusEl ||
    !revealSecretsEl ||
    !lightboxEl ||
    !lightboxImg ||
    !lightboxClose
  ) {
    console.error(
      "Wiki AI Agent: se esperaba el HTML actual (barra lateral + layout). " +
        "Si ves la página vieja, recargá con Ctrl+Shift+R o reiniciá el servidor (python web_app.py desde la carpeta del proyecto)."
    );
    return;
  }

  initSearchSourceRadios();

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function renderMarkdown(md) {
    if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
      return escapeHtml(md || "");
    }
    const raw = marked.parse(md || "", { breaks: true, gfm: true });
    return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  }

  function truncateText(t, n) {
    const s = String(t).replace(/\s+/g, " ").trim();
    if (s.length <= n) return s;
    return s.slice(0, Math.max(1, n - 1)) + "…";
  }

  function genId() {
    /* randomUUID() falla en http://IP (no es “secure context”): rompe todo el script. */
    try {
      if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
      }
    } catch (err) {
      /* ignorar */
    }
    return "s-" + Date.now() + "-" + Math.random().toString(36).slice(2, 11);
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function loadSessionsFromStorage() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        sessions = [];
        return;
      }
      const data = JSON.parse(raw);
      if (data && Array.isArray(data.sessions)) {
        sessions = data.sessions
          .filter((s) => s && s.id)
          .map((s) => ({
            id: s.id,
            title: typeof s.title === "string" ? s.title : "",
            updatedAt: s.updatedAt || nowIso(),
            messages: Array.isArray(s.messages) ? s.messages : [],
          }));
        activeSessionId = data.activeSessionId || null;
      } else {
        sessions = [];
      }
    } catch (e) {
      sessions = [];
    }
  }

  function persistSessions() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ v: 1, sessions, activeSessionId })
      );
    } catch (e) {
      /* quota exceeded etc. */
    }
  }

  function getActiveSession() {
    return sessions.find((s) => s.id === activeSessionId) || null;
  }

  function displayTitle(s) {
    if (!s) return "Conversación";
    if (s.title && String(s.title).trim()) return String(s.title).trim();
    const u = s.messages.find((m) => m.role === "user");
    return u ? truncateText(u.content, 52) : "Nueva conversación";
  }

  function renderSidebar() {
    if (!sessionListEl) return;
    sessionListEl.innerHTML = "";
    const sorted = [...sessions].sort(
      (a, b) => new Date(b.updatedAt || 0) - new Date(a.updatedAt || 0)
    );
    sorted.forEach((s) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "session-item" + (s.id === activeSessionId ? " active" : "");
      btn.textContent = displayTitle(s);
      btn.setAttribute("role", "listitem");
      btn.addEventListener("click", () => {
        selectSession(s.id);
        closeMobileNav();
      });
      sessionListEl.appendChild(btn);
    });
  }

  function showWelcomeBlock() {
    messagesEl.innerHTML = "";
    const w = document.createElement("div");
    w.className = "welcome";
    w.id = "welcome";
    w.innerHTML =
      "<p>Formule su consulta abajo. Las respuestas se basan en la documentación autorizada indexada por el sistema.</p>" +
      '<p class="welcome-hint">Use <strong>Enter</strong> para enviar y <strong>Mayús+Enter</strong> para un salto de línea. Puede abrir conversaciones anteriores en el panel izquierdo; el modelo recibe un <em>historial acotado</em> (límites en configuración avanzada <code>.env</code>) para ahorrar tokens.</p>';
    messagesEl.appendChild(w);
    welcomeEl = w;
  }

  function renderMessagesFromSession() {
    const s = getActiveSession();
    messagesEl.innerHTML = "";
    welcomeEl = null;
    if (!s || !s.messages.length) {
      showWelcomeBlock();
      return;
    }
    s.messages.forEach((m) => {
      if (m.role === "user") {
        appendUserBubble(m.content);
      } else {
        appendAssistantBubble(
          renderMarkdown(m.md || ""),
          m.meta || null,
          m.gallery || null
        );
      }
    });
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function newSession() {
    const s = {
      id: genId(),
      title: "",
      updatedAt: nowIso(),
      messages: [],
    };
    sessions.unshift(s);
    activeSessionId = s.id;
    persistSessions();
    renderSidebar();
    showWelcomeBlock();
    input.focus();
  }

  function ensureSession() {
    if (!sessions.length) {
      newSession();
      return;
    }
    if (!activeSessionId || !sessions.some((x) => x.id === activeSessionId)) {
      activeSessionId = sessions[0].id;
      persistSessions();
    }
  }

  function selectSession(id) {
    activeSessionId = id;
    persistSessions();
    renderSidebar();
    renderMessagesFromSession();
    input.focus();
  }

  function buildHistoryForApi() {
    const s = getActiveSession();
    if (!s) return [];
    const hist = [];
    for (const m of s.messages) {
      if (m.role === "user") hist.push({ role: "user", content: m.content });
      else hist.push({ role: "assistant", content: m.md || "" });
    }
    return hist;
  }

  function openMobileNav() {
    layoutShell.classList.add("nav-open");
    document.body.classList.add("nav-open");
    sidebarBackdrop.hidden = false;
    sidebarBackdrop.setAttribute("aria-hidden", "false");
    btnSidebarToggle.setAttribute("aria-expanded", "true");
  }

  function closeMobileNav() {
    layoutShell.classList.remove("nav-open");
    document.body.classList.remove("nav-open");
    sidebarBackdrop.hidden = true;
    sidebarBackdrop.setAttribute("aria-hidden", "true");
    btnSidebarToggle.setAttribute("aria-expanded", "false");
  }

  btnSidebarToggle.addEventListener("click", () => {
    if (layoutShell.classList.contains("nav-open")) closeMobileNav();
    else openMobileNav();
  });
  sidebarBackdrop.addEventListener("click", closeMobileNav);
  btnNewChat.addEventListener("click", () => {
    newSession();
    closeMobileNav();
  });

  function setSettingsStatus(msg, isErr) {
    if (!settingsStatusEl) return;
    settingsStatusEl.textContent = msg || "";
    settingsStatusEl.className = "settings-status" + (isErr ? " error" : "");
  }

  function openSettings() {
    settingsPanel.hidden = false;
    settingsPanel.setAttribute("aria-hidden", "false");
    settingsBtn.setAttribute("aria-expanded", "true");
    document.body.classList.add("settings-open");
  }

  function closeSettings() {
    settingsPanel.hidden = true;
    settingsPanel.setAttribute("aria-hidden", "true");
    settingsBtn.setAttribute("aria-expanded", "false");
    document.body.classList.remove("settings-open");
  }

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (lightboxEl && !lightboxEl.hidden) {
      closeLightbox();
      e.preventDefault();
      return;
    }
    if (settingsPanel && !settingsPanel.hidden) {
      closeSettings();
      e.preventDefault();
      return;
    }
    if (layoutShell && layoutShell.classList.contains("nav-open")) {
      closeMobileNav();
      e.preventDefault();
    }
  });

  function fieldForKey(key, value) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    const label = document.createElement("label");
    label.htmlFor = "env-" + key;
    label.textContent = key;
    wrap.appendChild(label);

    const isSecret = secretKeys.has(key);
    const boolKeys = new Set([
      "WIKI_VISION_ENABLED",
      "WIKI_ATTACH_PDF",
      "WIKI_HTML_ASSETS",
      "WIKI_GALLERY",
      "WIKI_ATTACH_TEXT",
    ]);

    if (key === "WIKI_QUALITY_MODE") {
      const sel = document.createElement("select");
      sel.id = "env-" + key;
      sel.dataset.envKey = key;
      ["economy", "balanced", "thorough"].forEach((v) => {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = v;
        if (v === (value || "balanced")) o.selected = true;
        sel.appendChild(o);
      });
      wrap.appendChild(sel);
      return wrap;
    }

    if (boolKeys.has(key)) {
      const sel = document.createElement("select");
      sel.id = "env-" + key;
      sel.dataset.envKey = key;
      [
        ["true", "true"],
        ["false", "false"],
      ].forEach(([v, t]) => {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t;
        if (v === String(value || "true").toLowerCase()) o.selected = true;
        sel.appendChild(o);
      });
      wrap.appendChild(sel);
      return wrap;
    }

    const inp = document.createElement("input");
    inp.id = "env-" + key;
    inp.dataset.envKey = key;
    inp.type = isSecret && !revealSecretsEl.checked ? "password" : "text";
    inp.value = value == null ? "" : String(value);
    inp.autocomplete = "off";
    inp.spellcheck = false;
    wrap.appendChild(inp);
    return wrap;
  }

  function renderEnvForm(data) {
    envFieldsEl.innerHTML = "";
    secretKeys = new Set(data.secret_keys || []);
    stateEnv = { ...(data.env || {}) };

    (data.env_groups || []).forEach((g) => {
      const details = document.createElement("details");
      details.className = "env-group";
      details.open = true;
      const sum = document.createElement("summary");
      sum.textContent = g.label;
      details.appendChild(sum);
      const inner = document.createElement("div");
      inner.className = "env-group-inner";
      (g.keys || []).forEach((key) => {
        inner.appendChild(fieldForKey(key, stateEnv[key] ?? ""));
      });
      details.appendChild(inner);
      envFieldsEl.appendChild(details);
    });
  }

  function collectEnv() {
    const env = { ...stateEnv };
    envFieldsEl.querySelectorAll("[data-env-key]").forEach((el) => {
      const k = el.dataset.envKey;
      if (el.tagName === "SELECT") env[k] = el.value;
      else env[k] = el.value;
    });
    return env;
  }

  function collectAskParams() {
    const q = askParamsEls.quality();
    const qualityVal = q && q.value ? q.value : null;
    return {
      debug: askParamsEls.debug()?.checked || false,
      no_vision: askParamsEls.no_vision()?.checked || false,
      no_pdf: askParamsEls.no_pdf()?.checked || false,
      no_assets: askParamsEls.no_assets()?.checked || false,
      no_gallery: askParamsEls.no_gallery()?.checked || false,
      no_text_attach: askParamsEls.no_text_attach()?.checked || false,
      quality: qualityVal,
      search_source: getSearchSource(),
    };
  }

  async function loadConfig() {
    try {
      const r = await fetch("/api/config");
      const data = await r.json();
      dotenvPathEl.textContent = data.dotenv_path || "";
      personaPathEl.value = data.persona_path || "";
      constraintsPathEl.value = data.constraints_path || "";
      personaTextEl.value = data.persona_text || "";
      constraintsTextEl.value = data.constraints_text || "";
      renderEnvForm(data);
      const dotInp = document.getElementById("env-WIKI_DOTENV_PATH");
      if (dotInp && data.dotenv_path) {
        dotInp.placeholder =
          "Dejar vacío: archivo .env en la raíz del despliegue (" + data.dotenv_path + ")";
      }
      setSettingsStatus("");
    } catch (e) {
      setSettingsStatus("No fue posible cargar la configuración desde el servidor.", true);
    }
  }

  revealSecretsEl.addEventListener("change", () => {
    const show = revealSecretsEl.checked;
    envFieldsEl.querySelectorAll("input[data-env-key]").forEach((el) => {
      const key = el.dataset.envKey;
      if (secretKeys.has(key)) el.type = show ? "text" : "password";
    });
  });

  btnSaveEnv.addEventListener("click", async () => {
    setSettingsStatus("Guardando archivo de entorno…");
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ env: collectEnv() }),
      });
      const data = await r.json();
      if (data.ok) {
        setSettingsStatus(
          "Guardado correctamente. Reinicie el servicio de la interfaz web si modificó WIKI_WEB_HOST o WIKI_WEB_PORT."
        );
        stateEnv = collectEnv();
      } else {
        setSettingsStatus((data.errors || []).join(" ") || "No se pudieron registrar los cambios.", true);
      }
    } catch (e) {
      setSettingsStatus("No se pudo guardar el archivo de entorno (error de red).", true);
    }
  });

  btnSavePersona.addEventListener("click", async () => {
    setSettingsStatus("Guardando documentos Markdown…");
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          persona_path: personaPathEl.value,
          constraints_path: constraintsPathEl.value,
          persona_text: personaTextEl.value,
          constraints_text: constraintsTextEl.value,
        }),
      });
      const data = await r.json();
      if (data.ok) setSettingsStatus("Archivos de persona y restricciones guardados correctamente.");
      else setSettingsStatus((data.errors || []).join(" ") || "Operación no completada.", true);
    } catch (e) {
      setSettingsStatus("Error de red al guardar los archivos.", true);
    }
  });

  settingsBtn.addEventListener("click", openSettings);
  settingsClose.addEventListener("click", closeSettings);
  settingsPanel.addEventListener("click", (e) => {
    if (e.target === settingsPanel) closeSettings();
  });

  function openLightbox(src) {
    lightboxImg.src = src;
    lightboxEl.hidden = false;
    lightboxEl.setAttribute("aria-hidden", "false");
  }

  function closeLightbox() {
    lightboxEl.hidden = true;
    lightboxEl.setAttribute("aria-hidden", "true");
    lightboxImg.removeAttribute("src");
  }

  lightboxClose.addEventListener("click", closeLightbox);
  lightboxEl.addEventListener("click", (e) => {
    if (e.target === lightboxEl) closeLightbox();
  });

  messagesEl.addEventListener("click", (e) => {
    const t = e.target;
    if (t.tagName === "IMG" && t.src) {
      e.preventDefault();
      openLightbox(t.src);
    }
  });

  function appendUserBubble(text) {
    if (welcomeEl) {
      welcomeEl.remove();
      welcomeEl = null;
    }
    const div = document.createElement("div");
    div.className = "msg user";
    div.innerHTML =
      '<div class="label">Consulta</div><div class="body">' + escapeHtml(text) + "</div>";
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function appendAssistantBubble(html, meta, galleryUrls) {
    const div = document.createElement("div");
    div.className = "msg assistant";
    let inner = '<div class="label">Respuesta</div><div class="body">' + html + "</div>";
    if (meta) inner += '<div class="meta">' + escapeHtml(meta) + "</div>";
    if (galleryUrls && galleryUrls.length) {
      inner += '<div class="gallery" role="list">';
      galleryUrls.forEach((src) => {
        inner +=
          '<img src="' +
          escapeHtml(src) +
          '" alt="Recurso gráfico de la documentación" loading="lazy" decoding="async" role="listitem" />';
      });
      inner += "</div>";
    }
    div.innerHTML = inner;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function addLoading() {
    const div = document.createElement("div");
    div.className = "msg assistant loading";
    div.id = "loading";
    div.textContent = "Procesando consulta…";
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function removeLoading() {
    document.getElementById("loading")?.remove();
  }

  function triggerSendFromTextarea(e) {
    if (e.key !== "Enter") return;
    if (e.shiftKey) return;
    e.preventDefault();
    e.stopPropagation();
    if (!sendBtn || sendBtn.disabled) return;
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
    } else {
      sendBtn.click();
    }
  }

  input.addEventListener("keydown", triggerSendFromTextarea);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = "";

    ensureSession();
    const s = getActiveSession();
    const history = buildHistoryForApi();

    appendUserBubble(question);
    s.messages.push({ role: "user", content: question });
    if (!s.title) s.title = truncateText(question, 56);
    s.updatedAt = nowIso();
    persistSessions();
    renderSidebar();

    sendBtn.disabled = true;
    addLoading();
    try {
      const r = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          history,
          runtime_env: collectEnv(),
          ask_params: collectAskParams(),
        }),
      });
      const data = await r.json();
      removeLoading();
      const hits = data.search_hits;
      const usedGlpi =
        Array.isArray(hits) && hits.some(function (h) { return h && h.type === "glpi_ticket"; });
      let meta =
        (data.model_used ? "Modelo de inferencia: " + data.model_used : "") +
        (data.used_vision ? " · modalidad con visión (solo imágenes de la wiki)" : " · solo texto");
      if (usedGlpi) {
        meta +=
          " · GLPI: coincidencia por texto en el servidor (no semántica); tickets solo como texto (sin visión)";
      }

      if (!data.ok) {
        const errMd =
          data.error || "La operación finalizó sin un mensaje de error detallado.";
        appendAssistantBubble('<p class="error">' + escapeHtml(errMd) + "</p>", null, null);
        s.messages.push({
          role: "assistant",
          md: "**Error:** " + errMd,
          meta: "",
          gallery: [],
        });
      } else {
        appendAssistantBubble(
          renderMarkdown(data.answer),
          meta || null,
          data.image_proxy_urls || []
        );
        s.messages.push({
          role: "assistant",
          md: data.answer || "",
          meta: meta || "",
          gallery: data.image_proxy_urls || [],
        });
      }
      s.updatedAt = nowIso();
      persistSessions();
      renderSidebar();
    } catch (err) {
      removeLoading();
      const errTxt = "No hay conexión con el servidor de aplicación.";
      appendAssistantBubble('<p class="error">' + errTxt + "</p>", null, null);
      s.messages.push({
        role: "assistant",
        md: "**Error:** " + errTxt,
        meta: "",
        gallery: [],
      });
      s.updatedAt = nowIso();
      persistSessions();
      renderSidebar();
    }
    sendBtn.disabled = false;
    input.focus();
  });

  try {
    loadSessionsFromStorage();
    if (!sessions.length) {
      newSession();
    } else {
      ensureSession();
      renderSidebar();
      renderMessagesFromSession();
    }
  } catch (err) {
    console.error("Wiki AI Agent (sesiones):", err);
    sessions = [];
    activeSessionId = null;
    try {
      newSession();
    } catch (e2) {
      console.error("Wiki AI Agent (newSession):", e2);
    }
  }

  try {
    loadConfig();
  } catch (err) {
    console.error("Wiki AI Agent (config):", err);
  }
})();
