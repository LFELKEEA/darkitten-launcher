from __future__ import annotations

import json
import os
import shutil
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .servers import ServerInfo, save_server, update_server_properties

MAX_CONFIG_BYTES = 2 * 1024 * 1024
CONFIG_SUFFIXES = {
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".properties": "Properties",
    ".conf": "文本",
    ".config": "文本",
}
SKIP_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "advancements",
    "cache",
    "crash-reports",
    "data",
    "libraries",
    "logs",
    "playerdata",
    "region",
    "stats",
    "usercache",
    "versions",
    "web",
    "world",
    "world_nether",
    "world_the_end",
}
ROOT_SERVER_CONFIGS = (
    "bukkit.yml",
    "spigot.yml",
    "paper-global.yml",
    "paper-world-defaults.yml",
    "commands.yml",
    "permissions.yml",
    "help.yml",
)
CATEGORY_ORDER = (
    "基础",
    "玩家与规则",
    "世界",
    "网络",
    "性能",
    "资源包",
    "远程控制",
    "高级",
    "其他",
)
COMMON_PROPERTY_KEYS = (
    "server-port",
    "online-mode",
    "motd",
    "max-players",
    "difficulty",
    "gamemode",
    "pvp",
    "view-distance",
    "simulation-distance",
    "level-name",
    "white-list",
    "enforce-secure-profile",
    "enable-command-block",
    "allow-flight",
    "spawn-protection",
)


@dataclass(frozen=True)
class PropertySpec:
    key: str
    label: str
    help: str
    category: str = "基础"
    kind: str = "string"
    default: str = ""
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


def _spec(
    key: str,
    label: str,
    help_text: str,
    category: str = "基础",
    kind: str = "string",
    default: str = "",
    choices: Iterable[str] = (),
    minimum: int | None = None,
    maximum: int | None = None,
) -> PropertySpec:
    return PropertySpec(
        key=key,
        label=label,
        help=help_text,
        category=category,
        kind=kind,
        default=default,
        choices=tuple(choices),
        minimum=minimum,
        maximum=maximum,
    )


