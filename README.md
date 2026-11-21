# Darkitten Launcher

Minecraft 启动器 + 下载站源码。当前版本 `1.13.0`。

- 下载站：https://lfelkeea.github.io/darkitten-launcher/
- 更新清单：`update.json`
- 反馈：https://github.com/LFELKEEA/darkitten-launcher/issues

## 仓库结构

```text
app.py / darkitten/       # 启动器主程序（PySide6）
darkitten/ui/pages/       # 各功能页（首页 / 下载 / 整合包 / 皮肤 / 联机 / 服务器 / 游戏助手 / 设置）
assets/                   # 启动器图标与界面素材
tests/                    # 单元测试（联动桥、游戏助手算法）
assets/world_template_modern.dat  # 1.21.11+ 新存档模板（服务端验证通过）
tools/enchcracker/        # EnchCracker 1.6（Apache-2.0，附许可证与 NOTICE）
requirements.txt          # Python 依赖
launch.bat / 启动.bat      # 本地启动脚本
```

## 本地运行

双击 `launch.bat` / `启动.bat`。如果本机没有 Python + PySide6，脚本会通过
`bootstrap_runtime.ps1` 自动下载 Python 3.12.10 嵌入式运行时和
PySide6-Essentials 6.12.0 到程序目录的 `pyruntime/`，首次约 150 MB，之后直接启动。

手动运行：

```bash
pip install -r requirements.txt
python app.py
```

## 游戏助手

启动器左侧导航的「助手」页，不进入游戏也能用：

- **种子**：解析数字 / 十六进制 / 文本种子；版本列表启动后自动从 Chunkbase 读取（缓存 12 小时，
  离线时回落到内置列表），Chunkbase 新增版本后无需更新启动器；精确计算史莱姆区块、结构候选点、
  区块与方块坐标换算、主世界与下界坐标换算；生物群系与地形通过 Chunkbase /
  MinecraftSeedMap 在线查看（原版群系无法离线复现，页面里写清楚了原因）。
- **指令**：45 条常用指令速查、参数校验、21 个可填参数的生成模板，
  可导出命令方块链（`/setblock`，含正确的方块状态与 NBT）和 `.mcfunction` / 数据包。
- **AI 生成**：兼容 OpenAI Chat Completions 协议的任意接口；配置只写入本机
  `assistant.json`，不会打包进发行包。
- **附魔预测**：内置 EnchCracker 1.6 启动入口与 6 步教程。需要 Java 和
  clientcommands 模组（`/cenchant`）。
- **MC百科**：启动器内直接搜索 mcmod.cn，结果可点击跳转浏览器。

## 存档与服务器

首页每个游戏版本右侧的地球按钮可以打开「存档与服务器」：

- **单人游戏**：列出该版本 `saves/` 下的全部存档，显示名称、模式、难度、最后游玩时间、
  游戏版本和**种子**（可一键复制）；可以新建存档（名称、种子、模式、难度、硬核、作弊、
  死亡不掉落）、改名 / 改设置、打开目录、删除（移到回收站）。
- **多人游戏**：读写 `servers.dat`，可添加、编辑、删除服务器，保留图标、资源包开关和隐藏状态。
- **离线修改存档**：不启动游戏就能修改难度、游戏模式、作弊、硬核、天数与时间、天气，
  以及 14 条常用游戏规则（死亡不掉落、昼夜流逝、天气流逝、生物生成、生物破坏、
  自然回血、幻翼、立即重生、进度公告、生物掉落、方块掉落、随机刻速度、重生半径、
  睡觉比例）。同时兼容 1.21.11+ 的 `game_rules` 新格式与旧版 `GameRules` 格式，
  写入前会在同目录保留 `level.dat_old`。

NBT 读写为项目内自实现（`darkitten/nbt.py`），不依赖第三方库；新建存档使用
`assets/world_template_modern.dat`，已在真实 Fabric 1.21.11 服务端加载验证。

## 测试

```bash
python tests/test_bridge.py
python tests/test_game_assistant.py
python tests/test_worlds.py
```

## 第三方组件

`tools/enchcracker/enchcracker-1.6.jar` 来自
[Earthcomputer/EnchantmentCracker](https://github.com/Earthcomputer/EnchantmentCracker)，
按 Apache License 2.0 分发，见同目录 `LICENSE-APACHE-2.0.txt` 与 `NOTICE.txt`。