# LFELKEEA 桌宠插件开发指南

本文档面向人类开发者和 AI 编程助手。按这里的约定产出的插件，可以直接放进 LFELKEEA 的插件目录运行。

- 插件 API 版本：`1`
- 插件语言：Python 3.10+
- 插件目录：`data/plugins/<插件文件夹>/`
- 默认入口：`main.py`
- 默认状态：**不启用**，必须由用户在「插件管理」里手动勾选

---

## 1. 安全模型（先读这一段）

插件是**运行在桌宠进程里的本机 Python 代码**，不是沙箱。插件能做什么，取决于它声明的权限和它自己写的代码。

因此：

1. 只安装你信任的插件。
2. 插件默认不运行；新插件被发现后显示为「未启用」。
3. 插件调用的每个受控接口都会检查 `plugin.json` 里的 `permissions`，没声明的权限会直接抛错。
4. 插件抛出的异常会被捕获并写入 `data/plugins.log`，不会让桌宠崩溃；但插件仍然可以执行普通的 Python 代码（读写文件、联网等），权限系统只能约束本 API，不能约束 Python 本身。
5. 插件 API **不会**把 `ai.api_key` 暴露给插件。需要 AI 能力时请调用 `api.chat()`，由桌宠统一读取配置。
6. 需要截屏时必须声明 `screen.capture`。不要在插件里偷偷调用系统截屏；也不要把截图、聊天内容、密钥写进日志或上传到未知服务器。

如果你要运行来源不明的插件，请先读它的 `main.py`。

---

## 2. 目录结构

最小插件：

```text
data/plugins/
└─ my_plugin/
   ├─ plugin.json      # 必需，清单
   ├─ main.py          # 必需，入口
   └─ README.md        # 可选，说明
```

入口文件可以拆成多个模块：

```text
my_plugin/
├─ plugin.json
├─ main.py
├─ helpers.py
└─ assets/
   └─ icon.png
```

加载时插件自己的文件夹会被临时加入 `sys.path`，所以 `main.py` 可以直接 `import helpers`。不要把插件文件放到别的插件目录里。

---

## 3. plugin.json

```json
{
  "id": "example.hello",
  "name": "示例：定时问候",
  "version": "1.0.0",
  "api_version": 1,
  "entry": "main.py",
  "description": "每隔一段时间说一句话。",
  "author": "你的名字",
  "permissions": ["pet.say", "timer", "events", "settings"],
  "settings": {
    "interval_minutes": 10,
    "message": ""
  }
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 全局唯一。小写字母、数字、点、下划线、短横线，2–64 位，例如 `com.example.clock`。 |
| `name` | 是 | 显示名称，中文可以。 |
| `version` | 是 | 插件版本，建议语义化版本，如 `1.0.0`。 |
| `api_version` | 是 | 固定为 `1`。不兼容的版本会被拒绝加载。 |
| `entry` | 否 | 入口文件，默认 `main.py`；不能指向插件目录之外。 |
| `description` | 否 | 一句话说明，显示在插件管理里。 |
| `author` | 否 | 作者名。 |
| `permissions` | 是 | 权限数组，见第 6 节。没有声明的接口不能调用。 |
| `settings` | 否 | 默认设置。会与用户保存的设置做浅合并，插件里用 `api.settings` 读取。 |

`settings` 只用于放插件自己的可配置项，不要放 API Key、密码等敏感信息。

---

## 4. 生命周期

```text
发现插件
  └─ 读取 plugin.json，校验 id / api_version / entry
       └─ 用户在插件管理里勾选「启用」
            └─ 导入 entry 模块
                 └─ 调用 setup(api)
                      ├─ 注册事件 api.on(...)
                      ├─ 创建定时器 api.every(...) / api.after(...)
                      └─ 触发 startup 事件
                           └─ 运行中
                                ├─ 定时器 / 事件回调
                                └─ 用户取消启用 / 重载 / 退出
                                     ├─ 触发 shutdown 事件
                                     ├─ 调用可选 teardown()
                                     └─ 停止定时器、取消未完成的 AI 请求
