from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .config import APP_NAME

ACTIVATE_MESSAGE = b"darkitten:activate"
ACK_MESSAGE = b"darkitten:ack"
_SERVER_PREFIX = "darkitten-launcher-"
_CONNECT_TIMEOUT_MS = 500
_ACK_TIMEOUT_MS = 900


def default_server_name() -> str:
    """返回当前用户独占的本地服务名。

    只用用户名和用户目录生成，不绑定安装路径：同一个用户无论从桌面
    快捷方式、安装目录还是源码目录启动，都归到同一个启动器实例。
    """
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    identity = "|".join(
        (
            APP_NAME,
            os.environ.get("USERNAME") or os.environ.get("USER") or "",
            profile.casefold(),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{_SERVER_PREFIX}{digest}"


class SingleInstanceGuard(QObject):
    """基于本地套接字的单实例守卫。

    第一个进程监听本地服务；后续进程连接成功后发送激活消息并立即退出，
    由已有进程把窗口从托盘或最小化状态拉回前台。
    """

    activated = Signal()

    def __init__(
        self, server_name: str = "", parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._name = str(server_name or "").strip() or default_server_name()
        self._server: QLocalServer | None = None
        self._primary = False
        self._pending_activation = False

    @property
    def server_name(self) -> str:
        return self._name

    @property
    def is_primary(self) -> bool:
        return self._primary

    @property
    def has_pending_activation(self) -> bool:
        return self._pending_activation

    def acquire(self) -> bool:
        """尝试成为唯一实例。

        返回 True 表示当前进程应该继续运行；返回 False 表示已经通知
        已有实例并应当立刻退出。
        """
        if self._activate_existing():
            return False
        for attempt in range(3):
            if self._listen():
                self._primary = True
                return True
            # 两个进程同时启动时，监听可能刚好被对方抢走，再确认一次。
            if self._activate_existing():
                return False
            if attempt < 2:
                time.sleep(0.1)
        # 本地套接字不可用时不能让用户完全打不开启动器，退化为普通启动。
        return True

    def flush_pending_activation(self) -> None:
        """把窗口建好之前收到的激活请求补发一次。"""
        if not self._pending_activation:
            return
        self._pending_activation = False
        self.activated.emit()

    def close(self) -> None:
        server = self._server
        self._server = None
        self._primary = False
        if server is not None:
            server.close()
            server.deleteLater()
            QLocalServer.removeServer(self._name)

    # ------------------------------------------------------------------
    # 内部实现

    def _activate_existing(self) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(self._name)
        if not socket.waitForConnected(_CONNECT_TIMEOUT_MS):
            socket.abort()
            return False
        try:
            socket.write(ACTIVATE_MESSAGE)
            socket.flush()
            socket.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
            # 等已有实例确认收到再退出，避免第二个进程走了但窗口没被拉起。
            socket.waitForReadyRead(_ACK_TIMEOUT_MS)
            socket.disconnectFromServer()
            if socket.state() != QLocalSocket.UnconnectedState:
                socket.waitForDisconnected(200)
        finally:
            socket.deleteLater()
        return True

    def _listen(self) -> bool:
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.UserAccessOption)
        server.newConnection.connect(self._accept_connections)
        if not server.listen(self._name):
            server.deleteLater()
            return False
        self._server = server
        return True

    def _accept_connections(self) -> None:
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                break
            socket.setParent(self)
            socket.readyRead.connect(lambda s=socket: self._read_socket(s))
            socket.disconnected.connect(socket.deleteLater)
            self._read_socket(socket)

    def _read_socket(self, socket: QLocalSocket) -> None:
        try:
            data = bytes(socket.readAll())
        except RuntimeError:
            return
        if not data:
            return
        buffer = bytes(socket.property("dkl-buffer") or b"") + data
        if len(buffer) > 4096:
            buffer = buffer[-4096:]
        socket.setProperty("dkl-buffer", buffer)
        if ACTIVATE_MESSAGE in buffer and not bool(
            socket.property("dkl-activated")
        ):
            socket.setProperty("dkl-activated", True)
            self._pending_activation = True
            self.activated.emit()
        try:
            socket.write(ACK_MESSAGE)
            socket.flush()
        except RuntimeError:
            pass