SERVER_PROPERTY_SPECS: dict[str, PropertySpec] = {
    item.key: item
    for item in (
        # 基础
        _spec(
            "server-port", "服务器端口",
            "玩家连接服务器时使用的端口。默认 25565；同一台电脑开多个服务端时要改成不同端口。",
            "基础", "int", "25565", minimum=1, maximum=65535,
        ),
        _spec(
            "server-ip", "监听地址",
            "服务器绑定的本机 IP。留空表示监听所有网卡，通常不需要修改。",
        ),
        _spec(
            "motd", "服务器标语",
            "多人游戏列表里显示的服务器描述，支持颜色代码；可以用 \\n 换行。",
            "基础", "string", "A Minecraft Server",
        ),
        _spec(
            "max-players", "最大玩家数",
            "同时允许进入服务器的玩家数量上限。",
            "基础", "int", "20", minimum=1, maximum=10000,
        ),
        _spec(
            "online-mode", "正版验证",
            "开启后只允许通过 Mojang/Microsoft 验证的正版账户进入；"
            "离线或 LittleSkin 服务器通常需要关闭。",
            "基础", "bool", "true",
        ),
        _spec(
            "enforce-secure-profile", "强制安全聊天签名",
            "要求玩家使用带签名密钥的正版账户。关闭后部分第三方登录或离线客户端更容易进入。",
            "玩家与规则", "bool", "true",
        ),
        _spec(
            "player-idle-timeout", "挂机踢出时间",
            "玩家无操作多少分钟后被踢出；0 表示不自动踢出。",
            "玩家与规则", "int", "0", minimum=0, maximum=1440,
        ),
        _spec(
            "white-list", "启用白名单",
            "只允许 whitelist.json 中的玩家进入。开启前请先用 /whitelist add 添加玩家。",
            "玩家与规则", "bool", "false",
        ),
        _spec(
            "enforce-whitelist", "强制白名单",
            "白名单变更后立即踢出不在名单中的在线玩家。",
            "玩家与规则", "bool", "false",
        ),
        _spec(
            "difficulty", "游戏难度",
            "peaceful 和平、easy 简单、normal 普通、hard 困难。",
            "玩家与规则", "choice", "easy", ("peaceful", "easy", "normal", "hard"),
        ),
        _spec(
            "gamemode", "默认游戏模式",
            "新玩家首次进入时使用的模式：survival 生存、creative 创造、"
            "adventure 冒险、spectator 旁观。",
            "玩家与规则", "choice", "survival",
            ("survival", "creative", "adventure", "spectator"),
        ),
        _spec(
            "force-gamemode", "强制游戏模式",
            "玩家每次加入时都重置为默认游戏模式，而不是保留上次的模式。",
            "玩家与规则", "bool", "false",
        ),
        _spec(
            "hardcore", "极限模式",
            "开启后玩家死亡会进入旁观者模式，世界难度固定为困难。",
            "玩家与规则", "bool", "false",
        ),
        _spec(
            "pvp", "允许玩家互相伤害",
            "关闭后玩家之间无法直接造成伤害。",
            "玩家与规则", "bool", "true",
        ),
        _spec(
            "allow-flight", "允许飞行",
            "允许玩家在生存模式下飞行；使用飞行类 Mod 或插件时通常需要开启。",
            "玩家与规则", "bool", "false",
        ),
        _spec(
            "spawn-protection", "出生点保护半径",
            "出生点周围多少格内非管理员不能破坏方块；0 表示关闭保护。",
            "玩家与规则", "int", "16", minimum=0, maximum=10000,
        ),
        _spec(
            "enable-command-block", "启用命令方块",
            "允许使用命令方块执行命令。",
            "玩家与规则", "bool", "false",
        ),
        _spec(
            "op-permission-level", "管理员权限等级",
            "OP 默认拥有的权限等级（1-4）；4 拥有全部命令权限。",
            "玩家与规则", "choice", "4", ("1", "2", "3", "4"),
        ),
        _spec(
            "function-permission-level", "函数权限等级",
            "数据包函数和命令方块默认可使用的权限等级（1-4）。",
            "玩家与规则", "choice", "2", ("1", "2", "3", "4"),
        ),
        _spec(
            "allow-nether", "允许进入下界",
            "关闭后玩家无法通过传送门进入下界。",
            "世界", "bool", "true",
        ),
        _spec(
            "level-name", "世界文件夹名称",
            "主世界存档所在的文件夹名。修改后会加载另一个世界，原世界不会自动搬迁。",
            "世界", "string", "world",
        ),
        _spec(
            "level-seed", "世界种子",
            "生成新世界时使用的种子；已有世界不会因为修改这里而改变。",
            "世界", "string", "",
        ),
        _spec(
            "level-type", "世界生成类型",
            "normal 普通、flat 超平坦、large_biomes 巨型生物群系、"
            "amplified 放大化、single_biome_surface 单一生物群系。",
            "世界", "choice", "normal",
            (
                "normal", "flat", "large_biomes", "amplified",
                "single_biome_surface",
            ),
        ),
        _spec(
            "generate-structures", "生成结构",
            "生成村庄、要塞、遗迹等结构；只影响新生成的地图区域。",
            "世界", "bool", "true",
        ),
        _spec(
            "generator-settings", "自定义生成设置",
            "超平坦等世界类型的生成参数，JSON 格式；普通世界通常留空或 {}。",
            "世界", "string", "{}",
        ),
        _spec(
            "initial-enabled-packs", "默认启用数据包",
            "新世界默认启用的数据包名称，多个名称用英文逗号分隔。",
            "世界", "string", "vanilla",
        ),
        _spec(
            "initial-disabled-packs", "默认禁用数据包",
            "新世界默认禁用的数据包名称，多个名称用英文逗号分隔。",
            "世界", "string", "",
        ),
        _spec(
            "max-world-size", "世界边界半径",
            "世界边界的最大半径，单位方块；默认值接近 3000 万格。",
            "世界", "int", "29999984", minimum=1, maximum=29999984,
        ),
        _spec(
            "spawn-monsters", "生成敌对生物",
            "是否自然生成僵尸、骷髅等敌对生物。",
            "世界", "bool", "true",
        ),
        _spec(
            "spawn-animals", "生成动物",
            "是否自然生成牛羊等被动生物。",
            "世界", "bool", "true",
        ),
        _spec(
            "spawn-npcs", "生成村民",
            "是否生成村民等 NPC。",
            "世界", "bool", "true",
        ),
        _spec(
            "view-distance", "视距",
            "服务器向玩家发送的区块半径。数值越大玩家看得越远，也更吃 CPU 和带宽。",
            "性能", "int", "10", minimum=3, maximum=32,
        ),
        _spec(
            "simulation-distance", "模拟距离",
            "实体、作物和红石等逻辑更新的区块半径。降低它可以明显减轻卡顿。",
            "性能", "int", "10", minimum=3, maximum=32,
        ),
        _spec(
            "entity-broadcast-range-percentage", "实体同步范围",
            "实体对玩家的可见/同步距离百分比，100 为默认；降低可减少带宽。",
            "性能", "int", "100", minimum=10, maximum=1000,
        ),
        _spec(
            "max-tick-time", "单刻超时",
            "单个游戏刻允许的最长毫秒数，超过后服务器会判定卡死；-1 表示禁用超时检测。",
            "性能", "int", "60000", minimum=-1, maximum=600000,
        ),
        _spec(
            "network-compression-threshold", "网络压缩阈值",
            "超过多少字节的数据包才压缩；-1 关闭压缩，0 全部压缩。一般保持默认。",
            "性能", "int", "256", minimum=-1, maximum=65536,
        ),
        _spec(
            "rate-limit", "数据包速率限制",
            "每个玩家每秒允许的数据包数量上限；0 表示不限制。",
            "性能", "int", "0", minimum=0, maximum=1000000,
        ),
        _spec(
            "sync-chunk-writes", "同步写入区块",
            "开启后区块写入更保守，通常更安全但可能增加磁盘压力。",
            "高级", "bool", "true",
        ),
        _spec(
            "use-native-transport", "启用原生网络传输",
            "在 Linux 上使用更高效的系统网络调用；Windows 上通常没有效果。",
            "高级", "bool", "true",
        ),
        _spec(
            "max-chained-neighbor-updates", "连锁方块更新上限",
            "限制红石等连锁方块更新的最大数量，防止恶意红石造成卡服。",
            "高级", "int", "1000000", minimum=0, maximum=10000000,
        ),
        _spec(
            "region-file-compression", "区块文件压缩方式",
            "deflate 兼容性最好；lz4 读写更快；none 不压缩但文件更大。",
            "高级", "choice", "deflate", ("deflate", "lz4", "none"),
        ),
        _spec(
            "enable-status", "在列表中显示状态",
            "关闭后服务器不会响应服务器列表的 ping，多人列表里可能显示离线。",
            "网络", "bool", "true",
        ),
        _spec(
            "hide-online-players", "隐藏在线玩家列表",
            "在服务器列表里不显示当前在线玩家。",
            "网络", "bool", "false",
        ),
        _spec(
            "log-ips", "记录玩家 IP",
            "是否在日志中记录玩家 IP 地址。",
            "网络", "bool", "true",
        ),
        _spec(
            "prevent-proxy-connections", "阻止代理连接",
            "开启后要求客户端连接地址与 Mojang 验证记录一致，可能误伤部分代理/VPN 玩家。",
            "网络", "bool", "false",
        ),
        _spec(
            "accepts-transfers", "接受跨服转移",
            "允许玩家从其他服务器通过 transfer 数据包直接转移到本服。",
            "网络", "bool", "false",
        ),
        _spec(
            "pause-when-empty-seconds", "空服暂停时间",
            "服务器没有玩家多少秒后暂停游戏逻辑；0 表示不暂停。适用于 1.21.2 及更新版本。",
            "性能", "int", "60", minimum=0, maximum=86400,
        ),
        _spec(
            "require-resource-pack", "强制资源包",
            "开启后玩家必须接受服务器资源包才能进入。",
            "资源包", "bool", "false",
        ),
        _spec(
            "resource-pack", "资源包地址",
            "服务器资源包的直接下载 URL；留空表示不使用。",
            "资源包", "string", "",
        ),
        _spec(
            "resource-pack-prompt", "资源包提示",
            "资源包确认界面显示的提示文字，支持 JSON 文本组件。",
            "资源包", "string", "",
        ),
        _spec(
            "resource-pack-sha1", "资源包 SHA1",
            "资源包文件的 SHA-1 校验值，用于确认下载完整。",
            "资源包", "string", "",
        ),
        _spec(
            "resource-pack-id", "资源包标识",
            "资源包的稳定标识字符串，用于客户端缓存。",
            "资源包", "string", "",
        ),
        _spec(
            "enable-query", "启用 Query",
            "开启 GameSpy4 Query 协议，供服务器列表网站查询状态。",
            "远程控制", "bool", "false",
        ),
        _spec(
            "query.port", "Query 端口",
            "Query 服务监听的端口，通常与服务器端口相同。",
            "远程控制", "int", "25565", minimum=1, maximum=65535,
        ),
        _spec(
            "enable-rcon", "启用 RCON",
            "开启远程控制台协议，允许外部程序执行服务器命令。只应在可信网络中开启。",
            "远程控制", "bool", "false",
        ),
        _spec(
            "rcon.port", "RCON 端口",
            "RCON 服务监听的端口，默认 25575。",
            "远程控制", "int", "25575", minimum=1, maximum=65535,
        ),
        _spec(
            "rcon.password", "RCON 密码",
            "远程控制台密码。开启 RCON 时请设置强密码，并避免暴露到公网。",
            "远程控制", "string", "",
        ),
        _spec(
            "broadcast-rcon-to-ops", "向管理员广播 RCON 命令",
            "其他程序通过 RCON 执行命令时，是否提示在线管理员。",
            "远程控制", "bool", "true",
        ),
        _spec(
            "broadcast-console-to-ops", "向管理员广播控制台命令",
            "控制台执行命令时，是否提示在线管理员。",
            "远程控制", "bool", "true",
        ),
        _spec(
            "enable-jmx-monitoring", "启用 JMX 监控",
            "开放 JMX 性能指标，通常只有专业监控工具需要。",
            "高级", "bool", "false",
        ),
        _spec(
            "text-filtering-config", "聊天过滤配置",
            "聊天文本过滤服务的配置 ID；没有对接过滤服务时留空。",
            "高级", "string", "",
        ),
        _spec(
            "enable-code-of-conduct", "启用行为准则",
            "要求玩家在多人游戏中同意服务器行为准则（较新版本可用）。",
            "高级", "bool", "false",
        ),
        _spec(
            "bug-report-link", "漏洞反馈链接",
            "显示在客户端暂停菜单中的服务器反馈链接。",
            "高级", "string", "",
        ),
        _spec(
            "status-heartbeat-interval", "状态心跳间隔",
            "服务器向客户端发送状态心跳的间隔秒数；0 使用默认行为。",
            "高级", "int", "0", minimum=0, maximum=3600,
        ),
        _spec(
            "debug", "调试模式",
            "输出更详细的调试信息，通常只在排查问题时临时开启。",
            "高级", "bool", "false",
        ),
    )
}


