from __future__ import annotations

import json
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
from darkitten.skins import load_skin_file

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")
    return condition


def png_bytes(
    path: Path, width: int = 64, height: int = 64, color: str = "#e84545"
) -> Path:
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(buffer.data()))
    return path


def use_library_dir(root: Path) -> None:
    target = root / "skins" / "library"
    library.LIBRARY_DIR = target
    library.INDEX_PATH = target / "index.json"


def backend_tests(root: Path) -> None:
    use_library_dir(root)
    local_copy = root / "skins" / "local-skin.png"
    library.local_skin_path = lambda: local_copy  # type: ignore[assignment]
    library.save_local_skin = (  # type: ignore[assignment]
        lambda skin: (local_copy.parent.mkdir(parents=True, exist_ok=True),
                      local_copy.write_bytes(skin.data), local_copy)[-1]
    )

    first_png = png_bytes(root / "src" / "Cat.png")
    second_png = png_bytes(root / "src" / "Fox.png", color="#3fb3aa")
    first = load_skin_file(first_png)
    second = load_skin_file(second_png)

    entry_a = library.add_skin(first, name="Cat", variant="slim", source=str(first_png))
    check("add_writes_file", entry_a.path.is_file() and entry_a.variant == "slim")
    check("index_exists", library.INDEX_PATH.is_file())

    duplicate = library.add_skin(
        first, name="Cat renamed", variant="classic", source=str(first_png)
    )
    check("dedupe_same_hash", duplicate.id == entry_a.id)
    check("dedupe_renames", duplicate.name == "Cat renamed" and len(library.list_skins()) == 1)

    entry_b = library.import_path(second_png, name="Fox", variant="classic")
    check("import_second", len(library.list_skins()) == 2 and entry_b.path.is_file())
    check("get_skin", library.get_skin(entry_b.id) is not None)

    renamed = library.rename_skin(entry_b.id, "  Fox   Girl  ")
    check("rename_clean", renamed is not None and renamed.name == "Fox Girl")

    slimmed = library.set_variant(entry_b.id, "slim")
    check("set_variant", slimmed is not None and slimmed.variant == "slim")
    collapsed = library.set_variant(entry_b.id, "weird")
    check("invalid_variant_falls_back", collapsed is not None and collapsed.variant == "classic")

    preview = library.sync_preview_copy(entry_b)
    check(
        "sync_preview",
        preview.is_file() and preview.read_bytes() == second_png.read_bytes(),
    )
    check("path_matches", library.path_matches(entry_b, entry_b.path))
    check("path_mismatch", not library.path_matches(entry_b, entry_a.path))

    legacy_png = png_bytes(root / "src" / "Legacy.png", 64, 32, "#8a5a45")
    legacy_entry = library.import_path(legacy_png, name="Legacy", variant="slim")
    check(
        "legacy_forces_classic",
        legacy_entry.legacy and legacy_entry.variant == "classic",
    )

    before = {item.id for item in library.list_skins()}
    ok, message = library.delete_skin(legacy_entry.id, to_recycle_bin=False)
    after = {item.id for item in library.list_skins()}
    check(
        "delete_entry",
        ok and legacy_entry.id not in after and before - after == {legacy_entry.id},
        message,
    )

    # 索引中的路径穿越必须被忽略
    INDEX_PATH = library.INDEX_PATH
    INDEX_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "id": "deadbeefdeadbeef",
                        "name": "evil",
                        "file": "../evil.png",
                        "hash": "x",
                        "variant": "classic",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    check("reject_path_traversal", library.list_skins() == [])

    # 旧版单文件皮肤迁移
    legacy_root = root / "migrate"
    use_library_dir(legacy_root)
    legacy_source = png_bytes(legacy_root / "legacy.png", color="#8866cc")
    library.local_skin_path = lambda: legacy_source  # type: ignore[assignment]
    library.save_local_skin = (  # type: ignore[assignment]
        lambda skin: legacy_source.write_bytes(skin.data) and legacy_source
    )
    config_module.CONFIG_PATH = legacy_root / "config.json"
    cfg = Config(game_dir=str(legacy_root / "minecraft"))
    cfg.skin_path = str(legacy_source)
    cfg.skin_variant = "slim"
    entries = library.ensure_library(cfg)
    migrated = next((item for item in entries if item.path.is_file()), None)
    check(
        "migration_added",
        migrated is not None and len(entries) == 1,
        f"entries={len(entries)}",
    )
    check(
        "migration_repoints_config",
        Path(cfg.skin_path) == migrated.path if migrated else False,
    )
    check(
        "migration_keeps_variant",
        migrated is not None and cfg.skin_variant == migrated.variant,
    )


