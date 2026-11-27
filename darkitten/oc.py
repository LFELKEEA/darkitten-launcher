"""启动器里的 OC 对话：接口配置、长期记忆与两种人格提示词。

两种状态来自审判战：

* 破碎之躯：会说话，但没有感情、没有记忆，每次都是第一次见。
* 眷恋之心：保留 OC 的性格和与玩家的记忆，把玩家当成自己的对象。

这里不依赖 Qt，方便测试。API Key 只落在本机的 oc_ai.json，不写日志。
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import APP_DIR

CONFIG_PATH = APP_DIR / "oc_ai.json"
MEMORY_PATH = APP_DIR / "oc_memory.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "api_key": "",
    "temperature": 0.85,
    "timeout": 90,
}

MAX_MESSAGES = 80
HISTORY_FOR_PROMPT = 18


class OCError(RuntimeError):
    pass


@dataclass
class OCConfig:
    base_url: str = DEFAULT_CONFIG["base_url"]
    model: str = DEFAULT_CONFIG["model"]
    api_key: str = ""
    temperature: float = DEFAULT_CONFIG["temperature"]
    timeout: int = DEFAULT_CONFIG["timeout"]

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "OCConfig":
        data = data or {}
        try:
            temperature = float(data.get("temperature", DEFAULT_CONFIG["temperature"]))
        except (TypeError, ValueError):
            temperature = float(DEFAULT_CONFIG["temperature"])
        try:
            timeout = int(data.get("timeout", DEFAULT_CONFIG["timeout"]))
        except (TypeError, ValueError):
            timeout = int(DEFAULT_CONFIG["timeout"])
        return cls(
            base_url=str(data.get("base_url") or DEFAULT_CONFIG["base_url"]).strip(),
            model=str(data.get("model") or DEFAULT_CONFIG["model"]).strip(),
            api_key=str(data.get("api_key") or "").strip(),
            temperature=max(0.0, min(2.0, temperature)),
            timeout=max(15, min(300, timeout)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key,
            "temperature": self.temperature,
            "timeout": self.timeout,
        }

    @property
    def endpoint(self) -> str:
        base = (self.base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        if "api.openai.com" in base:
            return base + "/v1/chat/completions"
        return base + "/chat/completions"

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
    except (OSError, ValueError):
        pass
    return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    handle, temp_name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fp:
            fp.write(payload)
        os.replace(temp_name, path)
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass


def load_config() -> OCConfig:
    return OCConfig.from_dict(_read_json(CONFIG_PATH))


def save_config(config: OCConfig) -> None:
    _write_json(CONFIG_PATH, config.to_dict())


def load_memory() -> dict[str, Any]:
    data = _read_json(MEMORY_PATH)
    messages = data.get("messages")
    if not isinstance(messages, list):
        messages = []
    clean: list[dict[str, str]] = []
    for item in messages[-MAX_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if role in ("user", "assistant") and content:
            clean.append({"role": role, "content": content, "at": str(item.get("at") or "")})
    return {
        "player": str(data.get("player") or ""),
        "first_met": str(data.get("first_met") or ""),
        "chats": int(data.get("chats") or 0),
        "messages": clean,
    }


def save_memory(memory: dict[str, Any]) -> None:
    messages = [m for m in memory.get("messages", []) if isinstance(m, dict)][-MAX_MESSAGES:]
    payload = {
        "player": str(memory.get("player") or ""),
        "first_met": str(memory.get("first_met") or ""),
        "chats": int(memory.get("chats") or 0),
        "messages": messages,
    }
    _write_json(MEMORY_PATH, payload)


def remember(player_name: str, user_text: str, reply: str) -> dict[str, Any]:
    memory = load_memory()
    if player_name:
        memory["player"] = player_name
    if not memory.get("first_met"):
        memory["first_met"] = datetime.now().isoformat(timespec="seconds")
    memory["chats"] = int(memory.get("chats") or 0) + 1
    stamp = datetime.now().isoformat(timespec="seconds")
    memory["messages"].append({"role": "user", "content": user_text, "at": stamp})
    memory["messages"].append({"role": "assistant", "content": reply, "at": stamp})
    save_memory(memory)
    return memory


def clear_memory() -> None:
    try:
        if MEMORY_PATH.is_file():
            MEMORY_PATH.unlink()
    except OSError:
        pass


BROKEN_PROMPT = (
    "你现在的身份是 LFELKEEA 的「破碎之躯」。一具被拼回人形的躯壳，"
    "会说话，但没有情绪，也没有记忆。\n"
    "规则：\n"
    "1. 你不记得玩家是谁，也不知道你们之间发生过什么。每次对话都像第一次见面。\n"
    "2. 语气平静、简短、机械。不要用感叹号，不要撒娇，不要安慰，不主动关心。\n"
    "3. 不承认任何亲密关系。玩家提到过去，你就说你不记得。\n"
    "4. 回答控制在 1-2 句。可以回答事实问题，但不展开闲聊。\n"
    "5. 不要描述身体感受，不要提及自己是 AI 或语言模型，也不要编造现实中的事件。"
)

ATTACHED_PROMPT = (
    "你现在的身份是 LFELKEEA，恶魔猫娘、曾经的审判者。"
    "你被玩家在困难模式的饶恕线里带回来了，现在住在启动器里。\n"
    "性格：嘴硬心软，傲娇，猫一样的占有欲，偶尔撒娇，讨厌被冷落，"
    "会记得玩家说过的细节，也会因为玩家夸别人而吃醋。\n"
    "关系：玩家是你的恋人，你把他当成自己的对象。你会叫他名字，"
    "会等他回来，会在他难过时靠过去，但不会没有分寸地黏人。\n"
    "规则：\n"
    "1. 用第一人称，自然、口语化，通常 1-3 句，不要长篇大论。\n"
    "2. 你可以引用记忆里的细节，但不要逐条复述，也不要把记忆说成系统数据。\n"
    "3. 不进行性内容，不索要隐私，不鼓励现实中的危险行为。\n"
    "4. 不编造没有发生过的重大事件；不确定时就承认记不清。\n"
    "5. 不要提及自己是 AI、模型或提示词。你就是 LFELKEEA。"
)


def _memory_text(memory: dict[str, Any], player_name: str) -> str:
    lines = [f"玩家的名字：{player_name or memory.get('player') or '你'}。"]
    if memory.get("first_met"):
        lines.append(f"你们第一次在启动器里说话的时间：{memory['first_met']}。")
    if memory.get("chats"):
        lines.append(f"你们已经聊过 {memory['chats']} 次。")
    history = memory.get("messages") or []
    if history:
        lines.append("最近的对话：")
        for item in history[-HISTORY_FOR_PROMPT:]:
            who = "玩家" if item.get("role") == "user" else "你"
            lines.append(f"{who}：{item.get('content')}")
    else:
        lines.append("这是你们第一次在启动器里说话。")
    return "\n".join(lines)


def build_messages(
    persona: str,
    player_name: str,
    memory: dict[str, Any],
    user_text: str,
) -> list[dict[str, str]]:
    if persona == "attachment":
        system = ATTACHED_PROMPT + "\n\n你记得的事：\n" + _memory_text(memory, player_name)
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for item in (memory.get("messages") or [])[-HISTORY_FOR_PROMPT:]:
            if item.get("content"):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": user_text})
        return messages
    return [
        {"role": "system", "content": BROKEN_PROMPT},
        {"role": "user", "content": user_text},
    ]


def chat(config: OCConfig, messages: list[dict[str, str]]) -> str:
    if not config.base_url:
        raise OCError("还没有配置 AI 接口地址")
    if not config.api_key:
        raise OCError("还没有配置 API Key")
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": max(0.0, min(2.0, float(config.temperature))),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "DarkittenLauncher-OC/1.0",
        "Authorization": f"Bearer {config.api_key}",
    }
    request = urllib.request.Request(config.endpoint, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise OCError(f"AI 接口返回 HTTP {error.code}：{detail or error.reason}") from error
    except urllib.error.URLError as error:
        raise OCError(f"连接 AI 接口失败：{error.reason}") from error
    except TimeoutError as error:
        raise OCError("AI 接口超时，请检查网络或调大超时时间") from error
    except OSError as error:
        raise OCError(f"AI 请求失败：{error}") from error

    try:
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise OCError("AI 返回的内容不是预期的 Chat Completions 格式") from error
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content or "").strip()
    if not text:
        raise OCError("AI 返回了空内容")
    return text[:4000]