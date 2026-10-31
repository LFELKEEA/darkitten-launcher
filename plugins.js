(function () {
  "use strict";

  document.documentElement.classList.add("js");

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const grid = $("#pluginGrid");
  const emptyNote = $("[data-plugin-empty]");
  const filters = $$("[data-filter]");

  const ICONS = [
    ["focus", "i-timer"],
    ["hello", "i-chat"],
    ["screen", "i-eye"],
    ["qq", "i-chat"]
  ];

  const SETTING_LABELS = {
    focus_minutes: "专注",
    break_minutes: "休息",
    interval_minutes: "间隔",
    message: "自定义语句"
  };

  let plugins = [];

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (!value) return "—";
    if (value >= 1024 * 1024) return (value / 1024 / 1024).toFixed(1) + " MB";
    if (value >= 1024) return (value / 1024).toFixed(1) + " KB";
    return value + " B";
  }

  function shortSha(sha) {
    const value = String(sha || "");
    if (value.length <= 28) return value;
    return value.slice(0, 18) + "…" + value.slice(-10);
  }

  function iconFor(id) {
    const key = String(id || "");
    const match = ICONS.find((item) => key.indexOf(item[0]) !== -1);
    return match ? match[1] : "i-plug";
  }

  function safeFile(file, id, version) {
    const value = String(file || "");
    if (/^plugins\/[A-Za-z0-9_.-]+\.zip$/.test(value)) return value;
    return "plugins/" + id + "-" + version + ".zip";
  }

  function settingChips(settings) {
    if (!settings || typeof settings !== "object") return "";
    const chips = [];
    Object.keys(settings).forEach((key) => {
      const raw = settings[key];
      if (raw === "" || raw === null || raw === undefined) return;
      if (Array.isArray(raw) && !raw.length) return;
      if (typeof raw === "object" && !Array.isArray(raw)) return;
      if (/token|secret|password/i.test(key)) return;
      const label = SETTING_LABELS[key] || key;
      let value = raw;
      if (Array.isArray(raw)) value = raw.length + " 项";
      if (typeof raw === "boolean") value = raw ? "开" : "关";
      const unit = key.slice(-8) === "_minutes" ? " 分钟" : "";
      chips.push(
        '<span class="pl-setting">' + escapeHtml(label) +
        " <strong>" + escapeHtml(value) + "</strong>" + unit + "</span>"
      );
    });
    return chips.length ? '<div class="pl-settings">' + chips.join("") + "</div>" : "";
  }

  function highlightChips(plugin) {
    const items = Array.isArray(plugin.highlights) ? plugin.highlights : [];
    if (!items.length) return "";
    const chips = items.map((item) => {
      if (!item || typeof item !== "object") return "";
      return (
        '<span class="pl-setting">' + escapeHtml(item.label || "说明") +
        " <strong>" + escapeHtml(item.value || "") + "</strong></span>"
      );
    }).filter(Boolean);
    return chips.length ? '<div class="pl-settings pl-highlights">' + chips.join("") + "</div>" : "";
  }

  function requiresBlock(plugin) {
    const items = Array.isArray(plugin.requires) ? plugin.requires : [];
    if (!items.length) return "";
    const rows = items.map((item) => {
      if (!item || typeof item !== "object") return "";
      const name = escapeHtml(item.name || "前置程序");
      const version = item.version ? " <strong>" + escapeHtml(item.version) + "</strong>" : "";
      const label = item.url
        ? '<a class="pl-requires-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener">' + name + version + ' <svg class="icon"><use href="#i-arrow"></use></svg></a>'
        : "<span>" + name + version + "</span>";
      const note = item.note ? '<span class="pl-requires-note">' + escapeHtml(item.note) + "</span>" : "";
      return '<div class="pl-requires-row"><span class="pl-requires-label">需要</span>' + label + note + "</div>";
    }).filter(Boolean);
    return rows.length ? '<div class="pl-requires">' + rows.join("") + "</div>" : "";
  }

  function permissionChips(plugin) {
    const perms = Array.isArray(plugin.permissions) ? plugin.permissions : [];
    const labels = Array.isArray(plugin.permission_labels) ? plugin.permission_labels : [];
    if (!perms.length) return "";
    const chips = perms.map((perm, index) => {
      const label = labels[index] || perm;
      return '<span class="pl-perm" title="' + escapeHtml(perm) + '">' + escapeHtml(label) + "</span>";
    });
    return '<div class="pl-perms"><span class="pl-perms-label">权限</span>' + chips.join("") + "</div>";
  }

  function badgeFor(plugin) {
    if (plugin.featured) return '<span class="pl-badge pl-badge-featured">推荐</span>';
    if (plugin.bundled) return '<span class="pl-badge pl-badge-bundled">内置示例</span>';
    return '<span class="pl-badge">社区</span>';
  }

  function cardFor(plugin) {
    const id = String(plugin.id || "");
    const version = String(plugin.version || "");
    const name = String(plugin.name || id);
    const author = String(plugin.author || "—");
    const href = safeFile(plugin.file, id, version);
    const sha = String(plugin.sha256 || "");
    const apiVersion = plugin.api_version ? "v" + esc(plugin.api_version) : "—";

    const article = document.createElement("article");
    article.className = "pl-card" + (plugin.featured ? " pl-featured" : "");
    article.dataset.featured = plugin.featured ? "true" : "false";
    article.dataset.bundled = plugin.bundled ? "true" : "false";
    article.innerHTML =
      '<div class="pl-card-top">' +
        badgeFor(plugin) +
        '<span class="pl-card-version">v' + escapeHtml(version) + "</span>" +
      "</div>" +
      '<div class="pl-card-head">' +
        '<span class="pl-glyph"><svg class="icon" aria-hidden="true"><use href="#' + iconFor(id) + '"></use></svg></span>' +
        '<div class="pl-name"><h3>' + escapeHtml(name) + "</h3>" +
        '<span class="pl-id">' + escapeHtml(id) + "</span></div>" +
      "</div>" +
      '<p class="pl-desc">' + escapeHtml(plugin.description || "") + "</p>" +
      requiresBlock(plugin) +
      (highlightChips(plugin) || settingChips(plugin.settings)) +
      permissionChips(plugin) +
      '<dl class="pl-meta">' +
        "<div><dt>包大小</dt><dd>" + escapeHtml(formatBytes(plugin.size)) + "</dd></div>" +
        "<div><dt>接口</dt><dd>API " + escapeHtml(apiVersion) + "</dd></div>" +
        '<div><dt>作者</dt><dd>' + escapeHtml(author) + "</dd></div>" +
      "</dl>" +
      '<div class="pl-card-foot">' +
        '<a class="btn btn-primary pl-download" href="' + escapeHtml(href) + '" download>' +
          '<svg class="icon" aria-hidden="true"><use href="#i-download"></use></svg>下载 ZIP</a>' +
        '<button class="btn btn-ghost pl-copy" type="button" data-sha="' + escapeHtml(sha) + '">' +
          '<svg class="icon" aria-hidden="true"><use href="#i-copy"></use></svg>复制 SHA-256</button>' +
      "</div>" +
      (sha
        ? '<div class="pl-sha"><span>SHA-256</span><code title="' + escapeHtml(sha) + '">' +
          escapeHtml(shortSha(sha)) + "</code></div>"
        : "");

    article.querySelector("[data-sha]")?.addEventListener("click", (event) => {
      copyText(sha, event.currentTarget);
    });

    return article;
  }

  function esc(value) {
    return escapeHtml(value);
  }

  function render() {
    if (!grid) return;
    grid.innerHTML = "";
    if (!plugins.length) {
      grid.innerHTML = '<p class="pl-noscript">清单里还没有插件。</p>';
      return;
    }
    const fragment = document.createDocumentFragment();
    plugins.forEach((plugin) => fragment.appendChild(cardFor(plugin)));
    grid.appendChild(fragment);
    applyFilter("all");
  }

  function applyFilter(kind) {
    let visible = 0;
    $$(".pl-card", grid).forEach((card) => {
      const featured = card.dataset.featured === "true";
      const bundled = card.dataset.bundled === "true";
      const show = kind === "all" || (kind === "featured" && featured) || (kind === "bundled" && bundled);
      card.classList.toggle("is-hidden", !show);
      if (show) visible += 1;
    });
    if (emptyNote) emptyNote.hidden = visible > 0;
  }

  function copyText(text, button) {
    if (!text) return;
    const done = () => {
      showToast("SHA-256 已复制");
      if (!button) return;
      button.classList.add("is-copied");
      window.setTimeout(() => button.classList.remove("is-copied"), 1400);
    };
    const fallback = () => {
      const area = document.createElement("textarea");
      area.className = "pl-copy-temp";
      area.value = text;
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        done();
      } catch (error) {
        showToast("复制失败，请手动选中校验值");
      }
      document.body.removeChild(area);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    } else {
      fallback();
    }
  }

  let toastNode = null;
  let toastTimer = 0;
  function showToast(message) {
    if (!toastNode) {
      toastNode = document.createElement("div");
      toastNode.className = "pl-toast";
      toastNode.setAttribute("role", "status");
      document.body.appendChild(toastNode);
    }
    toastNode.textContent = message;
    toastNode.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toastNode.classList.remove("show"), 2000);
  }

  function bindFilters() {
    filters.forEach((button) => {
      button.addEventListener("click", () => {
        filters.forEach((item) => {
          const active = item === button;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", active ? "true" : "false");
        });
        applyFilter(button.dataset.filter || "all");
      });
    });
  }

  function observeReveals() {
    const nodes = $$(".reveal");
    if (!("IntersectionObserver" in window)) {
      nodes.forEach((node) => node.classList.add("visible"));
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("visible");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    nodes.forEach((node) => observer.observe(node));
  }

  function updateCounts(data) {
    const total = data.plugins.length;
    $$("[data-plugin-total]").forEach((node) => { node.textContent = String(total); });
    $$("[data-count-all]").forEach((node) => { node.textContent = String(total); });
    if (data.pet_version) {
      $$("[data-pet-version]").forEach((node) => { node.textContent = data.pet_version; });
    }
  }

  async function loadPlugins() {
    if (!grid) return;
    try {
      const response = await fetch("plugins.json", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const data = await response.json();
      plugins = Array.isArray(data.plugins) ? data.plugins : [];
      updateCounts({ plugins, pet_version: data.pet_version });
      render();
    } catch (error) {
      console.warn("plugins.json 读取失败", error);
      grid.innerHTML =
        '<p class="pl-noscript">插件清单暂时读取失败。可以直接下载：' +
        '<a href="plugins/lfelkeea.qq_bridge-1.1.1.zip">QQ 桥接（SnowLuma）</a>、' +
        '<a href="plugins/lfelkeea.focus_guard-1.0.0.zip">专注时间</a>、' +
        '<a href="plugins/example.hello-1.0.0.zip">示例：定时问候</a>、' +
        '<a href="plugins/example.screen_note-1.0.0.zip">示例：屏幕观察补充</a>。' +
        "刷新页面可以重试。</p>";
    }
  }

  async function loadVersion() {
    try {
      const response = await fetch("update.json", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      const version = String(data.version || "").replace(/^v/i, "");
      if (!version) return;
      $$("[data-version]").forEach((node) => { node.textContent = version; });
    } catch (error) {
      console.warn("update.json 读取失败", error);
    }
  }

  bindFilters();
  observeReveals();
  loadVersion();
  loadPlugins();
})();