```

入口模块必须是：

```python
def setup(api):
    ...
```

可选：

```python
def teardown():
    ...

def open_settings(api, parent=None):
    """可选：返回一个 QDialog，插件管理里会出现「设置」按钮。"""
    return MySettingsDialog(api, parent)
```

注意：

- `setup(api)` 在桌宠主线程执行，不要在里面做长时间阻塞操作（例如 `time.sleep(10)`、同步 HTTP）。
- 插件可以自带设置界面：入口模块实现 `open_settings(api, parent=None)`，返回一个 `QDialog`（或任意带 `show()` 的窗口）即可，插件管理里会自动出现「设置」按钮。窗口由插件自己创建，必须使用 PySide6；父窗口是插件对话框，关闭插件对话框时窗口会一起销毁。
- 没有设置界面时，需要提示用户就调用 `api.say()`。
- 不要在模块顶层直接调用 `api`，那时 API 还不存在。
- 定时器和事件回调都在主线程执行。长时间计算请自己开线程，但不要从后台线程直接操作 Qt 界面或调用 `api.say()` / `api.capture_screen()`。
- 插件抛异常不会让桌宠崩溃，但会在插件管理里显示「运行出错（已隔离）」，并写入日志。

---

## 5. API 参考

`setup(api)` 里的 `api` 是一个受控对象。所有接口如下。

### 5.1 属性

| 属性 | 说明 |
| --- | --- |
| `api.version` | 当前插件 API 版本，整数 `1`。 |

### 5.2 让桌宠说话

```python
api.say("主人，该喝水了。", duration=5000)
```

- 权限：`pet.say`
- `duration` 是气泡显示毫秒数，会被限制在 1.2–20 秒。
- 文本会压成一行，超过 200 字会被截断。
- 桌宠被用户隐藏时不会显示。

### 5.3 触发动作

```python
api.action("hop")   # hop / spin / stretch / shake / lean / nod
```

- 权限：`pet.action`
- 未知动作会抛错。

### 5.4 调用 AI

```python
def on_reply(reply, error):
    if error:
        api.log(f"AI 失败：{error}")
        return
    api.say(reply[:80])

api.chat("用一句话夸夸主人。", on_reply)
```

- 权限：`ai.chat`
- `api.chat(messages, callback)` 是异步的，立即返回；`callback(reply, error)` 二选一非空。
- `messages` 支持三种形式：
  - 字符串：自动加上桌宠人格 system prompt，作为一条 user 消息；
  - 单个字典：自动加上 system prompt；
  - 消息列表：原样发送，格式与 OpenAI Chat Completions 一致。
- 不要在回调里做长时间阻塞操作。
- 插件不能读取或修改 `api_key`。

需要视觉能力时，可以自己构造 OpenAI 兼容的图片内容块：

```python
api.chat(
    [
        {"role": "system", "content": "你是一个图片描述助手。"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "描述这张图。"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
            ],
        },
    ],
    on_reply,
)
```

是否支持图片取决于用户当前配置的模型。

### 5.5 截屏

```python
frame = api.capture_screen()
if frame is not None:
    data_uri = frame.data_uri()   # data:image/jpeg;base64,...
    print(frame.width, frame.height, frame.title, frame.process)
