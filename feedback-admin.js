(() => {
  "use strict";

  // 网页端只保存令牌的哈希。明文密钥只存在于开发者和启动器输入框里。
  const EXPECTED_HASH = "6546d23169ed77eb99a0a516b0a3007bfbb97814163af02bbd80f57eb34bd81e";
  const TOKEN_SALT = "DKL::feedback-table::";
  const SESSION_KEY = "dkl.feedback.table.token";
  const API_URL =
    "https://api.github.com/repos/LFELKEEA/darkitten-launcher/issues" +
    "?state=all&per_page=100&sort=created&direction=desc";
  const ISSUES_PAGE = "https://github.com/LFELKEEA/darkitten-launcher/issues";
  const REFRESH_COOLDOWN = 30000;

  const KIND_LABELS = {
    bug: "Bug",
    suggestion: "改进建议",
    ui: "界面显示",
    download: "下载安装",
    update: "更新启动",
    other: "其他",
  };

  const KIND_BY_LABEL = {
    bug: "bug",
    "bug / 崩溃": "bug",
    "bug/崩溃": "bug",
    "改进建议": "suggestion",
    "建议": "suggestion",
    "enhancement": "suggestion",
    "界面显示": "ui",
    "界面 / 显示": "ui",
    "ui": "ui",
    "下载安装": "download",
    "下载 / 安装": "download",
    "download": "download",
    "更新启动": "update",
    "更新 / 启动": "update",
    "update": "update",
    "其他": "other",
    "other": "other",
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const state = {
    items: [],
    query: "",
    kind: "",
    status: "",
    loading: false,
    unlocked: false,
    lastFetch: 0,
  };

  const lockPanel = $("#adminLockPanel");
  const appPanel = $("#adminApp");
  const lockButton = $("#adminLock");
  const unlockForm = $("#adminUnlockForm");
  const keyInput = $("#adminKey");
  const unlockButton = $("#adminUnlockBtn");
  const lockStatus = $("#adminLockStatus");
  const statusLine = $("#adminStatusLine");
  const listRoot = $("#adminList");
  const emptyBox = $("#adminEmpty");
  const refreshButton = $("#adminRefresh");
  const searchInput = $("#adminSearch");
  const kindSelect = $("#adminKind");
  const statusSelect = $("#adminStatus");

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setText(node, text) {
    if (node) node.textContent = text;
  }

  function showLockStatus(message, tone) {
    if (!lockStatus) return;
    lockStatus.textContent = message;
    lockStatus.classList.remove("ok", "err");
    if (tone) lockStatus.classList.add(tone);
  }

  function showStatus(message, tone) {
    if (!statusLine) return;
    statusLine.textContent = message;
    statusLine.classList.remove("ok", "err");
    if (tone) statusLine.classList.add(tone);
  }

  async function sha256Hex(text) {
    if (!window.crypto || !window.crypto.subtle) {
      throw new Error("crypto-unavailable");
    }
    const data = new TextEncoder().encode(text);
    const digest = await window.crypto.subtle.digest("SHA-256", data);
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }

  async function tokenMatches(token) {
    const value = String(token || "").trim();
    if (!value) return false;
    try {
      return (await sha256Hex(value)) === EXPECTED_HASH;
    } catch (error) {
      return false;
    }
  }

  async function passphraseMatches(passphrase) {
    const value = String(passphrase || "").trim();
    if (!value) return false;
    try {
      return (await sha256Hex(TOKEN_SALT + value)) === EXPECTED_HASH;
    } catch (error) {
      return false;
    }
  }

  function readSessionToken() {
    try {
      return window.sessionStorage.getItem(SESSION_KEY) || "";
    } catch (error) {
      return "";
    }
  }

  function writeSessionToken(token) {
    try {
      window.sessionStorage.setItem(SESSION_KEY, token);
    } catch (error) {
      // 隐私模式下可能无法写入，本次仍可继续使用。
    }
  }

  function clearSessionToken() {
    try {
      window.sessionStorage.removeItem(SESSION_KEY);
    } catch (error) {
      // 忽略存储不可用。
    }
  }

  function clearHash() {
    try {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    } catch (error) {
      // 忽略历史记录接口不可用。
    }
  }

  async function startUnlocked(token) {
    state.unlocked = true;
    writeSessionToken(token);
    clearHash();
    if (lockPanel) lockPanel.hidden = true;
    if (appPanel) appPanel.hidden = false;
    if (lockButton) lockButton.hidden = false;
    setText(lockStatus, "");
    await loadIssues(true);
  }

  function showLock(message, tone) {
    state.unlocked = false;
    if (lockPanel) lockPanel.hidden = false;
    if (appPanel) appPanel.hidden = true;
    if (lockButton) lockButton.hidden = true;
    if (message) showLockStatus(message, tone);
  }

  async function boot() {
    const rawHash = String(window.location.hash || "").replace(/^#/, "");
    let hashToken = "";
    if (rawHash) {
      try {
        hashToken = new URLSearchParams(rawHash).get("key") || "";
      } catch (error) {
        hashToken = "";
      }
    }

    if (hashToken) {
      if (await tokenMatches(hashToken)) {
        await startUnlocked(hashToken);
        return;
      }
      clearHash();
      showLock("访问凭证无效，请从启动器的「设置 → 开发者模式」重新打开反馈表。", "err");
      return;
    }

    const saved = readSessionToken();
    if (saved && (await tokenMatches(saved))) {
      await startUnlocked(saved);
      return;
    }

    clearSessionToken();
    showLock("", "");
  }

  function isDklIssue(issue) {
    return Boolean(
      issue &&
        !issue.pull_request &&
        typeof issue.title === "string" &&
        issue.title.startsWith("[DKL")
    );
  }

  function parseMetadata(body) {
    const meta = {};
    const lines = String(body || "").split(/\r?\n/);
    for (const line of lines) {
      if (/^\s*---\s*$/.test(line)) break;
      const match = line.match(
        /^\*\*(来源|类型|启动器版本|联系方式|页面|系统)\*\*[：:]\s*(.*)$/
      );
      if (match) meta[match[1]] = match[2].trim();
    }
    return meta;
  }

  function extractDescription(body) {
    const lines = String(body || "").split(/\r?\n/);
    let start = -1;
    for (let index = 0; index < lines.length; index += 1) {
      if (/^\s*---\s*$/.test(lines[index])) {
        start = index + 1;
        break;
      }
    }
    let text = (start >= 0 ? lines.slice(start) : lines).join("\n").trim();
    text = text.replace(/^>\s*这条反馈通过[^\n]*$/gm, "").trim();
    return text;
  }

  function kindFromLabel(value) {
    const label = String(value || "").trim().toLowerCase();
    return KIND_BY_LABEL[label] || "other";
  }

  function normalizeIssue(issue) {
    const rawTitle = String(issue.title || "");
    const titleMatch = rawTitle.match(/^\[DKL\s+([^\]]+)\]\s*(.*)$/);
    const titleLabel = titleMatch ? titleMatch[1].trim() : "";
    const title = titleMatch ? titleMatch[2].trim() : rawTitle;
    const meta = parseMetadata(issue.body);
    const kind = kindFromLabel(meta["类型"] || titleLabel);
    const url = String(issue.html_url || "");
    return {
      id: Number(issue.number) || 0,
      title: title || "（没有标题）",
      kind,
      kindLabel: KIND_LABELS[kind] || meta["类型"] || "其他",
      status: issue.state === "closed" ? "closed" : "open",
      version: meta["启动器版本"] || "",
      contact: meta["联系方式"] || "",
      page: meta["页面"] || "",
      system: meta["系统"] || "",
      source: meta["来源"] || "GitHub",
      description: extractDescription(issue.body),
      createdAt: issue.created_at || "",
      comments: Number(issue.comments) || 0,
      url: url.startsWith("https://github.com/") ? url : ISSUES_PAGE,
    };
  }

  function formatTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "时间未知";
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  function updateStats() {
    const total = state.items.length;
    const open = state.items.filter((item) => item.status === "open").length;
    setText($("#adminTotal"), String(total));
    setText($("#adminOpen"), String(open));
    setText($("#adminClosed"), String(total - open));
    setText(
      $("#adminUpdated"),
      state.lastFetch ? new Date(state.lastFetch).toLocaleTimeString("zh-CN", { hour12: false }) : "—"
    );
  }

  function metaBit(label, value) {
    const span = el("span");
    span.append(el("b", "", label + " "), document.createTextNode(value));
    return span;
  }

  function badge(className, text) {
    return el("span", "fb-badge " + className, text);
  }

  function buildItem(item) {
    const article = el("article", "feedback-item");

    const main = el("div", "fb-main");
    const info = el("div", "fb-info");

    const titleRow = el("div", "fb-title-row");
    titleRow.append(
      badge("status-" + item.status, item.status === "closed" ? "已处理" : "待处理"),
      badge("kind-" + item.kind, item.kindLabel)
    );
    if (item.comments > 0) {
      titleRow.append(badge("comments", item.comments + " 条回复"));
    }
    info.append(titleRow);
    info.append(el("h3", "fb-title", item.title));

    const meta = el("div", "fb-meta");
    meta.append(metaBit("版本", item.version || "未填写"));
    meta.append(metaBit("来源", item.source));
    if (item.contact) meta.append(metaBit("联系方式", item.contact));
    if (item.page) meta.append(metaBit("页面", item.page));
    if (item.system) meta.append(metaBit("系统", item.system));
    info.append(meta);

    const actions = el("div", "fb-actions");
    actions.append(el("span", "fb-time", formatTime(item.createdAt)));
    const link = el("a", "btn btn-ghost", "在 GitHub 查看");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    actions.append(link);

    main.append(info, actions);
    article.append(main);

    const details = el("details", "fb-description");
    details.append(el("summary", "", "展开描述"));
    details.append(
      el(
        "p",
        "fb-body" + (item.description ? "" : " empty"),
        item.description || "（提交者没有填写详细描述。）"
      )
    );
    article.append(details);

    return article;
  }

  function filteredItems() {
    const query = state.query.trim().toLowerCase();
    return state.items.filter((item) => {
      if (state.kind && item.kind !== state.kind) return false;
      if (state.status && item.status !== state.status) return false;
      if (!query) return true;
      const haystack = [
        item.title,
        item.description,
        item.contact,
        item.page,
        item.system,
        item.version,
        item.kindLabel,
        "#" + item.id,
      ]
        .join("\n")
        .toLowerCase();
      return haystack.includes(query);
    });
  }

  function renderList() {
    if (!listRoot) return;
    const items = filteredItems();
    listRoot.replaceChildren();
    items.forEach((item) => listRoot.append(buildItem(item)));
    if (emptyBox) emptyBox.hidden = items.length > 0;
    if (!state.loading) {
      if (state.items.length === 0) {
        showStatus("还没有读取到 DKL 反馈。如果刚提交，等 GitHub 刷新后再点一次“刷新”。", "");
      } else {
        showStatus(
          "显示 " + items.length + " / " + state.items.length + " 条反馈。共 " +
            state.items.filter((item) => item.status === "open").length + " 条待处理。",
          ""
        );
      }
    }
  }

  function updateRefreshButton() {
    if (!refreshButton) return;
    if (state.loading) {
      refreshButton.disabled = true;
      refreshButton.textContent = "读取中…";
      return;
    }
    const remaining = REFRESH_COOLDOWN - (Date.now() - state.lastFetch);
    if (state.lastFetch && remaining > 0) {
      refreshButton.disabled = true;
      refreshButton.textContent = "刷新 (" + Math.ceil(remaining / 1000) + "s)";
      return;
    }
    refreshButton.disabled = false;
    refreshButton.textContent = "刷新";
  }

  function rateLimitMessage(response) {
    const reset = Number(response.headers.get("x-ratelimit-reset") || 0);
    if (reset > 0) {
      const time = new Date(reset * 1000).toLocaleTimeString("zh-CN", { hour12: false });
      return "GitHub 匿名接口的读取次数用完了，大约 " + time + " 后恢复。也可以直接去 GitHub Issues 查看。";
    }
    return "GitHub 暂时限制了读取频率，请稍后再试，或直接去 GitHub Issues 查看。";
  }

  async function loadIssues(force) {
    if (!state.unlocked || state.loading) return;
    if (!force && state.lastFetch && Date.now() - state.lastFetch < REFRESH_COOLDOWN) {
      return;
    }
    state.loading = true;
    updateRefreshButton();
    showStatus("正在读取 GitHub 反馈…", "");
    try {
      const response = await fetch(API_URL, {
        headers: { Accept: "application/vnd.github+json" },
        cache: "no-store",
      });
      if (!response.ok) {
        if (response.status === 403 || response.status === 429) {
          throw new Error(rateLimitMessage(response));
        }
        throw new Error("GitHub 返回 HTTP " + response.status + "，请稍后再试。");
      }
      const data = await response.json();
      if (!Array.isArray(data)) {
        throw new Error("GitHub 返回了无法识别的数据。");
      }
      state.items = data.filter(isDklIssue).map(normalizeIssue);
      state.lastFetch = Date.now();
      updateStats();
      renderList();
      showStatus("已同步 " + state.items.length + " 条反馈。", "ok");
    } catch (error) {
      const message =
        String(error && error.message) === "crypto-unavailable"
          ? "当前浏览器不支持 Web Crypto，请通过启动器打开反馈表。"
          : String((error && error.message) || "网络请求失败，请稍后再试。");
      showStatus(message, "err");
      if (state.items.length === 0 && emptyBox) emptyBox.hidden = true;
    } finally {
      state.loading = false;
      updateRefreshButton();
    }
  }

  if (unlockForm) {
    unlockForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = keyInput ? keyInput.value.trim() : "";
      if (!value) {
        showLockStatus("请输入开发者密钥。", "err");
        if (keyInput) keyInput.focus();
        return;
      }
      unlockButton.disabled = true;
      showLockStatus("正在验证…", "");
      try {
        if (await passphraseMatches(value)) {
          const token = await sha256Hex(TOKEN_SALT + value);
          await startUnlocked(token);
        } else {
          showLockStatus("密钥不正确。请使用启动器设置里配置的开发者密钥。", "err");
          if (keyInput) {
            keyInput.classList.add("invalid");
            keyInput.focus();
            keyInput.select();
          }
        }
      } catch (error) {
        showLockStatus(
          String(error && error.message) === "crypto-unavailable"
            ? "当前环境不支持 Web Crypto，请通过启动器打开反馈表。"
            : "验证失败，请刷新页面后重试。",
          "err"
        );
      } finally {
        unlockButton.disabled = false;
      }
    });
  }

  if (keyInput) {
    keyInput.addEventListener("input", () => {
      keyInput.classList.remove("invalid");
    });
  }

  if (lockButton) {
    lockButton.addEventListener("click", () => {
      clearSessionToken();
      state.items = [];
      state.lastFetch = 0;
      state.unlocked = false;
      if (listRoot) listRoot.replaceChildren();
      if (emptyBox) emptyBox.hidden = true;
      if (keyInput) keyInput.value = "";
      showLock("已锁定。重新输入密钥或从启动器打开即可继续。", "");
    });
  }

  if (refreshButton) {
    refreshButton.addEventListener("click", () => loadIssues(true));
  }

  if (searchInput) {
    searchInput.addEventListener("input", () => {
      state.query = searchInput.value || "";
      renderList();
    });
  }

  if (kindSelect) {
    kindSelect.addEventListener("change", () => {
      state.kind = kindSelect.value || "";
      renderList();
    });
  }

  if (statusSelect) {
    statusSelect.addEventListener("change", () => {
      state.status = statusSelect.value || "";
      renderList();
    });
  }

  window.setInterval(updateRefreshButton, 1000);
  boot().catch(() => {
    showLock("页面初始化失败，请刷新后重试。", "err");
  });
})();
