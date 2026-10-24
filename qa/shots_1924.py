from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QBuffer, QIODevice, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

import darkitten.config as config_module
import darkitten.skin_library as library
from darkitten.accounts import Account
from darkitten.config import Config

OUT = PROJECT / "qa"


def png_bytes(
    path: Path, color: str, width: int = 64, height: int = 64
) -> Path:
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(buffer.data()))
    return path


class FakeMain(QWidget):
    account_changed = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self._account = Account.offline(config.player_name)

    def current_account(self) -> Account:
        return self._account

    def toast(self, text: str, kind: str = "info") -> None:
        pass

    def open_path(self, path) -> None:
        pass

    def switch_page(self, page: str) -> None:
        pass


def main() -> None:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "library"
        library.LIBRARY_DIR = target
        library.INDEX_PATH = target / "index.json"
        local_copy = root / "local-skin.png"
        library.local_skin_path = lambda: local_copy  # type: ignore[assignment]
        library.save_local_skin = (  # type: ignore[assignment]
            lambda skin: (
                local_copy.parent.mkdir(parents=True, exist_ok=True),
                local_copy.write_bytes(skin.data),
                local_copy,
            )[-1]
        )
        config_module.CONFIG_PATH = root / "config.json"
        from darkitten.ui.pages import skin as skin_page_module

        skin_page_module.local_skin_path = lambda: local_copy  # type: ignore[assignment]

        cfg = Config(
            game_dir=str(root / "minecraft"),
            player_name="LFELKEEA",
        )
        main_window = FakeMain(cfg)
        page = skin_page_module.SkinPage(main_window)
        page.resize(1180, 900)
        page.show()
        app.processEvents()

        colors = ["#d95555", "#44aa88", "#8866cc", "#d9a441", "#5599dd"]
        names = ["恶魔猫猫", "薄荷狐", "夜莓", "焦糖猫", "深海"]
        first = png_bytes(root / "src" / f"{names[0]}.png", colors[0])
        page._apply_local_file(first)
        for name, color in zip(names[1:], colors[1:]):
            page._import_to_library(
                png_bytes(root / "src" / f"{name}.png", color)
            )
        app.processEvents()
        page.grab().save(str(OUT / "dkl_1924_skin_library.png"))

        second = next(
            item for item in library.list_skins() if item.name == names[1]
        )
        page._use_library_skin(second.id)
        page.variant_tabs.set_value("slim", emit=True)
        app.processEvents()
        page.grab().save(str(OUT / "dkl_1924_skin_offline_use.png"))
        print("saved", OUT / "dkl_1924_skin_library.png")
        print("saved", OUT / "dkl_1924_skin_offline_use.png")

        page.close()
        page.deleteLater()
        main_window.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
