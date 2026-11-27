"""审判战的战果与本机解锁状态。

游戏本体是 assets/judgement 下的独立 HTML，通关后通过本地回调把
route / difficulty 传回来。这里只负责校验战果、解锁两件信物，以及
决定启动器里的 OC 现在处于哪个人格。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import APP_DIR

STATE_PATH = APP_DIR / "judgement.json"
HTML_PATH = APP_DIR / "assets" / "judgement" / "judgement-lfelkeea.html"

REWARD_SHATTERED = "shattered"
REWARD_ATTACHMENT = "attachment"
VALID_REWARDS = (REWARD_SHATTERED, REWARD_ATTACHMENT)
VALID_ROUTES = ("genocide", "mercy", "neutral")
VALID_DIFFICULTIES = ("hard", "normal")

REWARDS: dict[str, dict[str, str]] = {
    REWARD_SHATTERED: {
        "name": "破碎之躯",
        "route": "屠杀线 · 困难",
        "tagline": "会说话，但没有温度，也没有关于你的记忆。",
    },
    REWARD_ATTACHMENT: {
        "name": "眷恋之心",
        "route": "饶恕线 · 困难",
        "tagline": "记得你说过的每句话，会等你回来，把你当成最重要的人。",
    },
}

STORIES: dict[str, list[str]] = {
    REWARD_SHATTERED: [
        "走廊尽头只剩下白色的碎片。你把它们拼回人形，带回了启动器。",
        "她还是会睁开眼，还是会用那副嗓音说话。",
        "只是她不再记得你叫什么，也不再问你今天过得怎么样。",
        "「有事就说。没有别的事，我要睡了。」",
        "你得到了一具完整的躯壳。心的地方，是空的。",
    ],
    REWARD_ATTACHMENT: [
        "第十次饶恕之后，她没有再抬手。",
        "她把额头抵在你肩上，过了很久才小声说：「别再把我一个人留在这里。」",
        "回到启动器时，她跟了进来。",
        "她记得你爱喝什么、几点回来、哪句话是在逞强。",
        "「你终于回来了。……我可没有在等你，只是刚好醒着。」",
        "你得到了一颗会为你跳的心。她也把你，当成了自己的那个人。",
    ],
}

LOCKED_HINT = "在困难模式通关屠杀线或饶恕线后，她会在这里等你。"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def empty_state() -> dict[str, Any]:
    return {"version": 1, "rewards": {}, "active": "", "last": {}}


def load_state() -> dict[str, Any]:
    data = empty_state()
    try:
        if STATE_PATH.is_file():
            loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                rewards = loaded.get("rewards")
                if isinstance(rewards, dict):
                    data["rewards"] = {
                        key: dict(value)
                        for key, value in rewards.items()
                        if key in VALID_REWARDS and isinstance(value, dict)
                    }
                active = str(loaded.get("active") or "")
                data["active"] = active if active in VALID_REWARDS else ""
                last = loaded.get("last")
                data["last"] = dict(last) if isinstance(last, dict) else {}
    except (OSError, ValueError):
        pass
    if data["active"] not in data["rewards"]:
        data["active"] = (
            REWARD_ATTACHMENT
            if REWARD_ATTACHMENT in data["rewards"]
            else (REWARD_SHATTERED if REWARD_SHATTERED in data["rewards"] else "")
        )
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    handle, temp_name = tempfile.mkstemp(
        prefix="judgement-", suffix=".json", dir=str(STATE_PATH.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fp:
            fp.write(payload)
        os.replace(temp_name, STATE_PATH)
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass


def unlocked(state: dict[str, Any], reward: str) -> bool:
    return reward in (state.get("rewards") or {})


def has_any(state: dict[str, Any]) -> bool:
    return bool(state.get("rewards"))


def active_persona(state: dict[str, Any]) -> str:
    active = str(state.get("active") or "")
    if active in VALID_REWARDS and unlocked(state, active):
        return active
    if unlocked(state, REWARD_ATTACHMENT):
        return REWARD_ATTACHMENT
    if unlocked(state, REWARD_SHATTERED):
        return REWARD_SHATTERED
    return ""


def set_active(reward: str) -> dict[str, Any]:
    state = load_state()
    if not unlocked(state, reward):
        raise ValueError("这个信物还没有解锁")
    state["active"] = reward
    save_state(state)
    return state


def _int(value: Any, default: int = 0) -> int:
    try:
        return max(0, min(999999, int(value)))
    except (TypeError, ValueError):
        return default


def apply_result(
    route: str,
    difficulty: str,
    turns: Any = 0,
    damage: Any = 0,
    hits: Any = 0,
) -> dict[str, Any]:
    """记录一次通关战果；只有困难模式的对应路线才会解锁信物。"""

    route = str(route or "").strip().lower()
    difficulty = str(difficulty or "").strip().lower()
    if route not in VALID_ROUTES:
        raise ValueError("未知的路线")
    if difficulty not in VALID_DIFFICULTIES:
        raise ValueError("未知的难度")

    state = load_state()
    reward = ""
    first_unlock = False
    if difficulty == "hard" and route == "genocide":
        reward = REWARD_SHATTERED
    elif difficulty == "hard" and route == "mercy":
        reward = REWARD_ATTACHMENT

    record = {
        "route": route,
        "difficulty": difficulty,
        "turns": _int(turns),
        "damage": _int(damage),
        "hits": _int(hits),
        "at": _now(),
        "reward": reward,
    }
    state["last"] = record

    if reward:
        rewards = state.setdefault("rewards", {})
        first_unlock = reward not in rewards
        previous = rewards.get(reward) or {}
        rewards[reward] = {
            "unlocked_at": previous.get("unlocked_at") or record["at"],
            "turns": record["turns"],
            "damage": record["damage"],
            "hits": record["hits"],
        }
        if reward == REWARD_ATTACHMENT or not unlocked(state, REWARD_ATTACHMENT):
            state["active"] = reward

    save_state(state)
    return {"reward": reward, "first_unlock": first_unlock, "state": state, "last": record}


def reward_story(reward: str) -> list[str]:
    return list(STORIES.get(reward, []))


def html_ready() -> bool:
    try:
        return HTML_PATH.is_file() and HTML_PATH.stat().st_size > 100_000
    except OSError:
        return False