```

- 权限：`screen.capture`
- 只能在主线程调用。
- 返回 `ScreenContext`，字段：
  - `image_jpeg: bytes`：压缩后的 JPEG；
  - `width` / `height`：像素尺寸；
  - `title` / `process`：前台窗口标题与进程名；
  - `fullscreen` / `sensitive`：是否全屏、是否命中隐私关键词；
  - `ocr_text`：如果本次观察走过 OCR，会有文字；
  - `notes`：插件补充信息；
  - `mode`：本次观察方式。
- `frame.data_uri()` 返回可直接放进 `image_url` 的字符串。
- 不要每次定时器都截屏；截屏有成本，也会触发用户的隐私担忧。只有确实需要画面时才调用。

### 5.6 定时器

```python
api.every(10 * 60 * 1000, lambda: api.say("十分钟到了。"))
api.after(3000, lambda: api.log("三秒后执行一次"))
```

- 权限：`timer`
- `every` 最小间隔 1000ms，最大 24 小时。
- `after` 只执行一次。
- 插件卸载时定时器会自动停止。

### 5.7 订阅事件

```python
def on_screen(context):
    if context.process == "code":
        context.notes.append("主人可能正在写代码")

api.on("screen.context", on_screen)
```

- 权限：`events`
- 回调参数以关键字形式传入，参数名必须和事件表一致。
- 同一个事件可以注册多个回调。

### 5.8 设置

```python
minutes = int(api.settings.get("interval_minutes", 10))
api.save_settings({"interval_minutes": 15})
```

- 权限：`settings`
- `api.settings` 返回当前设置的副本，修改它不会自动保存。
- `save_settings(values)` 会合并进 `data/config.json` 的 `plugins.settings.<id>`。
- 设置变更不会自动重载插件；需要让用户点「重载」，或者在插件里自己处理。

### 5.8.1 插件设置界面

入口模块提供 `open_settings(api, parent=None)` 即可，返回一个 `QDialog`：

```python
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

def open_settings(api, parent=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle("我的插件设置")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("这里放插件自己的配置项"))
    close = QPushButton("关闭")
    close.clicked.connect(dialog.accept)
    layout.addWidget(close)
    return dialog
```

- 只有插件正在运行时才会显示「设置」按钮。
- 每次点击都会调用一次 `open_settings`，同一个插件的同时只保留一个窗口；插件被停用或重载时窗口自动关闭。
- 插件可以在窗口里直接调用 `api.save_settings()` 和 `api.say()`，但所有 Qt 操作都必须在主线程。
- 设置窗口是插件自己的代码，样式和布局自由发挥；建议用 760×640 左右、深色配色，和桌宠保持一致。

### 5.9 存储

```python
count = api.storage.get("count", 0)
api.storage["count"] = count + 1
api.save_storage()
```

- 权限：`storage`
- 数据保存在 `data/plugins/.state/<id>.json`。
- 适合存计数、上次运行时间等小数据；不要存截图或大文件。

### 5.10 日志

```python
api.log("定时器已启动")
```

- 无权限要求。
- 写入 `data/plugins.log`，按大小自动轮转。
- 不要写 API Key、密码、完整聊天记录、截图 base64。

---

## 6. 权限

| 权限 | 允许的接口 |
| --- | --- |
| `pet.say` | `api.say()` |
| `pet.action` | `api.action()` |
| `ai.chat` | `api.chat()` |
| `screen.capture` | `api.capture_screen()` |
| `timer` | `api.every()` / `api.after()` |
| `events` | `api.on()` |
| `settings` | `api.settings` / `api.save_settings()` |
| `storage` | `api.storage` / `api.save_storage()` |

`api.log()` 不需要权限。没有声明的权限调用会抛 `PluginPermissionError`，`setup()` 阶段会直接导致加载失败。

---

## 7. 事件表

| 事件 | 触发时机 | 回调参数 |
| --- | --- | --- |
| `startup` | 插件加载完成、`setup()` 返回后 | 无 |
| `shutdown` | 插件卸载、重载或桌宠退出前 | 无 |
| `settings.saved` | 用户在设置窗口保存后 | 无 |
| `chat.reply` | 聊天框收到 AI 回复后 | `reply: str` |
| `screen.context` | 每次屏幕观察、请求 AI 之前 | `context: ScreenContext` |
| `screen.comment` | 屏幕观察成功说出话之后 | `text: str, context: ScreenContext, mode: str` |
| `pet.pat` | 用户摸桌宠的头 | 无 |
| `pet.poke` | 用户戳桌宠 | 无 |

`screen.context` 是最有用的扩展点：

```python
def annotate(context):
    title = (context.title or "").lower()
    if "bilibili" in title:
        context.notes.append("主人可能在看视频")
    if "密码" in title:
        context.skip = True
        context.skip_reason = "敏感窗口"
