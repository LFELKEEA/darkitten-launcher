"""OC 的对话、AI 接口配置，以及审判战入口。

审判战本体是 assets/judgement 下的独立 HTML。启动器在 127.0.0.1 上开一个
带随机 token 的一次性回调端口，游戏结束后把路线和难度发回来，再按战果
解锁「破碎之躯」或「眷恋之心」。
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontMetrics, QPixmap
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import judgement, oc
from ..config import APP_DIR
from . import icons, theme
from .dialogs import ConfirmDialog, NoticeDialog, StyledDialog
from .widgets import badge, repolish

PROFILE_DIR = APP_DIR / "cache" / "judgement-profile"
HOLD_NOTE = "通关困难模式的屠杀线或饶恕线后，她会在这里等你。"


# ----------------------------------------------------------------------
# 后台请求


class ChatWorker(QThread):
    ok = Signal(str)
    fail = Signal(str)

    def __init__(
        self,
        config: oc.OCConfig,
        messages: list[dict[str, str]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._messages = messages

    def run(self) -> None:
        try:
            text = oc.chat(self._config, self._messages)
        except oc.OCError as error:
            self.fail.emit(str(error))
        except Exception as error:  # 网络栈的异常类型很杂，界面只需要一句话
            self.fail.emit(f"请求失败：{error}")
        else:
            self.ok.emit(text)


# ----------------------------------------------------------------------
# 本地战果回调


class ResultServer(QObject):
    """只监听 127.0.0.1，只接受 /result，并且必须带对 token。"""

    result = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._accept)
        self._token = secrets.token_urlsafe(24)
        self._buffers: dict[QTcpSocket, bytearray] = {}
        self._done = False

    @property
    def token(self) -> str:
        return self._token

    @property
    def port(self) -> int:
        return self._server.serverPort()

    def start(self) -> bool:
        return self._server.listen(QHostAddress(QHostAddress.LocalHost), 0)

    def stop(self) -> None:
        self._done = True
        if self._server.isListening():
            self._server.close()
        for socket in list(self._buffers):
            try:
                socket.abort()
                socket.deleteLater()
            except RuntimeError:
                pass
        self._buffers.clear()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._drop(s))

    def _drop(self, socket: QTcpSocket) -> None:
        self._buffers.pop(socket, None)
        try:
            socket.deleteLater()
        except RuntimeError:
            pass

    def _read(self, socket: QTcpSocket) -> None:
        if self._done:
            return
        try:
            chunk = bytes(socket.readAll())
        except RuntimeError:
            return
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(chunk)
        if len(buffer) > 16384:
            self._respond(socket, 413, "Payload Too Large")
            return
        if b"\r\n\r\n" not in buffer:
            return

        head = bytes(buffer).split(b"\r\n\r\n", 1)[0]
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = request_line.split(" ")
        if len(parts) < 2:
            self._respond(socket, 400, "Bad Request")
            return
        target = parts[1]
        parsed = urllib.parse.urlsplit(target)
        if parsed.path != "/result":
            self._respond(socket, 404, "Not Found")
            return
        query = urllib.parse.parse_qs(parsed.query)
        token = (query.get("token") or [""])[0]
        if not token or not secrets.compare_digest(token, self._token):
            self._respond(socket, 403, "Forbidden")
            return

        payload = {
            key: (query.get(key) or [""])[0]
            for key in ("route", "diff", "turns", "damage", "hits")
        }
        self._respond(socket, 204, "No Content")
        self._done = True
        self.result.emit(payload)

    def _respond(self, socket: QTcpSocket, code: int, reason: str) -> None:
        head = (
            f"HTTP/1.1 {code} {reason}\r\n"
            "Content-Length: 0\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Connection: close\r\n\r\n"
        ).encode("latin-1")
        try:
            socket.write(head)
            socket.flush()
            socket.disconnectFromHost()
        except RuntimeError:
            pass


# ----------------------------------------------------------------------
# 小工具


def _set_badge(label: QLabel, text: str, kind: str) -> None:
    label.setText(text)
    label.setProperty("badge", kind)
    repolish(label)


def _browser_candidates() -> list[str]:
    found: list[str] = []
    override = os.environ.get("DARKITTEN_BROWSER", "").strip()
    if override:
        found.append(override)
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        path = shutil.which(name)
        if path:
            found.append(path)
    roots = [
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for root in roots:
        if not root:
            continue
        found.append(str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
        found.append(str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    unique: list[str] = []
    seen: set[str] = set()
    for item in found:
        key = os.path.normcase(item)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(item):
            unique.append(item)
    return unique


def _launch_browser(url: str) -> bool:
    try:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for exe in _browser_candidates():
        try:
            subprocess.Popen(
                [
                    exe,
                    f"--app={url}",
                    "--window-size=1280,1000",
                    f"--user-data-dir={PROFILE_DIR}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                close_fds=True,
                creationflags=flags,
            )
            return True
        except OSError:
            continue
    return QDesktopServices.openUrl(QUrl(url))


def _player_name(main) -> str:
    try:
        account = main.current_account()
        return str(account.name or "")
    except Exception:
        return ""


# ----------------------------------------------------------------------
# AI 接口设置


class OCConfigDialog(StyledDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, "AI 接口设置", "settings", width=520)
        self._saved = False
        self.config = oc.load_config()

        intro = QLabel(
            "需要一个兼容 OpenAI Chat Completions 的接口。Key 只会写进本机的 "
            "oc_ai.json，不会上传，也不会写进日志。"
        )
        intro.setObjectName("Tiny")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        self.base_edit = QLineEdit(self.config.base_url)
        self.base_edit.setPlaceholderText("https://api.openai.com/v1")
        self.model_edit = QLineEdit(self.config.model)
        self.model_edit.setPlaceholderText("gpt-4o-mini")
        self.key_edit = QLineEdit(self.config.api_key)
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-...")

        self._field("接口地址", self.base_edit, "填到 /v1 为止，也可以直接填完整的 /chat/completions。")

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        key_row.addWidget(self.key_edit, 1)
        self.show_key = QPushButton("显示")
        self.show_key.setObjectName("Ghost")
        self.show_key.setCheckable(True)
        self.show_key.setCursor(Qt.PointingHandCursor)
        self.show_key.toggled.connect(self._toggle_key)
        key_row.addWidget(self.show_key)
        self._field("API Key", key_row, "只保存在本机；换电脑或重装前记得备份。")

        self._field("模型", self.model_edit, "例如 gpt-4o-mini、deepseek-chat、moonshot-v1-8k。")

        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {theme.colors()['red']}; font-size: 11px;")
        self.error.setVisible(False)
        self.body.addWidget(self.error)

        self.add_button("取消", "Secondary", self.reject)
        self.add_button("保存", "Primary", self._save)
        self.adjustSize()

    def _field(self, title: str, widget, hint: str) -> None:
        label = QLabel(title)
        label.setObjectName("Tiny")
        self.body.addWidget(label)
        if isinstance(widget, QHBoxLayout):
            self.body.addLayout(widget)
        else:
            self.body.addWidget(widget)
        note = QLabel(hint)
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        self.body.addWidget(note)

    def _toggle_key(self, shown: bool) -> None:
        self.key_edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.show_key.setText("隐藏" if shown else "显示")

    def _save(self) -> None:
        base_url = self.base_edit.text().strip().rstrip("/")
        model = self.model_edit.text().strip()
        api_key = self.key_edit.text().strip()
        problem = ""
        if not base_url.startswith(("http://", "https://")):
            problem = "接口地址需要以 http:// 或 https:// 开头。"
        elif not model:
            problem = "模型名称不能为空。"
        elif not api_key:
            problem = "API Key 不能为空。"
        if problem:
            self.error.setText(problem)
            self.error.setVisible(True)
            return
        config = oc.OCConfig(
            base_url=base_url,
            model=model,
            api_key=api_key,
            temperature=self.config.temperature,
            timeout=self.config.timeout,
        )
        try:
            oc.save_config(config)
        except OSError as error:
            self.error.setText(f"保存失败：{error}")
            self.error.setVisible(True)
            return
        self.config = config
        self._saved = True
        self.accept()

    @property
    def saved(self) -> bool:
        return self._saved


# ----------------------------------------------------------------------
# 对话


class OCChatDialog(StyledDialog):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        state = judgement.load_state()
        persona = judgement.active_persona(state)
        super().__init__(parent, "LFELKEEA", "message", width=560)
        self.main = main
        self._state = state
        self.persona = persona
        self._both = judgement.unlocked(state, judgement.REWARD_SHATTERED) and judgement.unlocked(
            state, judgement.REWARD_ATTACHMENT
        )
        self._worker: ChatWorker | None = None
        self._pending: QWidget | None = None
        self._pending_text = ""
        self._busy = False
        self._build()
        self._refresh_header()
        self._greet()
        self.adjustSize()

    # -- 界面 ----------------------------------------------------------
    def _build(self) -> None:
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.persona_badge = badge("", "red")
        tools.addWidget(self.persona_badge)
        self.memory_label = QLabel("")
        self.memory_label.setObjectName("Tiny")
        tools.addWidget(self.memory_label)
        tools.addStretch(1)

        self.switch_btn = QPushButton("切换")
        self.switch_btn.setObjectName("Ghost")
        self.switch_btn.setCursor(Qt.PointingHandCursor)
        self.switch_btn.clicked.connect(self._switch_persona)
        self.switch_btn.setVisible(self._both)
        tools.addWidget(self.switch_btn)

        settings_btn = QPushButton("接口设置")
        settings_btn.setObjectName("Ghost")
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.clicked.connect(self._open_config)
        tools.addWidget(settings_btn)

        self.clear_btn = QPushButton("清空记忆")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear_memory)
        self.clear_btn.setVisible(self.persona == judgement.REWARD_ATTACHMENT)
        tools.addWidget(self.clear_btn)
        self.body.addLayout(tools)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setMinimumHeight(360)
        self.scroll.setMaximumHeight(420)
        self.chat_host = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_host)
        self.chat_layout.setContentsMargins(2, 2, 2, 2)
        self.chat_layout.setSpacing(8)
        self.chat_layout.addStretch(1)
        self.scroll.setWidget(self.chat_host)
        self.body.addWidget(self.scroll, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("和她说点什么…")
        self.input.returnPressed.connect(self._send)
        input_row.addWidget(self.input, 1)
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("Primary")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setMinimumWidth(72)
        self.send_btn.clicked.connect(self._send)
        input_row.addWidget(self.send_btn)
        self.body.addLayout(input_row)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.add_button("关闭", "Secondary", self.reject)

    def _refresh_header(self) -> None:
        attached = self.persona == judgement.REWARD_ATTACHMENT
        label = "眷恋之心" if attached else "破碎之躯"
        _set_badge(self.persona_badge, label, "red" if attached else "gray")
        self.title_label.setText(f"LFELKEEA · {label}")
        self.switch_btn.setText("切换人格")
        self.switch_btn.setToolTip(
            "切换为破碎之躯" if attached else "切换为眷恋之心"
        )
        self.clear_btn.setVisible(attached)
        self._refresh_memory()

    def _refresh_memory(self) -> None:
        if self.persona == judgement.REWARD_ATTACHMENT:
            memory = oc.load_memory()
            count = len(memory.get("messages") or [])
            chats = int(memory.get("chats") or 0)
            self.memory_label.setText(
                f"记得 {count // 2} 轮对话 · 聊过 {chats} 次" if count else "还没有留下记忆"
            )
        else:
            self.memory_label.setText("无感情 · 无记忆")

    def _greet(self) -> None:
        if self.persona == judgement.REWARD_ATTACHMENT:
            memory = oc.load_memory()
            if memory.get("chats"):
                self._add_bubble("你回来了。……我可没有一直盯着门口，只是刚好醒着。")
            else:
                self._add_bubble(
                    "……你终于来了。我是 LFELKEEA。\n别误会，我只是刚好在这里。"
                )
        else:
            self._add_bubble("有事就说。")

    def _add_bubble(self, text: str, mine: bool = False, pending: bool = False) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(0)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        metrics = QFontMetrics(label.font())
        longest = max(
            (metrics.horizontalAdvance(part) for part in text.split("\n")),
            default=0,
        )
        label.setFixedWidth(min(390, max(170, longest + 34)))
        colors = theme.colors()
        if mine:
            label.setStyleSheet(
                f"background: {colors['accent']}; color: #FFFFFF;"
                " border-radius: 12px; padding: 8px 12px;"
            )
            line.addStretch(1)
            line.addWidget(label)
        elif pending:
            label.setStyleSheet(
                f"background: {colors['card2']}; color: {colors['muted']};"
                f" border: 1px dashed {colors['border_strong']};"
                " border-radius: 12px; padding: 8px 12px;"
            )
            line.addWidget(label)
            line.addStretch(1)
        else:
            label.setStyleSheet(
                f"background: {colors['accent_soft']}; color: {colors['text']};"
                f" border: 1px solid {colors['accent_border']};"
                " border-radius: 12px; padding: 8px 12px;"
            )
            line.addWidget(label)
            line.addStretch(1)
        # 插在 stretch 之前，保证气泡从上往下排。
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, row)
        QTimer.singleShot(0, self._scroll_bottom)
        return row

    def _add_notice(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("Tiny")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, label)
        QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # -- 发送 ----------------------------------------------------------
    def _send(self) -> None:
        if self._busy:
            return
        text = self.input.text().strip()
        if not text:
            return
        config = oc.load_config()
        if not config.ready:
            self._add_notice("还没有配置 AI 接口。点右上角「接口设置」填入 API Key。")
            self._open_config()
            return
        self.input.clear()
        self._pending_text = text
        self._add_bubble(text, mine=True)
        self._pending = self._add_bubble("……", pending=True)
        memory = oc.load_memory()
        messages = oc.build_messages(
            self.persona,
            _player_name(self.main),
            memory,
            text,
        )
        self._set_busy(True)
        self._worker = ChatWorker(config, messages, self)
        self._worker.ok.connect(self._on_reply)
        self._worker.fail.connect(self._on_fail)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_btn.setEnabled(not busy)
        self.input.setEnabled(not busy)
        self.status.setText("她正在输入…" if busy else "")
        if not busy:
            self.input.setFocus()

    def _drop_pending(self) -> None:
        if self._pending is not None:
            self._pending.setParent(None)
            self._pending.deleteLater()
            self._pending = None

    def _on_reply(self, text: str) -> None:
        self._worker = None
        self._drop_pending()
        self._add_bubble(text)
        if self.persona == judgement.REWARD_ATTACHMENT:
            try:
                oc.remember(_player_name(self.main), self._pending_text, text)
            except OSError:
                pass
        self._refresh_memory()
        self._set_busy(False)

    def _on_fail(self, message: str) -> None:
        self._worker = None
        self._drop_pending()
        self._add_notice(f"没有收到回复：{message}")
        self._set_busy(False)

    # -- 工具 ----------------------------------------------------------
    def _open_config(self) -> None:
        dialog = OCConfigDialog(self)
        if dialog.exec() and dialog.saved:
            self.status.setText("接口已保存。")
        self.input.setFocus()

    def _clear_memory(self) -> None:
        confirm = ConfirmDialog(
            self,
            "清空记忆",
            "她会忘记你们之前聊过的一切。这个操作不能撤销。",
            "清空",
            "取消",
            "warning",
        )
        if not confirm.exec():
            return
        oc.clear_memory()
        self._refresh_memory()
        self._add_notice("记忆已清空。")

    def _switch_persona(self) -> None:
        if not self._both:
            return
        target = (
            judgement.REWARD_SHATTERED
            if self.persona == judgement.REWARD_ATTACHMENT
            else judgement.REWARD_ATTACHMENT
        )
        try:
            judgement.set_active(target)
        except ValueError:
            return
        self.persona = target
        self._refresh_header()
        if target == judgement.REWARD_ATTACHMENT:
            self._add_notice("她抬起眼，像是终于认出了你。")
        else:
            self._add_notice("她的眼神重新变得空白。")

    def reject(self) -> None:
        # QThread 不能在运行中被销毁；等这一句说完再允许关闭。
        if self._worker is not None and self._worker.isRunning():
            self.status.setText("等她说完这一句再关闭。")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            self.status.setText("等她说完这一句再关闭。")
            return
        super().closeEvent(event)


# ----------------------------------------------------------------------
# 战果弹窗


class JudgementResultDialog(StyledDialog):
    def __init__(self, parent: QWidget | None, applied: dict) -> None:
        reward = str(applied.get("reward") or "")
        state = applied.get("state") or {}
        last = applied.get("last") or {}
        info = judgement.REWARDS.get(reward)
        title = info["name"] if info else "审判战结束"
        super().__init__(parent, title, "heart" if reward else "bolt", width=540)
        self.chat_requested = False

        if info:
            route = QLabel(f"{info['route']} · 新信物" if applied.get("first_unlock") else info["route"])
            route.setObjectName("Tiny")
            self.body.addWidget(route)
            for line in judgement.reward_story(reward):
                text = QLabel(line)
                text.setObjectName("DialogText")
                text.setWordWrap(True)
                self.body.addWidget(text)
        else:
            text = QLabel(
                "她重新站直了身子，像什么都没发生过。\n"
                "只有困难模式的屠杀线或饶恕线，才会留下信物。"
            )
            text.setObjectName("DialogText")
            text.setWordWrap(True)
            self.body.addWidget(text)

        stats = QFrame()
        stats.setObjectName("SoftCard")
        stats.setAttribute(Qt.WA_StyledBackground, True)
        stats_layout = QHBoxLayout(stats)
        stats_layout.setContentsMargins(12, 8, 12, 8)
        stats_layout.setSpacing(16)
        for label, value in (
            ("回合", last.get("turns", 0)),
            ("承受伤害", last.get("damage", 0)),
            ("被击中", last.get("hits", 0)),
        ):
            item = QLabel(f"{label}  {value}")
            item.setObjectName("Tiny")
            stats_layout.addWidget(item)
        stats_layout.addStretch(1)
        self.body.addWidget(stats)

        if reward:
            has_any = judgement.has_any(state)
            if has_any:
                self.chat_btn = self.add_button("去和她说话", "Primary", self._chat)
            self.add_button("稍后", "Secondary", self.reject)
        else:
            self.add_button("知道了", "Secondary", self.reject)

    def _chat(self) -> None:
        self.chat_requested = True
        self.accept()


# ----------------------------------------------------------------------
# 审判战入口


class JudgementDialog(StyledDialog):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent, "审判战 · LFELKEEA", "bolt", width=620)
        self.setModal(False)
        self.main = main
        self._server: ResultServer | None = None
        self._playing = False
        self._build()
        self._refresh()
        self.adjustSize()

        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(4 * 60 * 60 * 1000)
        self._deadline.timeout.connect(self._on_timeout)

    # -- 界面 ----------------------------------------------------------
    def _build(self) -> None:
        hero = QFrame()
        hero.setObjectName("SoftCard")
        hero.setAttribute(Qt.WA_StyledBackground, True)
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(14, 12, 14, 12)
        hero_layout.setSpacing(14)

        art = QLabel()
        art.setFixedSize(72, 72)
        art.setAlignment(Qt.AlignCenter)
        art.setStyleSheet(
            f"background: {theme.colors()['accent_soft']}; border-radius: 18px;"
        )
        image_path = icons.ASSETS_DIR / "chibi_face.png"
        if image_path.is_file():
            art.setPixmap(
                QPixmap(str(image_path)).scaled(
                    64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        else:
            art.setPixmap(icons.pixmap("paw", theme.colors()["accent"], 32))
        hero_layout.addWidget(art)

        texts = QVBoxLayout()
        texts.setSpacing(4)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name = QLabel("LFELKEEA")
        name.setObjectName("H2")
        name_row.addWidget(name)
        self.state_badge = badge("", "gray")
        name_row.addWidget(self.state_badge)
        name_row.addStretch(1)
        texts.addLayout(name_row)
        intro = QLabel("恶魔猫娘，走廊尽头的审判者。右键头像直接对话，右键长按可随时打开这里。")
        intro.setObjectName("Tiny")
        intro.setWordWrap(True)
        texts.addWidget(intro)
        hero_layout.addLayout(texts, 1)
        self.body.addWidget(hero)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.reward_badges: dict[str, QLabel] = {}
        for reward in (judgement.REWARD_SHATTERED, judgement.REWARD_ATTACHMENT):
            card = QFrame()
            card.setObjectName("SoftCard")
            card.setAttribute(Qt.WA_StyledBackground, True)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(5)
            head = QHBoxLayout()
            head.setSpacing(6)
            icon = QLabel()
            icon.setPixmap(
                icons.pixmap(
                    "heart" if reward == judgement.REWARD_ATTACHMENT else "sparkles",
                    theme.colors()["accent"],
                    16,
                )
            )
            head.addWidget(icon)
            title = QLabel(judgement.REWARDS[reward]["name"])
            title.setObjectName("H2")
            head.addWidget(title)
            head.addStretch(1)
            status = badge("未解锁", "gray")
            self.reward_badges[reward] = status
            head.addWidget(status)
            card_layout.addLayout(head)
            route = QLabel(judgement.REWARDS[reward]["route"])
            route.setObjectName("Tiny")
            card_layout.addWidget(route)
            tagline = QLabel(judgement.REWARDS[reward]["tagline"])
            tagline.setObjectName("Tiny")
            tagline.setWordWrap(True)
            card_layout.addWidget(tagline)
            cards.addWidget(card, 1)
        self.body.addLayout(cards)

        self.story_box = QFrame()
        self.story_box.setObjectName("SoftCard")
        self.story_box.setAttribute(Qt.WA_StyledBackground, True)
        story_layout = QVBoxLayout(self.story_box)
        story_layout.setContentsMargins(14, 10, 14, 10)
        story_layout.setSpacing(5)
        story_title = QLabel("结局剧情")
        story_title.setObjectName("H2")
        story_layout.addWidget(story_title)
        self.story_label = QLabel("")
        self.story_label.setObjectName("DialogText")
        self.story_label.setWordWrap(True)
        story_layout.addWidget(self.story_label)
        self.body.addWidget(self.story_box)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.config_btn = self.add_button("AI 接口设置", "Ghost", self._open_config)
        self.chat_btn = self.add_button("和她说话", "Secondary", self._open_chat)
        self.stop_btn = self.add_button("停止等待", "Ghost", self._stop_session)
        self.start_btn = self.add_button("开始审判", "Primary", self._start)

    def _refresh(self) -> None:
        state = judgement.load_state()
        rewards = state.get("rewards") or {}
        for reward, status in self.reward_badges.items():
            if reward in rewards:
                _set_badge(status, "已解锁", "green" if reward == judgement.REWARD_SHATTERED else "red")
            else:
                _set_badge(status, "未解锁", "gray")
        active = judgement.active_persona(state)
        if len(rewards) >= 2:
            _set_badge(self.state_badge, "两件信物", "purple")
        elif rewards:
            _set_badge(self.state_badge, judgement.REWARDS[active]["name"], "red" if active == judgement.REWARD_ATTACHMENT else "gray")
        else:
            _set_badge(self.state_badge, "尚未解锁", "gray")

        lines: list[str] = []
        for reward in (judgement.REWARD_SHATTERED, judgement.REWARD_ATTACHMENT):
            if reward not in rewards:
                continue
            if lines:
                lines.append("")
            lines.append(f"【{judgement.REWARDS[reward]['name']}】")
            lines.extend(judgement.reward_story(reward))
        self.story_label.setText("\n".join(lines) if lines else judgement.LOCKED_HINT)
        self.story_box.setVisible(True)

        has_any = judgement.has_any(state)
        self.chat_btn.setEnabled(has_any)
        self.chat_btn.setToolTip("" if has_any else "先在困难模式的审判战里带回一件信物")
        self.stop_btn.setVisible(self._playing)
        self.start_btn.setEnabled(not self._playing and judgement.html_ready())
        if self._playing:
            self.start_btn.setText("审判进行中…")
            self.status.setText(
                "审判战已在独立窗口打开。打完会自己把战果送回来，不用关闭这个窗口。"
            )
        elif not judgement.html_ready():
            self.start_btn.setText("缺少游戏文件")
            self.status.setText("没有找到 assets/judgement/judgement-lfelkeea.html，请重新安装启动器。")
        else:
            self.start_btn.setText("开始审判" if not has_any else "再打一次")
            self.status.setText(
                "困难模式的屠杀线给「破碎之躯」，饶恕线需要看完所有互动并饶恕十次，给「眷恋之心」。"
            )

    # -- 开战 ----------------------------------------------------------
    def _start(self) -> None:
        if self._playing:
            return
        if not judgement.html_ready():
            NoticeDialog(
                self,
                "缺少审判战文件",
                "assets/judgement/judgement-lfelkeea.html 不存在或不完整，请重新安装启动器。",
                "知道了",
                "warning",
            ).exec()
            return

        server = ResultServer(self)
        if not server.start():
            NoticeDialog(
                self,
                "无法接收战果",
                "本机端口被占用，启动器没法接收这场战斗的结果。请稍后再试。",
                "知道了",
                "warning",
            ).exec()
            server.deleteLater()
            return
        server.result.connect(self._on_result)
        self._server = server

        callback = f"http://127.0.0.1:{server.port}/result"
        url = QUrl.fromLocalFile(str(judgement.HTML_PATH))
        url.setQuery(
            urllib.parse.urlencode({"cb": callback, "token": server.token})
        )
        if not _launch_browser(url.toString()):
            server.stop()
            server.deleteLater()
            self._server = None
            NoticeDialog(
                self,
                "打不开审判战",
                "没有找到 Microsoft Edge 或 Google Chrome。安装其中一个后再试，"
                "或者把默认浏览器设为它们。",
                "知道了",
                "warning",
            ).exec()
            return

        self._playing = True
        self._deadline.start()
        self._refresh()

    def _on_result(self, payload: dict) -> None:
        self._deadline.stop()
        if self._server is not None:
            self._server.stop()
            self._server.deleteLater()
            self._server = None
        self._playing = False

        route = str(payload.get("route") or "")
        difficulty = "hard" if str(payload.get("diff") or "") == "hard" else "normal"
        try:
            applied = judgement.apply_result(
                route,
                difficulty,
                payload.get("turns"),
                payload.get("damage"),
                payload.get("hits"),
            )
        except ValueError as error:
            self._refresh()
            NoticeDialog(self, "战果无法识别", str(error), "知道了", "warning").exec()
            return

        self._refresh()
        result = JudgementResultDialog(self, applied)
        result.exec()
        if result.chat_requested:
            self._open_chat()

    def _on_timeout(self) -> None:
        if not self._playing:
            return
        self._stop_session()
        self.status.setText("等了很久也没有收到战果，先停止等待了。可以重新开始。")

    def _stop_session(self) -> None:
        self._deadline.stop()
        if self._server is not None:
            self._server.stop()
            self._server.deleteLater()
            self._server = None
        self._playing = False
        self._refresh()

    # -- 其他入口 ------------------------------------------------------
    def _open_chat(self) -> None:
        self.main.open_oc_chat()

    def _open_config(self) -> None:
        self.main.open_oc_config()

    def reject(self) -> None:
        if self._playing:
            confirm = ConfirmDialog(
                self,
                "审判战还在进行",
                "现在关闭会停止接收战果，游戏窗口仍然会开着。确定要离开吗？",
                "停止等待并关闭",
                "继续等待",
                "warning",
            )
            if not confirm.exec():
                return
            self._stop_session()
        super().reject()

    def closeEvent(self, event) -> None:
        if self._playing:
            event.ignore()
            self.reject()
            return
        self._deadline.stop()
        if self._server is not None:
            self._server.stop()
            self._server.deleteLater()
            self._server = None
        super().closeEvent(event)