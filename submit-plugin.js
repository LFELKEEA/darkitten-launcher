(function () {
  "use strict";

  const MAX_ZIP = 6 * 1024 * 1024;
  const MAX_UNCOMPRESSED = 30 * 1024 * 1024;
  const MAX_ENTRIES = 300;
  const MAX_MANIFEST = 64 * 1024;
  const MAX_SETTINGS = 8 * 1024;
  const MAX_FILE = 8 * 1024 * 1024;
  const MAX_DESCRIPTION = 4000;
  const MAX_NAME = 80;
  const MAX_AUTHOR = 80;

  const ID_RE = /^[a-z0-9][a-z0-9_.-]{1,63}$/;
  const VERSION_RE = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]{1,32})?$/;
  const RESERVED_PREFIXES = ["lfelkeea.", "example."];
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
  const DANGEROUS_PARTS = new Set(["__pycache__", ".git", ".idea", ".vscode", "node_modules", "data"]);
  const DANGEROUS_NAMES = new Set([
    ".env", ".env.local", "config.json", "social.json", "credentials.json",
    "secrets.json", "id_rsa", "id_ed25519"
  ]);
  const DANGEROUS_SUFFIXES = new Set([
    ".pyc", ".pyo", ".log", ".pem", ".key", ".p12", ".pfx",
    ".exe", ".dll", ".so", ".dylib", ".msi", ".scr", ".com",
    ".bat", ".cmd", ".ps1", ".sh", ".jar"
  ]);

  const $ = (selector, root = document) => root.querySelector(selector);
  const dropZone = $("#dropZone");
  const fileInput = $("#zipFile");
  const pickFile = $("#pickFile");
  const clearFile = $("#clearFile");
  const fileSummary = $("#fileSummary");
  const formErrors = $("#formErrors");
  const formWarnings = $("#formWarnings");
  const formStatus = $("#formStatus");
  const submitButton = $("#createIssue");
  const agree = $("#agree");
  const notes = $("#notes");
  const handoff = $("#handoff");
  const openIssue = $("#openIssue");
  const copyBody = $("#copyBody");
  const issueBody = $("#issueBody");
  const handoffFileName = $("#handoffFileName");

  const state = {
    file: null,
    plugin: null,
    sha256: "",
    size: 0,
    body: "",
    busy: false
  };

  function text(node, value) {
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (!value) return "—";
    if (value >= 1024 * 1024) return (value / 1024 / 1024).toFixed(2) + " MB";
    if (value >= 1024) return (value / 1024).toFixed(1) + " KB";
    return value + " B";
  }

  function showErrors(errors) {
    formErrors.innerHTML = "";
    if (!errors || !errors.length) {
      formErrors.hidden = true;
      return;
    }
    const title = document.createElement("strong");
    title.textContent = "这个插件包还不能提交：";
    formErrors.appendChild(title);
    const list = document.createElement("ul");
    errors.forEach((message) => {
      const item = document.createElement("li");
      item.textContent = message;
      list.appendChild(item);
    });
    formErrors.appendChild(list);
    formErrors.hidden = false;
  }

  function showWarnings(warnings) {
    formWarnings.innerHTML = "";
    if (!warnings || !warnings.length) {
      formWarnings.hidden = true;
      return;
    }
    const title = document.createElement("strong");
    title.textContent = "可以提交，但请留意：";
    formWarnings.appendChild(title);
    const list = document.createElement("ul");
    warnings.forEach((message) => {
      const item = document.createElement("li");
      item.textContent = message;
      list.appendChild(item);
    });
    formWarnings.appendChild(list);
    formWarnings.hidden = false;
  }

  function updateSubmitState() {
    submitButton.disabled = !(state.plugin && !state.busy && agree.checked);
  }

  function setBusy(value) {
    state.busy = Boolean(value);
    updateSubmitState();
  }

  function resetFile() {
    state.file = null;
    state.plugin = null;
    state.sha256 = "";
    state.size = 0;
    state.body = "";
    fileInput.value = "";
    fileSummary.hidden = true;
    handoff.hidden = true;
    showErrors([]);
    showWarnings([]);
    text(formStatus, "");
    agree.checked = false;
    updateSubmitState();
  }

  function readU16(view, offset) {
    return view.getUint16(offset, true);
  }

  function readU32(view, offset) {
    return view.getUint32(offset, true);
  }

  function decodeText(bytes) {
    return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  }

  function pathError(name) {
    if (!name) return "ZIP 里有空文件名。";
    if (name.includes("\0")) return "文件名包含空字符。";
    if (name.includes("\\")) return "文件名不能使用反斜杠：" + name;
    if (name.startsWith("/") || /^[A-Za-z]:/.test(name)) return "文件名不能是绝对路径：" + name;
    const parts = name.split("/");
    if (parts.some((part) => part === "" || part === "." || part === "..")) {
      return "文件名包含空段、`.` 或 `..`：" + name;
    }
    if (parts.some((part) => part.includes(":"))) return "文件名不能包含冒号：" + name;
    if (parts.some((part) => part.endsWith(" ") || part.endsWith("."))) {
      return "文件名不能以空格或点结尾：" + name;
    }
    return "";
  }

  function checkDangerous(name) {
    const parts = name.split("/");
    const lowered = parts.map((part) => part.toLowerCase());
    if (lowered.some((part) => DANGEROUS_PARTS.has(part))) {
      return "不能包含运行数据或开发缓存：" + name;
    }
    const base = lowered[lowered.length - 1];
    if (DANGEROUS_NAMES.has(base)) return "不能包含可能带密钥的配置文件：" + name;
    const dot = base.lastIndexOf(".");
    const suffix = dot >= 0 ? base.slice(dot) : "";
    if (DANGEROUS_SUFFIXES.has(suffix)) return "不能包含可执行文件、脚本或密钥：" + name;
    return "";
  }

  async function inflateRaw(bytes) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("当前浏览器不支持 ZIP 解压，请换最新版 Chrome、Edge 或 Firefox。");
    }
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    const output = await new Response(stream).arrayBuffer();
    return new Uint8Array(output);
  }

  function findEntry(entries, name) {
    return entries.find((entry) => entry.name === name) || null;
  }

  async function extractEntry(bytes, view, entry) {
    const offset = entry.localOffset;
    if (offset + 30 > bytes.length || readU32(view, offset) !== 0x04034b50) {
      throw new Error("ZIP 的本地文件头损坏：" + entry.name);
    }
    const nameLength = readU16(view, offset + 26);
    const extraLength = readU16(view, offset + 28);
    const localName = decodeText(bytes.slice(offset + 30, offset + 30 + nameLength));
    if (localName !== entry.name) {
      throw new Error("ZIP 的本地文件名和目录不一致：" + entry.name);
    }
    const dataStart = offset + 30 + nameLength + extraLength;
    const dataEnd = dataStart + entry.compressedSize;
    if (dataEnd > bytes.length) throw new Error("ZIP 文件数据越界：" + entry.name);
    const raw = bytes.slice(dataStart, dataEnd);
    let output;
    if (entry.method === 0) {
      output = raw;
    } else if (entry.method === 8) {
      output = await inflateRaw(raw);
    } else {
      throw new Error("只支持 stored/deflate 压缩：" + entry.name);
    }
    if (entry.uncompressedSize && output.length !== entry.uncompressedSize) {
      throw new Error("解压后大小不一致：" + entry.name);
    }
    return output;
  }

  async function parseZip(buffer) {
    const bytes = new Uint8Array(buffer);
    if (bytes.length < 22) throw new Error("ZIP 太小，不像一个完整的插件包。");
    if (!(bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x03 && bytes[3] === 0x04)) {
      throw new Error("文件不是 ZIP（缺少 PK 文件头）。");
    }
    const view = new DataView(buffer);
    let eocd = -1;
    const minOffset = Math.max(0, bytes.length - 0xffff - 22);
    for (let i = bytes.length - 22; i >= minOffset; i -= 1) {
      if (readU32(view, i) === 0x06054b50) {
        eocd = i;
        break;
      }
    }
    if (eocd < 0) throw new Error("找不到 ZIP 的中央目录结尾。");
    const disk = readU16(view, eocd + 4);
    const cdDisk = readU16(view, eocd + 6);
    const entriesOnDisk = readU16(view, eocd + 8);
    const totalEntries = readU16(view, eocd + 10);
    const cdSize = readU32(view, eocd + 12);
    const cdOffset = readU32(view, eocd + 16);
    if (disk !== 0 || cdDisk !== 0 || entriesOnDisk !== totalEntries) {
      throw new Error("不支持分卷 ZIP。");
    }
    if (totalEntries === 0xffff || cdSize === 0xffffffff || cdOffset === 0xffffffff) {
      throw new Error("不支持 ZIP64，请用普通 ZIP 重新打包。");
    }
    if (!totalEntries) throw new Error("ZIP 里没有文件。");
    if (totalEntries > MAX_ENTRIES) throw new Error("ZIP 最多 " + MAX_ENTRIES + " 个条目。");
    if (cdOffset + cdSize > eocd) throw new Error("ZIP 中央目录位置异常。");

    let pointer = cdOffset;
    const entries = [];
    const names = [];
    let total = 0;
    for (let index = 0; index < totalEntries; index += 1) {
      if (pointer + 46 > bytes.length || readU32(view, pointer) !== 0x02014b50) {
        throw new Error("ZIP 中央目录损坏。");
      }
      const flags = readU16(view, pointer + 8);
      const method = readU16(view, pointer + 10);
      const compressedSize = readU32(view, pointer + 20);
      const uncompressedSize = readU32(view, pointer + 24);
      const nameLength = readU16(view, pointer + 28);
      const extraLength = readU16(view, pointer + 30);
      const commentLength = readU16(view, pointer + 32);
      const externalAttr = readU32(view, pointer + 38);
      const localOffset = readU32(view, pointer + 42);
      const nameStart = pointer + 46;
      const nameEnd = nameStart + nameLength;
      if (nameEnd > bytes.length) throw new Error("ZIP 文件名越界。");
      const name = decodeText(bytes.slice(nameStart, nameEnd));
      if (name.includes("\ufffd")) throw new Error("ZIP 文件名不是 UTF-8：" + name);
      const isDir = name.endsWith("/");
      const error = pathError(isDir ? name.slice(0, -1) : name);
      if (error) throw new Error(error);
      if (flags & 0x1) throw new Error("ZIP 不能包含加密文件：" + name);
      if (compressedSize === 0xffffffff || uncompressedSize === 0xffffffff) {
        throw new Error("不支持 ZIP64：" + name);
      }
      if (!isDir) {
        if (method !== 0 && method !== 8) throw new Error("只支持 stored/deflate 压缩：" + name);
        if (((externalAttr >>> 16) & 0xf000) === 0xa000) {
          throw new Error("ZIP 不能包含符号链接：" + name);
        }
        const danger = checkDangerous(name);
        if (danger) throw new Error(danger);
        if (uncompressedSize > MAX_FILE) {
          throw new Error("单个文件不能超过 8 MB：" + name);
        }
        if (names.includes(name)) throw new Error("ZIP 里有重名文件：" + name);
        total += uncompressedSize;
        if (total > MAX_UNCOMPRESSED) throw new Error("ZIP 解压后总大小超过 30 MB。");
        names.push(name);
      }
      entries.push({
        name,
        isDir,
        flags,
        method,
        compressedSize,
        uncompressedSize,
        localOffset
      });
      pointer = nameEnd + extraLength + commentLength;
    }
    if (pointer !== cdOffset + cdSize) throw new Error("ZIP 中央目录长度不一致。");
    if (!names.length) throw new Error("ZIP 里没有文件。");

    const roots = Array.from(new Set(names.map((name) => name.split("/")[0])));
    if (roots.length !== 1) throw new Error("ZIP 里必须只有一个顶层插件文件夹。");
    const root = roots[0];
    if (names.some((name) => !name.startsWith(root + "/"))) {
      throw new Error("所有文件都必须放在插件 id 文件夹里。");
    }

    const manifestName = root + "/plugin.json";
    const manifestEntry = findEntry(entries, manifestName);
    if (!manifestEntry) throw new Error("缺少 " + manifestName);
    if (manifestEntry.uncompressedSize > MAX_MANIFEST) throw new Error("plugin.json 不能超过 64 KB。");
    const manifestBytes = await extractEntry(bytes, view, manifestEntry);
    if (manifestBytes.length > MAX_MANIFEST) throw new Error("plugin.json 不能超过 64 KB。");
    let manifest;
    try {
      manifest = JSON.parse(decodeText(manifestBytes).replace(/^\ufeff/, ""));
    } catch (error) {
      throw new Error("plugin.json 不是合法的 JSON：" + error.message);
    }
    if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
      throw new Error("plugin.json 顶层必须是对象。");
    }

    const issues = [];
    const warnings = [];
    const id = String(manifest.id || "").trim();
    if (!ID_RE.test(id)) issues.push("id 只能使用小写字母、数字、点、下划线、短横线，长度 2-64。");
    if (id !== root) issues.push("ZIP 根目录名必须等于插件 id：" + (id || "未知"));
    if (RESERVED_PREFIXES.some((prefix) => id.startsWith(prefix))) {
      issues.push("lfelkeea. 和 example. 前缀由维护者保留，投稿请换一个 id。");
    }
    const version = String(manifest.version || "").trim();
    if (!VERSION_RE.test(version)) issues.push("version 必须是 1.0.0 这样的语义化版本。");
    const apiVersion = Number(manifest.api_version == null ? 1 : manifest.api_version);
    if (!Number.isInteger(apiVersion) || apiVersion !== 1) issues.push("api_version 必须是 1。");
    const entry = String(manifest.entry || "main.py").trim() || "main.py";
    const entryError = pathError(entry);
    if (entryError) issues.push("entry 不合法：" + entryError);
    else if (!entry.toLowerCase().endsWith(".py")) issues.push("entry 必须是 Python 文件（.py）。");
    else if (!names.includes(root + "/" + entry)) issues.push("找不到入口文件：" + entry);

    const name = String(manifest.name || "").trim().slice(0, MAX_NAME);
    if (!name) issues.push("name 不能为空。");
    const description = String(manifest.description || "").trim().slice(0, MAX_DESCRIPTION);
    const author = String(manifest.author || "").trim().slice(0, MAX_AUTHOR);

    const permissionList = [];
    if (manifest.permissions != null && !Array.isArray(manifest.permissions)) {
      issues.push("permissions 必须是数组。");
    } else {
      (manifest.permissions || []).forEach((item, index) => {
        const permission = String(item || "").trim();
        if (!permission) return;
        if (!Object.prototype.hasOwnProperty.call(PERMISSIONS, permission)) {
          issues.push("permissions 第 " + (index + 1) + " 项不是 v1 已知权限：" + permission);
        }
        if (permissionList.includes(permission)) issues.push("permissions 重复：" + permission);
        permissionList.push(permission);
      });
    }

    const settings = manifest.settings == null ? {} : manifest.settings;
    if (!settings || typeof settings !== "object" || Array.isArray(settings)) {
      issues.push("settings 必须是对象。");
    } else if (new TextEncoder().encode(JSON.stringify(settings)).length > MAX_SETTINGS) {
      issues.push("settings 不能超过 8 KB。");
    }

    const requires = [];
    if (manifest.requires != null) {
      if (!Array.isArray(manifest.requires)) {
        issues.push("requires 必须是数组。");
      } else if (manifest.requires.length > 10) {
        issues.push("requires 最多 10 项。");
      } else {
        manifest.requires.forEach((item, index) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) {
            issues.push("requires 第 " + (index + 1) + " 项必须是对象。");
            return;
          }
          const itemName = String(item.name || "").trim().slice(0, 60);
          if (!itemName) issues.push("requires 第 " + (index + 1) + " 项缺少 name。");
          const itemVersion = String(item.version || "").trim().slice(0, 40);
          const itemUrl = String(item.url || "").trim().slice(0, 300);
          const itemNote = String(item.note || "").trim().slice(0, 200);
          if (itemUrl && !/^https?:\/\//i.test(itemUrl)) {
            issues.push("requires 第 " + (index + 1) + " 项的 url 只能是 http(s)。");
          }
          requires.push({ name: itemName, version: itemVersion, url: itemUrl, note: itemNote });
        });
      }
    }

    const highlights = [];
    if (manifest.highlights != null) {
      if (!Array.isArray(manifest.highlights)) {
        issues.push("highlights 必须是数组。");
      } else if (manifest.highlights.length > 10) {
        issues.push("highlights 最多 10 项。");
      } else {
        manifest.highlights.forEach((item, index) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) {
            issues.push("highlights 第 " + (index + 1) + " 项必须是对象。");
            return;
          }
          const label = String(item.label || "").trim().slice(0, 40);
          const value = String(item.value || "").trim().slice(0, 80);
          if (label || value) highlights.push({ label: label || "说明", value });
        });
      }
    }

    if (!permissionList.length) warnings.push("插件没有声明任何权限，运行时只能使用 api.log()。");
    if (issues.length) {
      const error = new Error("插件清单没有通过检查。");
      error.issues = issues;
      error.warnings = warnings;
      throw error;
    }

    return {
      manifest,
      id,
      name: name || id,
      version,
      author,
      description,
      permissions: permissionList,
      requires,
      highlights,
      settings,
      entry,
      files: names
    };
  }

  async function sha256(buffer) {
    const digest = await crypto.subtle.digest("SHA-256", buffer);
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }

  function renderSummary(plugin) {
    text($("#pluginName"), plugin.name);
    text($("#pluginId"), plugin.id);
    text($("#pluginDescription"), plugin.description || "（没有填写描述）");
    text($("#pluginVersion"), "v" + plugin.version);
    text($("#pluginAuthor"), plugin.author || "未署名");
    text($("#pluginSize"), formatBytes(state.size));
    text($("#pluginFiles"), plugin.files.length + " 个文件");
    text($("#pluginSha"), state.sha256);

    const permRoot = $("#pluginPerms");
    permRoot.innerHTML = "";
    const label = document.createElement("span");
    label.className = "submit-perms-label";
    label.textContent = "权限";
    permRoot.appendChild(label);
    if (plugin.permissions.length) {
      plugin.permissions.forEach((permission) => {
        const chip = document.createElement("span");
        chip.className = "submit-perm";
        chip.title = permission;
        chip.textContent = PERMISSIONS[permission] || permission;
        permRoot.appendChild(chip);
      });
    } else {
      const chip = document.createElement("span");
      chip.className = "submit-perm submit-perm-empty";
      chip.textContent = "无特殊权限";
      permRoot.appendChild(chip);
    }
    if (plugin.requires.length) {
      const row = document.createElement("p");
      row.className = "submit-requires";
      row.textContent = "外部依赖：" + plugin.requires.map((item) => {
        return item.name + (item.version ? " " + item.version : "");
      }).join("；");
      permRoot.appendChild(row);
    }
    fileSummary.hidden = false;
  }

  async function handleFile(file) {
    if (!file) return;
    resetFile();
    if (!/\.zip$/i.test(file.name)) {
      showErrors(["请选择 .zip 文件。"]);
      return;
    }
    if (file.size > MAX_ZIP) {
      showErrors(["ZIP 不能超过 6 MB，当前 " + formatBytes(file.size) + "。"]);
      return;
    }
    setBusy(true);
    text(formStatus, "正在读取并检查插件包…");
    try {
      const buffer = await file.arrayBuffer();
      const plugin = await parseZip(buffer);
      const digest = await sha256(buffer);
      state.file = file;
      state.plugin = plugin;
      state.sha256 = digest;
      state.size = buffer.byteLength;
      renderSummary(plugin);
      showErrors([]);
      showWarnings([]);
      text(formStatus, "检查通过。确认下面的权限和说明后，点「生成投稿 Issue」。");
      handoffFileName.textContent = file.name;
    } catch (error) {
      state.plugin = null;
      const messages = error && Array.isArray(error.issues) ? error.issues : [error.message || String(error)];
      showErrors(messages);
      if (error && Array.isArray(error.warnings)) showWarnings(error.warnings);
      text(formStatus, "检查没有通过，请按上面的提示修改后重新选择。");
      fileSummary.hidden = true;
    } finally {
      setBusy(false);
      updateSubmitState();
    }
  }

  function buildMetadata(plugin) {
    return {
      schema: 1,
      id: plugin.id,
      name: plugin.name,
      version: plugin.version,
      author: plugin.author,
      description: plugin.description,
      permissions: plugin.permissions,
      requires: plugin.requires,
      sha256: state.sha256,
      size: state.size,
      archive_name: state.file ? state.file.name : "",
      notes: notes.value.trim().slice(0, 2000),
      submitted_at: new Date().toISOString()
    };
  }

  function buildIssueBody(plugin) {
    const meta = buildMetadata(plugin);
    const noteText = meta.notes || "（未填写）";
    return [
      "<!-- dkl-plugin-submission:v1 -->",
      "",
      "### 插件投稿",
      "",
      "感谢投稿。请把 ZIP 拖到下面「插件包」区域，等 GitHub 上传完成后再提交。",
      "",
      "```json",
      JSON.stringify(meta, null, 2),
      "```",
      "",
      "### 插件包",
      "<!-- 把 ZIP 拖到这一行下面，等上传完成 -->",
      "",
      "### 投稿说明",
      noteText,
      "",
      "### 投稿人确认",
      "- [x] 这是我自己制作或有权分发的插件",
      "- [x] 插件只使用上面声明的权限",
      "- [x] 我理解审核通过后插件会公开下载",
      ""
    ].join("\n");
  }

  function issueUrl(plugin, body) {
    const title = "[插件投稿] " + plugin.name + " v" + plugin.version + " (" + plugin.id + ")";
    const base = "https://github.com/LFELKEEA/darkitten-launcher/issues/new";
    const titlePart = "labels=plugin-submission&title=" + encodeURIComponent(title);
    const bodyPart = encodeURIComponent(body);
    if (bodyPart.length <= 6000) return base + "?" + titlePart + "&body=" + bodyPart;
    return base + "?" + titlePart;
  }

  async function copyText(value) {
    if (!value) return false;
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch (error) {
      issueBody.focus();
      issueBody.select();
      try {
        return document.execCommand("copy");
      } catch (fallbackError) {
        return false;
      }
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    if (!state.plugin || !state.file || !agree.checked || state.busy) return;
    const body = buildIssueBody(state.plugin);
    state.body = body;
    issueBody.value = body;
    const url = issueUrl(state.plugin, body);
    openIssue.href = url;
    handoff.hidden = false;
    handoff.scrollIntoView({ behavior: "smooth", block: "start" });
    // 先同步打开新窗口，避免 await 剪贴板后触发浏览器的弹窗拦截。
    window.open(url, "_blank", "noopener");
    const copied = await copyText(body);
    if (copied) {
      text(formStatus, "投稿正文已复制。打开 GitHub 页面后直接粘贴，再把 ZIP 拖进正文。");
    } else {
      text(formStatus, "请手动复制下面的 Issue 正文，粘贴到 GitHub 页面。");
    }
  }

  dropZone.addEventListener("click", (event) => {
    if (event.target.closest("button")) return;
    fileInput.click();
  });
  pickFile.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    if (file) handleFile(file);
  });
  clearFile.addEventListener("click", resetFile);
  agree.addEventListener("change", updateSubmitState);
  notes.addEventListener("input", () => {
    if (state.plugin) text(formStatus, "说明已记录。修改说明后重新点「生成投稿 Issue」即可更新正文。");
  });
  $("#submitForm").addEventListener("submit", onSubmit);
  copyBody.addEventListener("click", async () => {
    if (!state.body) return;
    const copied = await copyText(state.body);
    text(formStatus, copied ? "正文已复制。" : "复制失败，请手动选择文本框内容。");
  });

  ["dragenter", "dragover"].forEach((name) => {
    dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.remove("is-dragover");
    });
  });
  dropZone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) handleFile(file);
  });

  updateSubmitState();
})();
