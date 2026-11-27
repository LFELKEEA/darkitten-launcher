"""个性化配置与主题配色的单元测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import config as config_module  # noqa: E402
from darkitten.config import Config, THEME_PALETTES  # noqa: E402
from darkitten.ui import theme  # noqa: E402


class PersonalizationConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = Config()
        self.assertEqual(config.mod_page_size, 20)
        self.assertEqual(config.theme_palette, "sakura")
        self.assertEqual(config.background_image, "")
        self.assertEqual(config.background_mode, "cover")
        self.assertEqual(config.background_opacity, 28)
        self.assertTrue(config.launch_background)

    def _load_with(self, payload: dict) -> Config:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(config_module, "CONFIG_PATH", path):
                return Config.load()

    def test_page_size_is_clamped(self) -> None:
        self.assertEqual(self._load_with({"mod_page_size": 5}).mod_page_size, 10)
        self.assertEqual(self._load_with({"mod_page_size": 999}).mod_page_size, 100)
        self.assertEqual(self._load_with({"mod_page_size": "abc"}).mod_page_size, 20)

    def test_unknown_palette_falls_back(self) -> None:
        self.assertEqual(
            self._load_with({"theme_palette": "neon"}).theme_palette, "sakura"
        )
        self.assertEqual(
            self._load_with({"theme_palette": "ocean"}).theme_palette, "ocean"
        )

    def test_background_mode_is_validated(self) -> None:
        self.assertEqual(
            self._load_with({"background_mode": "zoom"}).background_mode, "cover"
        )
        self.assertEqual(
            self._load_with({"background_mode": "tile"}).background_mode, "tile"
        )

    def test_opacity_is_clamped(self) -> None:
        self.assertEqual(
            self._load_with({"background_opacity": -20}).background_opacity, 0
        )
        self.assertEqual(
            self._load_with({"background_opacity": 500}).background_opacity, 100
        )


class PaletteTests(unittest.TestCase):
    def tearDown(self) -> None:
        theme.set_current("dark", "sakura")

    def test_every_config_palette_exists_in_theme(self) -> None:
        for palette_id in THEME_PALETTES:
            self.assertIn(palette_id, theme.PALETTES)
            self.assertEqual(
                theme.PALETTES[palette_id]["name"], THEME_PALETTES[palette_id]
            )

    def test_palette_changes_accent(self) -> None:
        accents = set()
        for palette_id in theme.palette_ids():
            theme.set_current("dark", palette_id)
            accents.add(theme.colors()["accent"])
        self.assertEqual(len(accents), len(theme.palette_ids()))

    def test_unknown_palette_uses_sakura(self) -> None:
        theme.set_current("dark", "no-such-palette")
        self.assertEqual(theme.current_palette(), "sakura")
        self.assertEqual(theme.colors("dark", "sakura")["accent"], theme.colors()["accent"])

    def test_light_and_dark_keep_neutral_colors(self) -> None:
        theme.set_current("light", "ocean")
        light_bg = theme.colors()["bg"]
        theme.set_current("dark", "ocean")
        dark_bg = theme.colors()["bg"]
        self.assertNotEqual(light_bg, dark_bg)
        self.assertEqual(light_bg, theme.LIGHT["bg"])
        self.assertEqual(dark_bg, theme.DARK["bg"])

    def test_light_palette_variant_is_applied(self) -> None:
        self.assertNotEqual(
            theme.colors("light", "ocean")["accent"],
            theme.colors("dark", "ocean")["accent"],
        )


if __name__ == "__main__":
    unittest.main()
