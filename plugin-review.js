(function () {
  "use strict";

  const REPO = "LFELKEEA/darkitten-launcher";
  const API = "https://api.github.com/repos/" + REPO + "/issues";
  const LABEL = "plugin-submission";
  const PAGE_SIZE = 100;
  const MAX_PAGES = 3;
  const MARKER = "<!-- dkl-plugin-submission:v1 -->";
  const JSON_BLOCK = /```json\s*([\s\S]*?)```/i;
  const SNAPSHOT_URL = "plugin-queue.json";

  const PERMISSIONS = {
    "pet.say": "让桌宠说话",
    "pet.action": "触发桌宠动作",
    "ai.chat": "调用 AI 对话",
    "screen.capture": "截取屏幕",
    "timer": "创建定时器",
    "events": "订阅事件",
    "settings": "读写插件设置",
    "storage": "读写插件存储"
  };

  const STATUS_TEXT = {
    pending: "待审核",
    validated: "已通过自动检查",
    approved: "已批准，等待发布",
    published: "已发布",
    rejected: "已退回",
    invalid: "自动校验未通过",
    closed: "已关闭"
  };

  const FILTER_LABELS = {
    pending: {
      title: "当前没有待审核的投稿",
      text: "新的投稿会出现在这里。也可以直接去 GitHub 查看全部投稿 Issue。"
    },
    validated: {
      title: "当前没有已校验的投稿",
      text: "投稿通过 Actions 自动检查后会出现在这里，等待维护者阅读代码和确认权限。"
    },
    published: {
      title: "还没有已发布的社区插件",
      text: "维护者评论 /approve 并通过再次校验后，插件会自动发布到插件库和桌宠插件市场。"
    },
    rejected: {
      title: "当前没有已退回的投稿",
      text: "被退回或自动校验失败的投稿会保留在这里，方便投稿人查看原因。"
    },
    all: {
      title: "还没有社区投稿",
      text: "通过站点投稿页提交的插件会出现在这里。"
    }
  };

  const grid = document.getElementById("reviewGrid");
  const template = document.getElementById("reviewCardTemplate");
  const empty = document.getElementById("reviewEmpty");
  const emptyTitle = document.getElementById("reviewEmptyTitle");
  const emptyText = document.getElementById("reviewEmptyText");
  const notice = document.getElementById("reviewNotice");
  const updated = document.getElementById("reviewUpdated");
  const refresh = document.getElementById("refreshQueue");
  const search = document.getElementById("reviewSearch");
  const toast = document.getElementById("reviewToast");

  const state = {
    issues: [],
    filter: "pending",
    query: "",
    loading: false,
    loaded: false,
    snapshotAt: "",
    apiFailed: false,
    toastTimer: 0
  };

  function text(node, value) {
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (!value) return "";
    if (value >= 1024 * 1024) return (value / 1024 / 1024).toFixed(2) + " MB";
    if (value >= 1024) return (value / 1024).toFixed(1) + " KB";
    return value + " B";
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (part) => String(part).padStart(2, "0");
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
  }

  function formatDateTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (part) => String(part).padStart(2, "0");
    return (
      date.getFullYear() +
      "-" + pad(date.getMonth() + 1) +
      "-" + pad(date.getDate()) +
      " " + pad(date.getHours()) +
      ":" + pad(date.getMinutes())
    );
  }

  function labelSet(issue) {
    return new Set(
      (issue.labels || []).map((label) => (typeof label === "string" ? label : label && label.name)).filter(Boolean)
    );
  }

  function statusOf(issue) {
    const labels = labelSet(issue);
    if (labels.has("plugin-published")) return "published";
    if (labels.has("plugin-rejected")) return "rejected";
    if (labels.has("plugin-invalid")) return "invalid";
    if (labels.has("plugin-approved")) return "approved";
    if (labels.has("plugin-validated")) return "validated";
    if (issue.state === "closed") return "closed";
    return "pending";
  }

  function metadataOf(issue) {
    if (issue.plugin && typeof issue.plugin === "object") return issue.plugin;
    const body = issue.body || "";
    if (!body.includes(MARKER)) return null;
    const match = JSON_BLOCK.exec(body);
    if (!match) return null;
    try {
      const meta = JSON.parse(match[1]);
      return meta && typeof meta === "object" && !Array.isArray(meta) ? meta : null;
    } catch (error) {
      return null;
    }
  }

  function formatShort(value, length) {
    const textValue = String(value || "");
    if (!textValue) return "";
    return textValue.length > length ? textValue.slice(0, length) + "…" : textValue;
  }

  function matchesFilter(issue, filter) {
    const status = statusOf(issue);
    if (filter === "all") return true;
    if (filter === "validated") return status === "validated" || status === "approved";
    if (filter === "rejected") return status === "rejected" || status === "invalid";
    return status === filter;
  }

  function matchesQuery(issue, meta, query) {
    if (!query) return true;
    const haystack = [
      issue.number,
      issue.title,
      (issue.user && issue.user.login) || "",
      meta && meta.id,
      meta && meta.name,
      meta && meta.author,
      meta && meta.version
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  }

  function visibleIssues() {
    const query = state.query.trim().toLowerCase();
    return state.issues.filter((issue) => {
      const meta = metadataOf(issue);
      return matchesFilter(issue, state.filter) && matchesQuery(issue, meta, query);
    });
  }

  function setCount(name, value) {
    document.querySelectorAll('[data-count="' + name + '"]').forEach((node) => {
      text(node, value);
    });
  }

  function setStat(name, value) {
    const node = document.querySelector('[data-stat="' + name + '"]');
    text(node, value);
  }

  function updateStats() {
    const counts = { pending: 0, validated: 0, published: 0, rejected: 0, all: state.issues.length };
    state.issues.forEach((issue) => {
      const status = statusOf(issue);
      if (status === "pending") counts.pending += 1;
      if (status === "validated" || status === "approved") counts.validated += 1;
      if (status === "published") counts.published += 1;
      if (status === "rejected" || status === "invalid") counts.rejected += 1;
    });
    setStat("pending", counts.pending);
    setStat("validated", counts.validated);
    setStat("published", counts.published);
    setStat("rejected", counts.rejected);
    setCount("pending", counts.pending);
    setCount("validated", counts.validated);
    setCount("published", counts.published);
    setCount("rejected", counts.rejected);
    setCount("all", counts.all);
  }

  function showNotice(message) {
    notice.innerHTML = "";
    if (!message) {
      notice.hidden = true;
      return;
    }
    const paragraph = document.createElement("span");
    paragraph.textContent = message + " ";
    notice.appendChild(paragraph);
    const link = document.createElement("a");
    link.href = "https://github.com/" + REPO + "/issues?q=label%3A" + LABEL;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "直接打开 GitHub 队列";
    notice.appendChild(link);
    notice.hidden = false;
  }

  function updateUpdated() {
    if (!updated) return;
    if (state.snapshotAt) {
      text(
        updated,
        "队列快照更新于 " +
          formatDateTime(state.snapshotAt) +
          (state.apiFailed ? "（GitHub 实时接口暂时不可用，状态可能略有延迟）" : "")
      );
      updated.hidden = false;
      return;
    }
    if (state.loaded && !state.apiFailed) {
      text(updated, "已从 GitHub 实时读取。");
      updated.hidden = false;
      return;
    }
    text(updated, "");
    updated.hidden = true;
  }

  function showToast(message) {
    if (!toast) return;
    text(toast, message);
    toast.hidden = false;
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 2400);
  }

  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch (error) {
      const area = document.createElement("textarea");
      area.value = value;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      let copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (fallbackError) {
        copied = false;
      }
      document.body.removeChild(area);
      return copied;
    }
  }

  function buildCard(issue, meta) {
    const node = template.content.firstElementChild.cloneNode(true);
    const status = statusOf(issue);
    const statusNode = node.querySelector('[data-role="status"]');
    statusNode.className = "review-status is-" + status;
    text(statusNode, STATUS_TEXT[status] || status);

    const submitted = formatDate(issue.created_at);
    text(node.querySelector('[data-role="issue"]'), "#" + issue.number + (submitted ? " · " + submitted : ""));

    text(node.querySelector('[data-role="title"]'), (meta && meta.name) || issue.title || "未命名插件");
    text(
      node.querySelector('[data-role="description"]'),
      (meta && meta.description) || "投稿人没有填写插件描述。"
    );
    text(node.querySelector('[data-role="id"]'), (meta && meta.id) || "—");
    text(node.querySelector('[data-role="version"]'), meta && meta.version ? "v" + meta.version : "—");
    text(node.querySelector('[data-role="author"]'), (meta && meta.author) || "未署名");
    text(node.querySelector('[data-role="submitter"]'), (issue.user && issue.user.login) || "未知");

    const permRoot = node.querySelector('[data-role="perms"]');
    const permissions = meta && Array.isArray(meta.permissions) ? meta.permissions : [];
    if (permissions.length) {
      permissions.forEach((permission) => {
        const chip = document.createElement("span");
        chip.className = "review-perm";
        chip.title = permission;
        chip.textContent = PERMISSIONS[permission] || permission;
        permRoot.appendChild(chip);
      });
    } else {
      const chip = document.createElement("span");
      chip.className = "review-perm is-empty";
      chip.textContent = "未声明权限";
      permRoot.appendChild(chip);
    }

    const size = meta && meta.size ? formatBytes(meta.size) : "";
    text(node.querySelector('[data-role="size"]'), size || "大小未知");
    const sha = node.querySelector('[data-role="sha"]');
    const digest = (meta && meta.sha256) || "";
    text(sha, digest ? formatShort(digest, 20) : "缺少 SHA-256");
    if (digest) sha.title = digest;

    const open = node.querySelector('[data-role="open"]');
    const url = String(issue.html_url || "");
    open.href = url.startsWith("https://github.com/") ? url : "https://github.com/" + REPO + "/issues/" + issue.number;

    node.querySelector('[data-role="copy-approve"]').addEventListener("click", async () => {
      const copied = await copyText("/approve");
      showToast(copied ? "已复制 /approve，请粘贴到 Issue 评论。" : "复制失败，请手动输入 /approve。");
    });
    node.querySelector('[data-role="copy-reject"]').addEventListener("click", async () => {
      const copied = await copyText("/reject ");
      showToast(copied ? "已复制 /reject，请在后面补上退回原因。" : "复制失败，请手动输入 /reject 原因。");
    });

    return node;
  }

  function render() {
    updateStats();
    grid.innerHTML = "";

    if (state.loading && !state.loaded) {
      empty.hidden = false;
      text(emptyTitle, "正在读取投稿队列…");
      text(emptyText, "从 GitHub 公开接口读取 Issue 和标签，不会执行任何插件代码。");
      return;
    }

    const issues = visibleIssues();
    if (!issues.length) {
      empty.hidden = false;
      const labels = FILTER_LABELS[state.filter] || FILTER_LABELS.all;
      text(emptyTitle, state.query ? "没有匹配的投稿" : labels.title);
      text(
        emptyText,
        state.query ? "换一个关键词试试，或者切换到「全部」查看所有投稿。" : labels.text
      );
      return;
    }

    empty.hidden = true;
    const fragment = document.createDocumentFragment();
    issues.forEach((issue) => {
      fragment.appendChild(buildCard(issue, metadataOf(issue)));
    });
    grid.appendChild(fragment);
  }

  async function fetchSnapshot() {
    try {
      const response = await fetch(SNAPSHOT_URL + "?t=" + Date.now(), { cache: "no-store" });
      if (!response.ok) throw new Error("快照返回 " + response.status);
      const data = await response.json();
      const items = Array.isArray(data) ? data : data && Array.isArray(data.items) ? data.items : null;
      if (!items) throw new Error("快照格式无法识别。");
      return {
        items: items.filter((item) => item && Number.isFinite(Number(item.number))),
        generatedAt: (data && data.generated_at) || ""
      };
    } catch (error) {
      return null;
    }
  }

  async function fetchIssues() {
    const collected = [];
    for (let page = 1; page <= MAX_PAGES; page += 1) {
      const url =
        API +
        "?state=all&labels=" + encodeURIComponent(LABEL) +
        "&sort=created&direction=desc&per_page=" + PAGE_SIZE +
        "&page=" + page;
      const response = await fetch(url, {
        headers: { Accept: "application/vnd.github+json" }
      });
      if (!response.ok) {
        if (response.status === 403 || response.status === 429) {
          throw new Error("GitHub 公共接口暂时限流（未登录时每小时 60 次）。");
        }
        throw new Error("GitHub 接口返回 " + response.status + "。");
      }
      const data = await response.json();
      if (!Array.isArray(data)) throw new Error("GitHub 返回了无法识别的数据。");
      data
        .filter((item) => item && !item.pull_request)
        .forEach((item) => collected.push(item));
      if (data.length < PAGE_SIZE) break;
    }
    return collected;
  }

  async function loadQueue() {
    if (state.loading) return;
    state.loading = true;
    state.loaded = false;
    state.apiFailed = false;
    refresh.disabled = true;
    showNotice("");
    render();

    // 优先读站点同源快照：即使浏览器访问不了 api.github.com，审核队列也能正常显示。
    const snapshot = await fetchSnapshot();
    if (snapshot) {
      state.issues = snapshot.items;
      state.snapshotAt = snapshot.generatedAt;
      state.loaded = true;
      state.loading = false;
      render();
    }

    // 再尝试 GitHub 公共接口拿实时状态；失败时保留快照，不打断阅读。
    try {
      const collected = await fetchIssues();
      state.issues = collected;
      state.snapshotAt = "";
      state.loaded = true;
      showNotice("");
    } catch (error) {
      state.apiFailed = true;
      if (!snapshot) {
        state.issues = [];
        state.loaded = true;
        showNotice((error && error.message) || "读取投稿队列失败。");
      }
    } finally {
      state.loading = false;
      refresh.disabled = false;
      updateUpdated();
      render();
    }
  }

  document.querySelectorAll(".review-filter").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter || "all";
      document.querySelectorAll(".review-filter").forEach((item) => {
        const active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", active ? "true" : "false");
      });
      render();
    });
  });

  search.addEventListener("input", () => {
    state.query = search.value || "";
    render();
  });

  refresh.addEventListener("click", loadQueue);

  render();
  loadQueue();
})();
