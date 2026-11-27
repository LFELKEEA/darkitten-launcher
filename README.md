# Darkitten Launcher

Windows 上的 Minecraft 启动器：版本、加载器、Mod、整合包、皮肤、P2P 联机、服务端、存档和游戏助手都在同一个窗口里完成。当前版本 `1.18.0`，界面基于 PySide6。

- 下载站：https://lfelkeea.github.io/darkitten-launcher/
- 更新清单：`update.json`
- 反馈：https://github.com/LFELKEEA/darkitten-launcher/issues

## 能做什么

**版本与加载器。** 从 Mojang 清单读取正式版和快照；原版装好后可以直接在版本行绑定 Fabric、Forge 或 NeoForge。已有的版本文件夹、ZIP 也能拖进列表。每个版本有独立的 `mods/`、`saves/`、`config/`，互不影响；Java 版本自动判断，缺失时明确提示。

**Mod 与整合包。** Modrinth 与 CurseForge 同屏切换，按游戏版本、加载器和分类筛选。已安装的 Mod 可以启停、检查更新、修复冲突，必填前置缺哪个补哪个。整合包支持 Modrinth、CurseForge、本地 ZIP 和文件夹导入，下载中心集中显示速度、进度和后台任务，中断后可以继续。

**皮肤与账户。** 导入 PNG 自动识别 Classic / Slim，支持 64×64 和 64×32；待机、行走、转身三种动作的 3D 预览可以拖动旋转、滚轮缩放。皮肤库存多套皮肤，点一下切换。正版账户可上传 Mojang，离线账户本地加载；LittleSkin 与游戏账户共用登录状态，密码不经过启动器，令牌用 DPAPI 加密。

**P2P 联机。** 内置 P2P 直连和樱花穿透两套方案。房主开房后把 `U/XXXX-XXXX-XXXX-XXXX` 房间号发给朋友即可组网；优先直连，失败走公共节点中继。页面会显示玩家列表、直连 / 中继人数和延迟，也可以只允许直连或只走中继。房主退出游戏自动关房。

**服务端。** Paper、Spigot、Fabric、Forge、NeoForge 服务端统一管理：启动、停止、看控制台、输入命令、查看 PID / 端口 / 日志都在一个页面。`server.properties` 和插件配置可以可视化编辑并附中文说明，保存前自动备份；世界、玩家数据、插件和配置能整包 ZIP 备份或导入。

**存档与游戏规则。** 每个版本都能预览单人存档和多人服务器。存档列表显示名称、模式、难度、最后游玩时间、版本和种子，可以新建、改名、删除；不启动游戏就能改难度、模式、作弊、硬核、天数、时间、天气和 14 条常用游戏规则。多人服务器支持图标、资源包开关和隐藏状态。

**游戏助手。** 不用进游戏就能查种子对应的群系和区块、生成单条或多命令方块指令、按 RNG 流程预测附魔，也能在启动器内打开 MC 百科。详细能力见下文「游戏助手」。

**快捷操作与诊断。** `Ctrl+K` 可以启动游戏、跳转页面、创建世界快照、生成诊断包或切换主题。安全模式临时禁用 Mod，退出后自动恢复；诊断包会收集日志、系统和 Mod 信息并自动脱敏令牌。

**外观与性能。** 六套主题配色（樱花、暗夜、深海、森林、琥珀、素白）随时切换；可以上传自己的 PNG / JPG / WebP 图片当背景，支持铺满裁切、完整显示、平铺三种方式和透明度调节，重启后保持。模组搜索可以选每页 20 / 40 / 60 / 80 条。游戏启动后会自动降低它的进程与磁盘优先级，切出去用浏览器、写文档时更跟手，设置页可以关闭。

**更新与数据。** 更新只替换 `app.py`、`darkitten/`、`assets/` 和依赖清单，`config.json`、`social.json`、`minecraft/`、`skins/`、`servers/` 不会被覆盖；先校验 SHA-256，失败自动回滚。同一用户只允许一个实例，重复启动会唤醒已有窗口。

## 下载与安装

从[下载站](https://lfelkeea.github.io/darkitten-launcher/)获取安装包，支持 Windows 10 / 11。游戏文件和整合包依赖在首次使用时按需下载，不会一开始就把整个 `.minecraft` 塞进安装目录；Java 版本会先检查，缺失时提示安装。

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