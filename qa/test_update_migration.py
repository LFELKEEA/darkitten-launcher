from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stellar.config as config_module
from stellar.config import Config, DEFAULT_UPDATE_URL


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise SystemExit(f"FAIL {name} {detail}")
    print(f"ok  {name} {detail}".rstrip())


def main() -> int:
    original = config_module.CONFIG_PATH
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config_module.CONFIG_PATH = path
            path.write_text(
                json.dumps(
                    {
                        "player_name": "Steve",
                        "update_url": "http://127.0.0.1:8765/update.json",
                    }
                ),
                encoding="utf-8",
            )
            cfg = Config.load()
            check("old_source_replaced", cfg.update_url == DEFAULT_UPDATE_URL, cfg.update_url)

            path.write_text(
                json.dumps({"update_url": "https://example.com/update.json"}),
                encoding="utf-8",
            )
            cfg = Config.load()
            check("custom_source_replaced", cfg.update_url == DEFAULT_UPDATE_URL, cfg.update_url)

            path.write_text(
                json.dumps({"update_url": DEFAULT_UPDATE_URL}),
                encoding="utf-8",
            )
            cfg = Config.load()
            check("official_source_kept", cfg.update_url == DEFAULT_UPDATE_URL, cfg.update_url)

            path.unlink()
            cfg = Config.load()
            check("default_url_used", cfg.update_url == DEFAULT_UPDATE_URL, cfg.update_url)
    finally:
        config_module.CONFIG_PATH = original
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
