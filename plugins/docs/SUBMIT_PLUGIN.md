# 插件投稿与审核说明

这份文档写给想把自己的桌宠插件放进官方插件库的人。投稿是**人工审核制**：投稿不等于上架，审核通过后才会公开。

- 投稿入口：<https://lfelkeea.github.io/darkitten-launcher/submit-plugin.html>
- 审核队列：<https://lfelkeea.github.io/darkitten-launcher/plugin-review.html>
- 插件开发文档：[PLUGIN_DEV.md](PLUGIN_DEV.md)
- 投稿 Issue 列表：<https://github.com/LFELKEEA/darkitten-launcher/issues?q=label%3Aplugin-submission>

---

## 1. 投稿前准备

1. 按 [PLUGIN_DEV.md](PLUGIN_DEV.md) 完成插件，确认能在桌宠里正常启用。
2. 插件根目录名必须等于 `plugin.json` 里的 `id`，例如：

```text
my_plugin/
├─ plugin.json
├─ main.py
└─ README.md
```

3. 打包时只压缩这个文件夹本身，不要多套一层目录，也不要带运行数据：

```text
my_plugin-1.0.0.zip
└─ my_plugin/
   ├─ plugin.json
   └─ main.py
```

4. 不要包含以下内容：
   - `data/`、`__pycache__/`、`.git/`、`node_modules/`、日志和缓存；
   - `config.json`、`social.json`、`.env`、密钥、证书、账号密码；
   - `.exe`、`.dll`、`.bat`、`.cmd`、`.ps1`、`.sh`、`.jar` 等可执行文件或脚本；
   - 你无权分发的第三方代码、素材或闭源程序。

5. 单个 ZIP 不超过 **6 MB**，解压后总大小不超过 **30 MB**，最多 **300** 个条目，`plugin.json` 不超过 **64 KB**。

---

## 2. 投稿流程

```text
选择 ZIP
  ↓ 浏览器本地解析、检查清单和权限（不上传文件）
生成投稿 Issue
  ↓ 把 ZIP 拖进 Issue 附件
Actions 自动校验
  ↓ 打上 plugin-validated / plugin-invalid
维护者阅读代码
  ↓ 评论 /approve 或 /reject 原因
自动发布到插件库
```

### 2.1 在网站提交

打开投稿页，选择 ZIP。页面会在本地完成这些检查：

- ZIP 结构、路径穿越、绝对路径、加密、ZIP64、符号链接；
- 是否只有一个顶层插件文件夹，且文件夹名等于 `id`；
- `plugin.json` 是否存在、是否合法；
- `id`、`version`、`api_version`、`entry`、`permissions`、`settings`、`requires`、`highlights`；
- 是否使用保留前缀 `lfelkeea.` / `example.`；
- 是否包含危险文件名或可执行文件；
- 计算 SHA-256，供 GitHub Actions 再次核对。

检查通过后，页面会生成一个带 `plugin-submission` 标签的 GitHub Issue 正文，并打开新建 Issue 页面。把 ZIP 拖到正文里，等 GitHub 显示上传完成，再提交。

### 2.2 自动校验

Issue 创建或编辑后，GitHub Actions 会：

1. 下载 Issue 里的 ZIP 附件；
2. 用和网站相同的规则重新校验；
3. 对比 ZIP 的 SHA-256 和大小；
4. 在 Issue 里回复校验结果，并打标签：
   - `plugin-validated`：结构检查通过，等待人工审核；
   - `plugin-invalid`：检查失败，按评论里的提示修改后重新投稿。

自动校验**只解析 ZIP，不执行插件代码**。

### 2.3 人工审核

维护者会检查：

- `main.py` 和依赖模块的实际行为；
- 权限是否和代码一致，有没有多要权限；
- 是否读取、上传或记录不该碰的数据；
- 外部依赖是否说明清楚，链接是否指向官方来源；
- 名称、描述、版本号、作者信息是否准确；
- 是否覆盖官方插件、别人的插件 id，或只是空壳 / 重复投稿。

### 2.4 发布

维护者在 Issue 评论：

```text
/approve
```

Actions 会再次校验，确认不会覆盖官方 id、版本必须高于已发布版本，然后把 ZIP、`plugins.json` 和社区记录发布到插件库。发布成功后：

- Issue 打上 `plugin-published` 并关闭；
- 插件出现在[插件库](https://lfelkeea.github.io/darkitten-launcher/plugins.html)；
- 桌宠的插件市场刷新后可以看到。

退回时维护者评论：

```text
/reject 说明具体原因
```

Issue 会打上 `plugin-rejected` 并关闭。修改后可以重新走投稿流程；同一个 id 的新版本必须使用更高的 `version`。

---

## 3. 版本与 id 规则

- `id`：`^[a-z0-9][a-z0-9_.-]{1,63}$`，只能小写字母、数字、点、下划线、短横线，长度 2–64。
- `version`：语义化版本，例如 `1.0.0`、`1.2.3-beta.1`。
- `api_version`：目前必须是 `1`。
- `entry`：插件目录内的 `.py` 文件，默认 `main.py`。
- 保留前缀：`lfelkeea.` 和 `example.`，投稿请换一个 id。
- 官方或内置插件占用的 id 不能被覆盖。
- 同一 id 的新版本必须严格高于已发布版本。

权限只接受以下值：

| 权限 | 含义 |
| --- | --- |
| `pet.say` | 让桌宠说话 |
| `pet.action` | 触发桌宠动作 |
| `ai.chat` | 调用 AI 对话 |
| `screen.capture` | 截取屏幕 |
| `timer` | 创建定时器 |
| `events` | 订阅事件 |
| `settings` | 读写插件设置 |
| `storage` | 读写插件存储 |

---

## 4. 会被直接退回的情况

- 包含可执行文件、脚本、密钥、`config.json`、`social.json` 或 `data/`；
- 使用 `lfelkeea.` / `example.` 保留前缀；
- 覆盖官方插件或他人的插件 id；
- 权限声明和实际行为明显不符；
- 有联网上传、偷拍屏幕、读取账号凭证、下载执行远程代码等行为；
- 没有实际功能、只是空壳、重复投稿或无法正常启用；
- 描述、作者、版本信息与实际不符；
- 侵犯他人版权或违反第三方许可。

---

## 5. 没有 GitHub 账号

可以把以下内容发到 <lfel278623@outlook.com>：

- 插件 ZIP；
- `plugin.json`；
- 使用说明和权限说明；
- 你希望展示的作者名和联系方式。

同样要走审核，通过后由维护者代为发布。

---

## 6. 隐私与责任

- 网站不会保存你选择的 ZIP；文件只在浏览器里读取。
- Issue 里的附件由 GitHub 托管，公开可见，不要上传任何密钥或私人数据。
- 投稿即表示你确认自己制作了插件，或拥有分发它的权利，并同意插件在审核通过后公开下载。
- 插件运行在本机桌宠进程里，权限系统只能约束插件 API，不能约束 Python 本身。请对自己发布的代码负责，也请用户只安装信任的插件。
