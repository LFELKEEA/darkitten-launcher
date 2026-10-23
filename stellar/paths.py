from __future__ import annotations

from pathlib import Path


def version_game_dir(
    game_dir: Path | str, version_id: str, isolated: bool = True
) -> Path:
    """返回某个游戏版本实际使用的游戏目录。

    开启版本隔离后，每个版本以 versions/<版本名> 作为游戏工作目录，
    这样 mods、saves、config 等都会随版本分开存放。
    """
    root = Path(game_dir)
    if isolated and version_id:
        return root / "versions" / version_id
    return root


def version_mods_dir(
    game_dir: Path | str, version_id: str, isolated: bool = True
) -> Path:
    return version_game_dir(game_dir, version_id, isolated) / "mods"
