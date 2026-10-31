# Darkitten Launcher

Minecraft 启动器 + 下载站源码。当前版本 `1.11.6`。

- 下载站：https://lfelkeea.github.io/darkitten-launcher/
- 更新清单：[`update.json`](website/public/update.json)
- 插件库：https://lfelkeea.github.io/darkitten-launcher/plugins.html
- 桌宠源码：[LFELKEEA/darkitten-pet](https://github.com/LFELKEEA/darkitten-pet)
- 反馈：https://github.com/LFELKEEA/darkitten-launcher/issues

## 仓库结构

```text
app.py / darkitten/     # 启动器主程序（PySide6）
assets/                 # 启动器图标与界面素材
requirements.txt        # Python 依赖
launch.bat / 启动.bat    # 本地启动脚本
website/                # 静态下载站源码（index / plugins / feedback / 清单）
tools/                  # 构建与图标工具
qa/                     # 自动化测试与截图脚本（不随发布包分发）
```

## 本地运行

```bash
pip install -r requirements.txt
python app.py
```

网站预览：

```bash
cd website
python serve.py --port 8765
```

打开 http://127.0.0.1:8765/ 。

## 发布

启动器发布包与站点清单由 `website/build_release.py` 生成；插件库由 `website/build_plugins.py` 从桌宠仓库的 `plugins/` 打包。发布包不会包含 `config.json`、`logs/`、`skins/`、`minecraft/` 等本机数据。

## 许可证

代码使用 MIT 许可证，见 [`LICENSE`](LICENSE)。

Minecraft 是 Mojang Studios 的商标，本项目与 Mojang 或 Microsoft 无关联。桌宠使用的第三方语音模型遵循其原始许可。
