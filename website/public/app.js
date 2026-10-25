(function () {
  "use strict";

  document.documentElement.classList.add("js");

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (!value) return "读取中…";
    if (value >= 1024 * 1024) return (value / 1024 / 1024).toFixed(1) + " MB";
    if (value >= 1024) return Math.round(value / 1024) + " KB";
    return value + " B";
  }

  function setText(selector, text) {
    if (!text) return;
    $$(selector).forEach((node) => { node.textContent = text; });
  }

  function setHref(selector, href) {
    if (!href) return;
    $$(selector).forEach((node) => { node.setAttribute("href", href); });
  }

  let manifest = null;

  async function loadManifest() {
    try {
      const response = await fetch("update.json", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      manifest = await response.json();
    } catch (error) {
      console.warn("update.json 读取失败", error);
      showToast("更新清单暂时不可用，静态下载链接仍然有效");
      return;
    }

    const version = String(manifest.version || "").replace(/^v/i, "");
    setText("[data-version]", version);
    setText("[data-date]", manifest.released || "");
    setText("[data-size]", formatBytes(manifest.source_size || manifest.size));
    setText("[data-update-size]", formatBytes(manifest.size));
    setText("[data-source-txt-size]", formatBytes(manifest.source_txt_size));
    setText("[data-sha]", manifest.sha256 || "未提供校验值");

    const sourceUrl = manifest.source_url || manifest.download_url;
    setHref("[data-source-download]", sourceUrl);
    setHref("[data-update-download]", manifest.download_url);
    setHref("[data-source-txt]", manifest.source_txt_url);

    const versionInput = $('input[name="version"]', $("#feedbackForm") || document);
    if (versionInput && !versionInput.dataset.touched) versionInput.value = version;

    if (Array.isArray(manifest.notes) && manifest.notes.length) {
      const list = $("#release-notes");
      if (list) {
        list.innerHTML = "";
        manifest.notes.forEach((note) => {
          const item = document.createElement("li");
          item.textContent = String(note);
          list.appendChild(item);
        });
      }
    }

    if (version) {
      document.title = "Darkitten Launcher v" + version + " · 恶魔小猫的秘密基地";
    }
  }

  let toastTimer = 0;
  function showToast(text) {
    const toast = $("#toast");
    if (!toast) return;
    toast.textContent = text;
    toast.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2600);
  }

  function bindCopy() {
    const button = $("[data-copy-sha]");
    if (!button) return;
    button.addEventListener("click", async () => {
      const digest = manifest && manifest.sha256;
      if (!digest) {
        showToast("还没有读取到 SHA-256");
        return;
      }
      try {
        await navigator.clipboard.writeText(digest);
      } catch (error) {
        const area = document.createElement("textarea");
        area.value = digest;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      showToast("SHA-256 已复制");
    });
  }

  function bindReveal() {
    const items = $$(".reveal");
    if (!("IntersectionObserver" in window)) {
      items.forEach((item) => item.classList.add("visible"));
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("visible");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08, rootMargin: "0px 0px -40px 0px" });
    items.forEach((item, index) => {
      item.style.transitionDelay = Math.min(index * 35, 220) + "ms";
      observer.observe(item);
    });
    window.setTimeout(() => {
      items.forEach((item) => item.classList.add("visible"));
      observer.disconnect();
    }, 2600);
  }

  function bindHeader() {
    const header = $(".site-header");
    if (!header) return;
    const update = () => {
      header.classList.toggle("scrolled", window.scrollY > 12);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
  }

  function bindParallax() {
    const art = $("#heroArt");
    const card = art && $(".art-card", art);
    if (!art || !card) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    art.addEventListener("pointermove", (event) => {
      const rect = art.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      card.style.transform =
        "rotateY(" + (-7 + x * 10).toFixed(2) + "deg) rotateX(" +
        (2 - y * 8).toFixed(2) + "deg) translateY(-4px)";
    });
    art.addEventListener("pointerleave", () => {
      card.style.transform = "rotateY(-7deg) rotateX(2deg)";
    });
  }

  const FEEDBACK_EMAIL = "lfel278623@outlook.com";
  const GITHUB_NEW_ISSUE = "https://github.com/LFELKEEA/darkitten-launcher/issues/new";
  const FEEDBACK_VERSION_FALLBACK = "1.11.2";
  const KIND_LABELS = {
    bug: "Bug",
    suggestion: "改进建议",
    ui: "界面显示",
    download: "下载安装",
    update: "更新启动",
    other: "其他",
  };

  const FEEDBACK_COPY = {
    bug: {
      kicker: "BUG REPORT",
      title: "猫猫收到一只虫子。",
      lede: "描述发生了什么、你期待什么，以及能否稳定复现。带版本号的信息最有用。",
      button: "提交反馈",
      titlePlaceholder: "例如：安装 1.21.1 NeoForge 后启动报错",
      descriptionPlaceholder: "操作步骤、看到的报错、复现频率。请勿填写账号密码或令牌。",
    },
    suggestion: {
      kicker: "IMPROVEMENT",
      title: "猫猫想听你的主意。",
      lede: "希望启动器多做什么、少做什么，或者哪里用起来不顺手？具体的使用场景比一句“优化一下”更有帮助。",
      button: "提交建议",
      titlePlaceholder: "例如：希望下载中心支持按完成时间排序",
      descriptionPlaceholder: "希望新增什么功能、现在哪个流程不顺手、你通常在什么场景下会用到。请勿填写账号密码或令牌。",
    },
  };

  let feedbackKind = "bug";
  let feedbackLastPayload = null;
  let feedbackLastFocus = null;

  function feedbackForm() {
    return $("#feedbackForm") || document;
  }

  function setFeedbackStatus(text, kind) {
    const status = $("#feedbackStatus");
    if (!status) return;
    status.textContent = text || "";
    status.classList.remove("ok", "err");
    if (kind) status.classList.add(kind);
  }

  function feedbackVersion() {
    return (manifest && manifest.version) || FEEDBACK_VERSION_FALLBACK;
  }

  function applyFeedbackKind(kind) {
    feedbackKind = KIND_LABELS[kind] ? kind : "bug";
    const copy = FEEDBACK_COPY[feedbackKind] || FEEDBACK_COPY.bug;
    setText("#feedbackKicker", copy.kicker);
    setText("#feedbackTitle", copy.title);
    setText("#feedbackLede", copy.lede);
    const form = feedbackForm();
    if (form.elements && form.elements.type) form.elements.type.value = feedbackKind;
    if (form.elements && form.elements.title) form.elements.title.placeholder = copy.titlePlaceholder;
    if (form.elements && form.elements.description) form.elements.description.placeholder = copy.descriptionPlaceholder;
    const button = $("#feedbackSubmit");
    if (button && button.dataset.busy !== "1") button.textContent = copy.button;
    const fallback = $("#feedbackFallback");
    if (fallback) fallback.hidden = true;
  }

  function openFeedback(kind) {
    const modal = $("#feedbackModal");
    if (!modal) return;
    feedbackLastFocus = document.activeElement;
    applyFeedbackKind(kind || "bug");
    modal.hidden = false;
    document.body.classList.add("modal-open");
    const input = $('input[name="version"]', feedbackForm());
    if (input) input.value = feedbackVersion();
    setFeedbackStatus("", "");
    window.setTimeout(() => {
      const title = $('input[name="title"]', feedbackForm());
      if (title) title.focus();
    }, 60);
  }

  function closeFeedback() {
    const modal = $("#feedbackModal");
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    if (feedbackLastFocus && typeof feedbackLastFocus.focus === "function") {
      feedbackLastFocus.focus();
    }
    feedbackLastFocus = null;
  }

  function readFeedback(form) {
    const data = new FormData(form);
    const payload = {};
    ["type", "version", "title", "description", "contact", "website"].forEach((key) => {
      payload[key] = String(data.get(key) || "").trim();
    });
    payload.source = "website";
    payload.page = window.location.href;
    payload.user_agent = navigator.userAgent;
    return payload;
  }

  function feedbackKindLabel(type) {
    return KIND_LABELS[type] || "其他";
  }

  function feedbackText(payload) {
    return [
      "Darkitten Launcher " + feedbackKindLabel(payload.type) + "反馈",
      "版本: " + payload.version,
      "类型: " + feedbackKindLabel(payload.type),
      "标题: " + payload.title,
      "联系方式: " + (payload.contact || "未填写"),
      "页面: " + payload.page,
      "时间: " + new Date().toLocaleString("zh-CN"),
      "User-Agent: " + payload.user_agent,
      "",
      "详细描述:",
      payload.description,
    ].join("\n");
  }

  function githubIssueUrl(payload) {
    const body = [
      "**来源**：网站",
      "**类型**：" + feedbackKindLabel(payload.type),
      "**启动器版本**：" + payload.version,
      payload.contact ? "**联系方式**：" + payload.contact : "",
      payload.page ? "**页面**：" + payload.page : "",
      "",
      "---",
      "",
      payload.description,
      "",
      "> 这条反馈通过 DKL 下载站提交，开发者可在网站反馈表查看。",
    ].filter((line) => line !== "").join("\n").slice(0, 7000);
    const params = new URLSearchParams({
      title: "[DKL " + feedbackKindLabel(payload.type) + "] " + payload.title,
      body: body,
    });
    return GITHUB_NEW_ISSUE + "?" + params.toString();
  }

  function mailtoUrl(payload) {
    const text = feedbackText(payload);
    const body = text.length > 1600 ? text.slice(0, 1600) + "\n\n（完整内容已复制到剪贴板）" : text;
    return "mailto:" + FEEDBACK_EMAIL +
      "?subject=" + encodeURIComponent("[DKL " + feedbackKindLabel(payload.type) + "] " + payload.title) +
      "&body=" + encodeURIComponent(body);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (copyError) { ok = false; }
      area.remove();
      return ok;
    }
  }

  function prepareFallback(payload) {
    feedbackLastPayload = payload;
    const fallback = $("#feedbackFallback");
    if (!fallback) return;
    const github = $("#feedbackGithub");
    if (github) github.href = githubIssueUrl(payload);
    const mail = $("#feedbackMail");
    if (mail) mail.href = mailtoUrl(payload);
    fallback.hidden = false;
  }

  function submitFeedback(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("#feedbackSubmit");
    const payload = readFeedback(form);
    const titleInput = form.elements.title;
    const descriptionInput = form.elements.description;
    titleInput.classList.toggle("invalid", payload.title.length < 2);
    descriptionInput.classList.toggle("invalid", payload.description.length < 10);
    if (payload.title.length < 2 || payload.description.length < 10) {
      setFeedbackStatus("请填写至少 2 个字的标题和 10 个字的描述。", "err");
      return;
    }
    if (payload.website) {
      setFeedbackStatus("反馈已收到，谢谢。", "ok");
      form.reset();
      return;
    }

    prepareFallback(payload);
    const url = githubIssueUrl(payload);
    window.open(url, "_blank", "noopener");
    setFeedbackStatus(
      "已打开 GitHub 新建 Issue 页面，登录后点击 Submit new issue 即可提交。"
      + "如果浏览器拦截了弹窗，请点下面的 GitHub 按钮，或改用邮件发送。",
      "ok"
    );
    if (button) {
      button.textContent = "已打开 GitHub";
      window.setTimeout(() => {
        button.textContent = (FEEDBACK_COPY[feedbackKind] || FEEDBACK_COPY.bug).button;
      }, 2400);
    }
  }

  function prefillFeedbackFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const type = String(params.get("type") || "").toLowerCase();
    const title = String(params.get("title") || "");
    const description = String(params.get("description") || "");
    if (!type && !title && !description) return false;
    openFeedback(KIND_LABELS[type] ? type : "bug");
    const form = feedbackForm();
    if (!form.elements) return true;
    if (form.elements.type && KIND_LABELS[type]) {
      form.elements.type.value = type;
      applyFeedbackKind(type);
    }
    if (form.elements.version && params.get("version")) {
      form.elements.version.value = String(params.get("version") || "");
      form.elements.version.dataset.touched = "1";
    }
    if (form.elements.title) form.elements.title.value = title;
    if (form.elements.description) form.elements.description.value = description;
    if (form.elements.contact) form.elements.contact.value = String(params.get("contact") || "");
    return true;
  }

  function bindFeedback() {
    $$("[data-feedback-kind]").forEach((button) => {
      button.addEventListener("click", () => {
        openFeedback(button.dataset.feedbackKind || "bug");
      });
    });
    $$("[data-feedback-close]").forEach((button) => {
      button.addEventListener("click", closeFeedback);
    });
    const form = $("#feedbackForm");
    if (form) {
      form.addEventListener("submit", submitFeedback);
      form.addEventListener("input", (event) => {
        if (event.target && event.target.classList) event.target.classList.remove("invalid");
      });
    }
    const copyButton = $("#feedbackCopy");
    if (copyButton) {
      copyButton.addEventListener("click", async () => {
        const payload = feedbackLastPayload || (form ? readFeedback(form) : null);
        if (!payload) return;
        const copied = await copyText(feedbackText(payload));
        setFeedbackStatus(
          copied ? "反馈内容已复制到剪贴板。" : "复制失败，请手动选择文本。",
          copied ? "ok" : "err"
        );
      });
    }
    const modal = $("#feedbackModal");
    if (modal) {
      modal.addEventListener("click", (event) => {
        if (event.target === modal) closeFeedback();
      });
    }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeFeedback();
    });
  }

  function openFeedbackFromHash() {
    const hash = String(window.location.hash || "").toLowerCase();
    if (hash === "#feedback-bug") openFeedback("bug");
    if (hash === "#feedback-suggestion") openFeedback("suggestion");
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindHeader();
    bindReveal();
    bindCopy();
    bindParallax();
    bindFeedback();
    loadManifest();
    if (!prefillFeedbackFromQuery()) openFeedbackFromHash();
    window.addEventListener("hashchange", openFeedbackFromHash);
  });
})();