def property_spec(key: str) -> PropertySpec:
    known = SERVER_PROPERTY_SPECS.get(key)
    if known is not None:
        return known
    return PropertySpec(
        key=key,
        label=key,
        help="Minecraft 或插件写入的自定义配置项；请结合对应插件文档修改。",
        category="其他",
    )


def _unescape_properties(text: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "\\" or index + 1 >= len(text):
            result.append(char)
            index += 1
            continue
        index += 1
        code = text[index]
        if code == "u" and index + 4 < len(text):
            try:
                result.append(chr(int(text[index + 1 : index + 5], 16)))
                index += 5
                continue
            except ValueError:
                result.append("\\u")
                index += 1
                continue
        result.append(
            {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(code, code)
        )
        index += 1
    return "".join(result)


def _split_property(line: str) -> tuple[str, str] | None:
    escaped = False
    split_at = -1
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in "=:":
            split_at = index
            break
        if char.isspace():
            split_at = index
            break
    if split_at < 0:
        return None
    key = line[:split_at].strip()
    value = line[split_at + 1 :] if line[split_at : split_at + 1] in "=:" else line[split_at:]
    return _unescape_properties(key), _unescape_properties(value.strip())


def read_server_properties(path: Path) -> dict[str, str]:
    if not Path(path).is_file():
        return {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    pending = ""
    for physical in text.splitlines():
        line = physical.lstrip() if pending else physical
        if line.endswith("\\") and not line.endswith("\\\\"):
            pending += line[:-1]
            continue
        line = pending + line
        pending = ""
        raw = line.strip()
        if not raw or raw.startswith(("#", "!")):
            continue
        parsed = _split_property(line.strip())
        if parsed is None:
            continue
        key, value = parsed
        if key:
            values[key] = value
    if pending:
        parsed = _split_property(pending.strip())
        if parsed is not None and parsed[0]:
            values[parsed[0]] = parsed[1]
    return values


def property_rows(
    values: dict[str, str], include_all: bool = False
) -> list[tuple[PropertySpec, str]]:
    """按中文分类返回属性行，未知键放在“其他”。

    文件里没有的常用项会用默认值补齐，方便直接开启；
    include_all 为 True 时补齐全部已知项。
    """
    rows: list[tuple[PropertySpec, str]] = []
    for key, value in values.items():
        rows.append((property_spec(key), value))
    present = set(values)
    keys = (
        list(SERVER_PROPERTY_SPECS)
        if include_all
        else list(COMMON_PROPERTY_KEYS)
    )
    for key in keys:
        spec = SERVER_PROPERTY_SPECS.get(key)
        if spec is not None and key not in present:
            rows.append((spec, spec.default))
    order = {category: index for index, category in enumerate(CATEGORY_ORDER)}
    rows.sort(
        key=lambda item: (
            order.get(item[0].category, 99),
            list(SERVER_PROPERTY_SPECS).index(item[0].key)
            if item[0].key in SERVER_PROPERTY_SPECS
            else 100000,
            item[0].key.lower(),
        )
    )
    return rows


def normalise_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


def validate_property(spec: PropertySpec, value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if spec.kind == "bool":
        lowered = raw.lower()
        if lowered in {"true", "false"}:
            return lowered, ""
        if lowered in {"1", "0", "yes", "no", "on", "off"}:
            return "true" if lowered in {"1", "yes", "on"} else "false", ""
        return raw, f"{spec.label}只能填写 true 或 false。"
    if spec.kind == "int":
        try:
            number = int(raw)
        except ValueError:
            return raw, f"{spec.label}需要填写整数。"
        if spec.minimum is not None and number < spec.minimum:
            return raw, f"{spec.label}不能小于 {spec.minimum}。"
        if spec.maximum is not None and number > spec.maximum:
            return raw, f"{spec.label}不能大于 {spec.maximum}。"
        return str(number), ""
    if spec.kind == "choice":
        if raw not in spec.choices:
            return raw, f"{spec.label}只能从：{'、'.join(spec.choices)} 中选择。"
        return raw, ""
    if "\x00" in raw:
        return raw, f"{spec.label}包含无效字符。"
    return raw, ""


def save_server_properties(info: ServerInfo, values: dict[str, str]) -> Path:
    path = info.path / "server.properties"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        backup = path.with_name(path.name + ".bak")
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
    try:
        info.port = max(1, min(65535, int(values.get("server-port", info.port))))
    except (TypeError, ValueError):
        pass
    info.online_mode = normalise_bool(values.get("online-mode", str(info.online_mode)))
    update_server_properties(info, values)
    save_server(info)
    return path


@dataclass(frozen=True)
class PluginConfigFile:
    path: Path
    relative: str
    group: str
    format: str
    size: int
    modified: float

    @property
    def display_size(self) -> str:
        if self.size >= 1024 * 1024:
            return f"{self.size / 1024 / 1024:.1f} MB"
        if self.size >= 1024:
            return f"{self.size / 1024:.0f} KB"
        return f"{self.size} B"


def _config_format(path: Path) -> str:
    return CONFIG_SUFFIXES.get(path.suffix.lower(), "")


def _relative_label(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def scan_plugin_configs(info: ServerInfo) -> list[PluginConfigFile]:
    base = info.path
    if not base.is_dir():
        return []
    roots: list[tuple[Path, str]] = []
    plugins = base / "plugins"
    if plugins.is_dir():
        roots.append((plugins, "插件"))
    config = base / "config"
    if config.is_dir():
        roots.append((config, "Mod / 服务器"))
    files: dict[str, PluginConfigFile] = {}

    def add(path: Path, group: str) -> None:
        fmt = _config_format(path)
        if not fmt:
            return
        try:
            stat = path.stat()
        except OSError:
            return
        if stat.st_size > MAX_CONFIG_BYTES:
            return
        relative = _relative_label(path, base)
        files[relative.lower()] = PluginConfigFile(
            path=path,
            relative=relative,
            group=group,
            format=fmt,
            size=stat.st_size,
            modified=stat.st_mtime,
        )

    for root, group in roots:
        for current, dirs, names in os.walk(root):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                depth = 0
            if depth >= 4:
                dirs[:] = []
            dirs[:] = [
                name
                for name in dirs
                if name.lower() not in SKIP_DIRS and not name.startswith(".")
            ]
            for name in names:
                add(current_path / name, group)
                if len(files) >= 500:
                    break
            if len(files) >= 500:
                break

    for name in ROOT_SERVER_CONFIGS:
        path = base / name
        if path.is_file():
            add(path, "服务器")

    return sorted(
        files.values(),
        key=lambda item: (item.group != "服务器", item.group.lower(), item.relative.lower()),
    )


def read_config_text(path: Path) -> str:
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def write_text_atomic(path: Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".bak")
    if path.is_file():
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(str(text), encoding="utf-8", newline="\n")
    os.replace(temp, path)
    return path


def validate_config_text(text: str, suffix: str) -> str:
    suffix = str(suffix or "").lower()
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return f"JSON 第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}"
        except ValueError as exc:
            return f"JSON 格式错误：{exc}"
        return ""
    if suffix == ".toml":
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            return f"TOML 格式错误：{exc}"
        return ""
    if suffix in {".yml", ".yaml"}:
        seen: dict[str, int] = {}
        for number, line in enumerate(text.splitlines(), start=1):
            if "\t" in line[: len(line) - len(line.lstrip())]:
                return f"YAML 第 {number} 行使用了 Tab 缩进，请改成空格。"
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "---", "...")):
                continue
            if line[:1] not in {" ", "-"} and ":" in line:
                key = line.split(":", 1)[0].strip()
                if key and key in seen:
                    return (
                        f"YAML 第 {number} 行的顶层键“{key}”与第 {seen[key]} 行重复，"
                        "请合并或改名。"
                    )
                if key:
                    seen[key] = number
        return ""
    if suffix == ".properties":
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "!")):
                continue
            if "\\u" in stripped:
                tail = stripped.split("\\u", 1)[1][:4]
                if len(tail) < 4 or any(c not in "0123456789abcdefABCDEF" for c in tail):
                    return f"Properties 第 {number} 行有无效的 \\u 转义。"
        return ""
    return ""


_CONFIG_KEY_NOTES: dict[str, str] = {
    "enabled": "是否启用这个功能。",
    "enable": "是否启用这个功能。",
    "disabled": "是否停用这个功能。",
    "debug": "调试模式，开启后会输出更多日志，平时建议关闭。",
    "prefix": "插件消息的前缀，通常支持颜色代码。",
    "language": "语言文件或语言代码，例如 zh_CN、en_US。",
    "locale": "语言/地区设置。",
    "check-update": "是否检查插件或配置更新。",
    "check-for-updates": "是否检查插件或配置更新。",
    "update-check": "是否检查更新。",
    "metrics": "是否发送匿名统计（bStats）；注重隐私时可以关闭。",
    "bstats": "是否启用 bStats 匿名统计。",
    "database": "数据库配置分组，包含连接方式和账号密码。",
    "storage": "数据存储方式或存储配置。",
    "type": "类型选择，具体可选值由插件决定。",
    "host": "数据库或服务地址，通常填 localhost 或 IP。",
    "port": "服务端口，需要与对应服务监听端口一致。",
    "username": "连接数据库或服务使用的用户名。",
    "password": "连接密码。不要把这个文件发给不可信的人。",
    "table": "数据表名称。",
    "file": "数据文件路径，通常相对于插件数据目录。",
    "path": "文件或目录路径。",
    "url": "请求或下载地址，需要填写完整的 http(s) 链接。",
    "timeout": "请求超时时间，单位通常是秒或毫秒，由插件决定。",
    "cache": "缓存相关设置。",
    "cooldown": "冷却时间，防止玩家频繁触发同一功能。",
    "command": "命令名称或要执行的命令。",
    "commands": "命令列表或命令配置分组。",
    "aliases": "命令别名，玩家可以用这些名字触发同一个命令。",
    "permission": "执行该功能需要的权限节点。",
    "permissions": "权限配置分组。",
    "world": "生效的世界名称；ALL 或 * 通常表示所有世界。",
    "worlds": "生效的世界列表。",
    "default": "默认值或默认分组。",
    "default-world": "玩家默认所在的世界。",
    "allow": "允许名单或允许开关。",
    "deny": "拒绝名单或禁止开关。",
    "blacklist": "黑名单列表。",
    "whitelist": "白名单列表。",
    "message": "提示消息内容，通常支持颜色代码和占位符。",
    "messages": "消息文本配置分组。",
    "format": "显示格式，通常包含 %player% 等占位符。",
    "sound": "触发时播放的音效名称。",
    "particle": "触发时显示的粒子效果。",
    "radius": "作用半径，单位通常是方块。",
    "distance": "距离限制，单位通常是方块。",
    "max": "最大数量或上限。",
    "min": "最小数量或下限。",
    "amount": "数量。",
    "chance": "触发概率，通常为 0-100 或 0-1。",
    "interval": "执行间隔，单位由插件决定。",
    "seconds": "时间长度，单位秒。",
    "minutes": "时间长度，单位分钟。",
    "mode": "运行模式，可选值由插件决定。",
    "log": "是否记录日志或日志级别。",
    "console": "是否在控制台输出。",
    "player": "玩家相关设置。",
    "players": "玩家列表或玩家相关配置。",
    "kick": "踢出玩家相关设置。",
    "ban": "封禁玩家相关设置。",
    "motd": "服务器或功能提示文字。",
    "join": "玩家加入时的行为或消息。",
    "quit": "玩家退出时的行为或消息。",
    "first-join": "玩家第一次加入时的行为或消息。",
    "respawn": "玩家重生时的行为。",
    "spawn": "出生点或生成相关设置。",
    "teleport": "传送相关设置。",
    "economy": "经济系统相关设置。",
    "balance": "玩家余额或初始余额。",
    "currency": "货币名称或符号。",
    "cost": "花费或价格。",
    "price": "价格。",
    "placeholders": "占位符相关设置，可与 PlaceholderAPI 联动。",
    "hooks": "与其他插件的联动设置。",
    "integration": "与其他插件或服务的集成设置。",
    "sync": "数据同步开关或同步方式。",
    "redis": "Redis 连接配置，用于跨服同步。",
    "mysql": "MySQL 数据库连接配置。",
    "sqlite": "SQLite 本地数据库配置。",
    "encoding": "文件编码，通常使用 UTF-8。",
    "max-players": "最大玩家数。",
    "min-players": "最少玩家数。",
    "scoreboard": "计分板相关设置。",
    "tab": "Tab 玩家列表相关设置。",
    "chat": "聊天相关设置。",
    "join-message": "玩家加入时显示的消息。",
    "quit-message": "玩家退出时显示的消息。",
    "death-message": "玩家死亡时显示的消息。",
    "spawn-protection": "出生点保护范围。",
    "pvp": "玩家之间是否可以互相伤害。",
    "keep-inventory": "死亡后是否保留物品。",
    "keep-experience": "死亡后是否保留经验。",
    "exp": "经验相关设置。",
    "level": "等级相关设置。",
    "health": "生命值相关设置。",
    "food": "饥饿值相关设置。",
    "damage": "伤害相关设置。",
    "attack": "攻击相关设置。",
    "defense": "防御相关设置。",
    "speed": "速度相关设置。",
    "fly": "飞行相关设置。",
    "gamemode": "游戏模式。",
    "difficulty": "游戏难度。",
    "weather": "天气相关设置。",
    "time": "时间相关设置。",
    "day": "白天相关设置。",
    "night": "夜晚相关设置。",
    "region": "区域/领地相关设置。",
    "selection": "选择区域的方式或提示。",
    "tool": "区域选择工具。",
    "flag": "区域规则开关。",
    "flags": "区域规则列表。",
    "owner": "所有者。",
    "members": "成员列表。",
    "admin": "管理员相关设置。",
    "admins": "管理员列表。",
}


def explain_config_key(key: str) -> str:
    text = str(key or "").strip().strip("\"'")
    if not text:
        return ""
    if text in SERVER_PROPERTY_SPECS:
        return SERVER_PROPERTY_SPECS[text].help
    for candidate in (text, text.lower()):
        note = _CONFIG_KEY_NOTES.get(candidate.lower())
        if note:
            return note
    words = [
        part
        for part in text.replace("-", "_").replace(".", "_").split("_")
        if part
    ]
    notes = [
        _CONFIG_KEY_NOTES.get(word.lower())
        for word in words
        if _CONFIG_KEY_NOTES.get(word.lower())
    ]
    if notes:
        return "；".join(dict.fromkeys(notes))
    return "插件自定义配置项；具体含义以该插件的官方文档或配置注释为准。"


@dataclass(frozen=True)
class ConfigHint:
    key: str
    value: str
    line: int
    note: str


def _yaml_top_keys(text: str) -> list[tuple[str, str, int]]:
    result: list[tuple[str, str, int]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line or line[:1].isspace() or line.startswith(("#", "-", "---", "...")):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().strip("\"'")
        if not key:
            continue
        value = value.strip()
        if value.startswith("#"):
            value = ""
        if len(value) > 80:
            value = value[:77] + "..."
        result.append((key, value, number))
    return result


def _json_top_keys(text: str) -> list[tuple[str, str, int]]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    result: list[tuple[str, str, int]] = []
    for key, value in list(data.items())[:80]:
        if isinstance(value, (dict, list)):
            preview = "配置分组" if isinstance(value, dict) else f"列表（{len(value)} 项）"
        else:
            preview = str(value)
        if len(preview) > 80:
            preview = preview[:77] + "..."
        result.append((str(key), preview, 0))
    return result


def _toml_top_keys(text: str) -> list[tuple[str, str, int]]:
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    result: list[tuple[str, str, int]] = []
    for key, value in list(data.items())[:80]:
        if isinstance(value, dict):
            preview = "配置分组"
        elif isinstance(value, list):
            preview = f"列表（{len(value)} 项）"
        else:
            preview = str(value)
        if len(preview) > 80:
            preview = preview[:77] + "..."
        result.append((str(key), preview, 0))
    return result


def _properties_top_keys(text: str) -> list[tuple[str, str, int]]:
    result: list[tuple[str, str, int]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        parsed = _split_property(stripped)
        if parsed is None:
            continue
        key, value = parsed
        if len(value) > 80:
            value = value[:77] + "..."
        result.append((key, value, number))
    return result


def describe_config(text: str, suffix: str) -> list[ConfigHint]:
    suffix = str(suffix or "").lower()
    if suffix in {".yml", ".yaml"}:
        rows = _yaml_top_keys(text)
    elif suffix == ".json":
        rows = _json_top_keys(text)
    elif suffix == ".toml":
        rows = _toml_top_keys(text)
    else:
        rows = _properties_top_keys(text)
    hints: list[ConfigHint] = []
    for key, value, line in rows[:80]:
        hints.append(
            ConfigHint(
                key=key,
                value=value,
                line=line,
                note=explain_config_key(key),
            )
        )
    return hints


def key_at_line(text: str, line_number: int) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    index = max(0, min(len(lines) - 1, int(line_number) - 1))
    line = lines[index]
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "!", "//")):
        return ""
    if stripped.startswith(("[", "{")):
        return ""
    for separator in (":", "="):
        if separator in stripped:
            key = stripped.split(separator, 1)[0].strip().strip("\"'")
            key = key.lstrip("-").strip()
            if key and " " not in key:
                return key
    return ""


def file_modified_text(timestamp: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(timestamp)))
    except (OSError, ValueError, OverflowError):
        return ""


def config_summary(info: ServerInfo) -> str:
    try:
        values = read_server_properties(info.path / "server.properties")
    except Exception:
        values = {}
    port = values.get("server-port", str(info.port))
    online = "正版验证" if normalise_bool(values.get("online-mode", "")) else "离线可进"
    return f"端口 {port} · {online} · {len(scan_plugin_configs(info))} 个可编辑配置文件"