class FakeMain(QWidget):
    account_changed = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.messages: list[tuple[str, str]] = []
        self.pages: list[str] = []
        self.opened: list[object] = []
        self._account = Account.offline(config.player_name)

    def current_account(self) -> Account:
        return self._account

    def toast(self, text: str, kind: str = "info") -> None:
        self.messages.append((text, kind))

    def open_path(self, path) -> None:
        self.opened.append(path)

    def switch_page(self, page: str) -> None:
        self.pages.append(page)


def ui_tests(root: Path, app: QApplication) -> None:
    from darkitten.ui.pages import skin as skin_page_module
    from darkitten.ui.pages.skin import SkinPage

    use_library_dir(root / "ui")
    local_copy = root / "ui" / "skins" / "local-skin.png"
    library.local_skin_path = lambda: local_copy  # type: ignore[assignment]
    library.save_local_skin = (  # type: ignore[assignment]
        lambda skin: (local_copy.parent.mkdir(parents=True, exist_ok=True),
                      local_copy.write_bytes(skin.data), local_copy)[-1]
    )
    skin_page_module.local_skin_path = lambda: local_copy  # type: ignore[assignment]
    config_module.CONFIG_PATH = root / "ui" / "config.json"

    class FakeConfirm:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def exec(self) -> bool:
            return True

    skin_page_module.ConfirmDialog = FakeConfirm  # type: ignore[assignment]
    skin_page_module.delete_skin = (  # type: ignore[assignment]
        lambda entry_id, **_kwargs: library.delete_skin(
            entry_id, to_recycle_bin=False
        )
    )

    cfg = Config(game_dir=str(root / "ui" / "minecraft"), player_name="Tester")
    main = FakeMain(cfg)
    page = SkinPage(main)
    app.processEvents()

    check("ui_initial_empty", page.library_grid.count() == 0)
    check("ui_default_preview", page._skin_file is None)

    first_png = png_bytes(root / "ui" / "Cat.png", color="#d95555")
    page._apply_local_file(first_png)
    app.processEvents()
    check(
        "ui_apply_sets_skin",
        page._skin_file is not None and cfg.skin_path.endswith(".png"),
        cfg.skin_path,
    )
    check("ui_library_one", page.library_grid.count() == 1)
    check("ui_offline_badge", page.account_badge.text() == "离线皮肤")
    check("ui_offline_label", "离线皮肤" in page.current_label.text())
    check(
        "ui_status_mentions_library",
        "皮肤库" in page.status_label.text() or "启动" in page.status_label.text(),
        page.status_label.text(),
    )

    second_png = png_bytes(root / "ui" / "Fox.png", color="#44aa88")
    page._import_to_library(second_png)
    app.processEvents()
    check("ui_library_two", page.library_grid.count() == 2)

    active = next(item for item in library.list_skins() if library.path_matches(item, cfg.skin_path))
    page.variant_tabs.set_value("slim", emit=True)
    app.processEvents()
    refreshed = library.get_skin(active.id)
    check(
        "ui_variant_updates_entry",
        refreshed is not None and refreshed.variant == "slim" and cfg.skin_variant == "slim",
    )

    page._set_variant_silently("classic")
    app.processEvents()
    refreshed = library.get_skin(active.id)
    check(
        "ui_programmatic_variant_no_mutation",
        refreshed is not None and refreshed.variant == "slim",
    )

    page._use_library_skin(active.id)
    app.processEvents()
    check(
        "ui_use_entry",
        page._skin_file is not None and "已启用" in page.current_label.text(),
    )

    # 重命名依赖 QInputDialog，自动测试中直接验证后端，不弹交互框。
    page._delete_library_skin(active.id)
    app.processEvents()
    check("ui_delete_card", page.library_grid.count() == 1)
    check(
        "ui_delete_clears_active",
        cfg.skin_path == "" and page._skin_file is None,
    )

    page.close()
    page.deleteLater()
    main.close()
    main.deleteLater()
    app.processEvents()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backend_tests(root)
        ui_tests(root, app)
    print(f"\nskin_library: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