```

- 可以向 `context.notes` 追加简短提示，最终会进入 AI 的提示词。
- 设置 `context.skip = True` 可以跳过本次观察；此时不会截屏、不会请求 AI。
- 不要在 `notes` 里放密码、账号、聊天原文等隐私内容。

---

## 8. 完整示例

目录：

```text
data/plugins/example.hello/
├─ plugin.json
├─ main.py
└─ README.md
```

`plugin.json`：

```json
{
  "id": "example.hello",
  "name": "示例：定时问候",
  "version": "1.0.0",
  "api_version": 1,
  "entry": "main.py",
  "description": "每隔一段时间说一句话。",
  "author": "LFELKEEA",
  "permissions": ["pet.say", "timer", "events", "settings"],
  "settings": {
    "interval_minutes": 10,
    "message": ""
  }
}
```

`main.py`：

```python
def setup(api):
    settings = api.settings
    try:
        minutes = max(1, int(settings.get("interval_minutes", 10)))
    except (TypeError, ValueError):
        minutes = 10
    custom = str(settings.get("message", "")).strip()

    def greet():
        api.say(custom or "我还在这儿哦，主人。")

    api.every(minutes * 60 * 1000, greet)
    api.on("startup", lambda: api.log(f"定时问候已启动，每 {minutes} 分钟一次"))


def teardown():
    pass
```

---

## 9. 安装、启用与调试

1. 把插件文件夹放到 `data/plugins/`。
2. 打开桌宠右键菜单 →「插件管理」。
3. 点「重新扫描」。
4. 勾选「启用」。
5. 看状态是否变成「运行中」。

调试：

- 插件管理里会显示「加载失败」「运行出错（已隔离）」和简短错误。
- 详细堆栈在 `data/plugins.log`。
- 改完代码后点插件行里的「重载」，或点底部「重新扫描」。
- 如果插件把桌宠搞得很卡，先取消启用；插件异常不会阻止桌宠退出。

注意：桌宠首次运行会把内置示例复制到 `data/plugins/`。如果你在源码目录里改示例，需要删掉 `data/plugins/example.*` 再重新运行，或者直接改 `data/plugins/` 里的副本。

---

## 10. 版本兼容

- `api_version` 目前固定为 `1`。
- 新增接口会保持向后兼容；删除或改变语义时会提升 `api_version`。
- 插件应在 `setup()` 里检查自己依赖的能力，缺失时优雅降级，例如：

```python
if api.version < 1:
    api.log("API 版本过低，插件不运行")
    return
```

---

## 11. 提交前检查清单

- [ ] `plugin.json` 是合法 JSON，`id` 唯一且符合命名规则。
- [ ] `api_version` 是 `1`。
- [ ] `entry` 指向存在的文件。
- [ ] `permissions` 只声明真正用到的权限。
- [ ] `setup(api)` 中没有长时间阻塞操作。
- [ ] 没有把 API Key、密码、截图 base64 写进日志或存储。
- [ ] 定时器间隔合理（不建议低于 30 秒）。
- [ ] 插件异常时桌宠仍能正常运行。
- [ ] 在「插件管理」里能正常启用、重载、取消启用。
- [ ] README 里写清楚插件做什么、需要哪些权限、如何配置。

---

## 12. 给 AI 助手的提示词

见同目录的 [`AI_PLUGIN_PROMPT.md`](AI_PLUGIN_PROMPT.md)。把那份文件连同本指南一起交给其他 AI，就可以让它按统一规范产出插件。
