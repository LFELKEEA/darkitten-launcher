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
  let feedbackLastFocus = null;

  function setFeedbackStatus(text, kind) {
    const status = $("#feedbackStatus");
    if (!status) return;
    status.textContent = text || "";
    status.classList.remove("ok", "err");
    if (kind) status.classList.add(kind);
  }

  function feedbackVersion() {
    return (manifest && manifest.version) || "1.9.19";
  }

  function openFeedback() {
    const modal = $("#feedbackModal");
    if (!modal) return;
    feedbackLastFocus = document.activeElement;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    const input = $('input[name="version"]', $("#feedbackForm") || document);
    if (input) input.value = feedbackVersion();
    setFeedbackStatus("", "");
    window.setTimeout(() => {
      const title = $('input[name="title"]', $("#feedbackForm") || document);
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
    payload.page = window.location.href;
    payload.user_agent = navigator.userAgent;
    return payload;
  }

  function feedbackText(payload) {
    return [
      "Darkitten Launcher Bug 反馈",
      "版本: " + payload.version,
      "类型: " + payload.type,
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

  async function submitFeedback(event) {
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
    if (button) {
      button.disabled = true;
      button.textContent = "正在提交…";
    }
    setFeedbackStatus("正在提交…", "");

    let useEmailFallback = false;
    try {
      const response = await fetch("api/bug-report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      let data = {};
      try { data = await response.json(); } catch (error) { data = {}; }
      if (response.ok && data.ok) {
        setFeedbackStatus("猫猫已收到反馈，编号 " + (data.id || "已记录") + "。", "ok");
        form.reset();
        const versionInput = form.elements.version;
        if (versionInput) versionInput.value = feedbackVersion();
        if (button) button.textContent = "已提交";
        window.setTimeout(() => { if (button) button.textContent = "提交反馈"; }, 2200);
        return;
      }
      if (response.status === 400 || response.status === 413 || response.status === 429) {
        setFeedbackStatus(data.error || "提交失败，请稍后再试。", "err");
        return;
      }
      useEmailFallback = true;
    } catch (error) {
      useEmailFallback = true;
    } finally {
      if (button) {
        button.disabled = false;
        if (button.textContent === "正在提交…") button.textContent = "提交反馈";
      }
    }

    if (useEmailFallback) {
      const text = feedbackText(payload);
      const copied = await copyText(text);
      const body = text.length > 1500 ? text.slice(0, 1500) + "\n\n（完整内容已复制到剪贴板）" : text;
      const mailto =
        "mailto:" + FEEDBACK_EMAIL +
        "?subject=" + encodeURIComponent("[DKL Bug] " + payload.title) +
        "&body=" + encodeURIComponent(body);
      const link = document.createElement("a");
      link.href = mailto;
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setFeedbackStatus(
        "公开站点已改用邮件反馈。已打开邮件草稿；如果没弹出，内容" +
        (copied ? "已复制，可发送至 " : "可手动发送至 ") + FEEDBACK_EMAIL + "。",
        copied ? "ok" : "err"
      );
    }
  }

  function bindFeedback() {
    $$("[data-feedback-open]").forEach((button) => {
      button.addEventListener("click", openFeedback);
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

  document.addEventListener("DOMContentLoaded", () => {
    bindHeader();
    bindReveal();
    bindCopy();
    bindParallax();
    bindFeedback();
    loadManifest();
  });
})();
