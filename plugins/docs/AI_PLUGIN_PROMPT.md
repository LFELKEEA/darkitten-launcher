# 给其他 AI 的插件开发提示词

把下面整段内容复制给另一个 AI（Claude、GPT、Gemini、本地模型都可以），并把 `PLUGIN_DEV.md` 一并提供给它。

---

## 提示词开始

你在为 **LFELKEEA 桌面宠物**开发一个 Python 插件。

请先阅读随附的 `PLUGIN_DEV.md`，然后严格按它的约定产出文件。不要发明新字段、不要调用文档里没有的 API。

### 运行环境

- 桌宠是 Python + PySide6 应用，插件运行在桌宠主线程。
- 插件 API 版本：`1`。
- 插件放在 `data/plugins/<插件文件夹>/`。
- 入口文件默认 `main.py`，必须定义 `setup(api)`。
- 插件默认不启用，用户需要在「插件管理」里手动勾选。
- 插件是本机代码，不是沙箱；不要做危险操作。

### 你的任务

根据下面的需求写一个插件：

> 【在这里写插件需求。例如：每 30 分钟提醒我喝水；或者：屏幕观察时如果检测到 VS Code，就补充“主人正在写代码”；或者：每周五下午提醒我备份文件。】

### 必须产出

1. `plugin.json`
2. `main.py`
3. `README.md`（中文，说明功能、权限、配置项、测试方法）

如果插件需要多文件，可以增加 `helpers.py` 或 `assets/`，但入口仍然是 `main.py`。

### plugin.json 规范

```json
{
  "id": "your.plugin.id",
  "name": "插件显示名",
  "version": "1.0.0",
  "api_version": 1,
  "entry": "main.py",
  "description": "一句话说明",
  "author": "作者名",
  "permissions": ["pet.say", "timer", "events", "settings"],
  "settings": {
    "example_key": "example_value"
  }
}
```

要求：

- `id` 只能用小写字母、数字、点、下划线、短横线，2–64 位，且不要和内置示例重复。
- `api_version` 必须是 `1`。
- `permissions` 只写真正需要的权限，可选值：
  `pet.say`、`pet.action`、`ai.chat`、`screen.capture`、`timer`、`events`、`settings`、`storage`。
- `settings` 只放普通配置，不要放 API Key、密码、token。

### main.py 规范

```python
def setup(api):
    # 在这里注册定时器、事件、读取设置
    ...


def teardown():
    # 可选
    ...
```

要求：

- `setup(api)` 必须存在，且不能长时间阻塞。
- 定时器用 `api.every(ms, callback)` 或 `api.after(ms, callback)`，不要自己创建 Qt 定时器。
- 事件用 `api.on("事件名", callback)`，可用事件：
  `startup`、`shutdown`、`settings.saved`、`chat.reply`、`screen.context`、`screen.comment`、`pet.pat`、`pet.poke`。
- 说话用 `api.say(text, duration=5000)`。
- 调 AI 用 `api.chat(messages, callback)`，回调签名是 `callback(reply, error)`。
- 截屏用 `api.capture_screen()`，需要 `screen.capture` 权限，只能在主线程调用。
- 读写设置用 `api.settings` / `api.save_settings(...)`。
- 读写小数据用 `api.storage` / `api.save_storage()`。
- 日志用 `api.log(...)`，不要写敏感信息。
- 所有回调都要考虑异常；插件异常虽然会被捕获，但不要让用户看到一堆报错。

### 安全与隐私要求

1. 不要在代码里读取 `data/config.json` 或 `api_key`。
2. 不要把截图、聊天内容、密码、token 写入日志、存储或上传。
3. 需要截屏时必须声明 `screen.capture`；能不用就不用。
4. 屏幕观察插件优先使用 `screen.context` 里的 `title` / `process` / `notes`，不要每次回调都截屏。
5. 不要创建后台线程直接操作 Qt 界面或调用 `api.say()`；需要异步就用 `api.chat()` 或主线程定时器。
6. 不要执行删除文件、修改系统设置、安装软件等危险操作。

### 输出格式

请按以下顺序输出，每个文件用代码块标注路径：

```text
data/plugins/<插件文件夹>/plugin.json
data/plugins/<插件文件夹>/main.py
data/plugins/<插件文件夹>/README.md
```

最后附一段「安装与测试」：

1. 把文件夹放到 `data/plugins/`；
2. 打开桌宠右键菜单 →「插件管理」→「重新扫描」；
3. 勾选「启用」；
4. 观察状态是否为「运行中」；
5. 如果报错，查看 `data/plugins.log`。

## 提示词结束

---

## 给用户的使用建议

- 如果你用的是不支持读文件的聊天 AI，把 `PLUGIN_DEV.md` 的「API 参考」「权限」「事件表」三节一起粘进去。
- 让 AI 先复述它准备使用的权限和事件，再让它写代码，能减少幻觉。
- 拿到代码后先看 `main.py` 有没有联网、读文件、执行命令等意外操作，再启用。
- 插件写完后可以让 AI 按「提交前检查清单」自查一遍。
