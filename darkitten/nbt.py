"""极简 NBT 读写，用于离线修改 Minecraft 的 level.dat / servers.dat。

只依赖标准库。读取时保留原标签类型，写入时按原有类型回写，
避免把 byte 写成 int 之类导致游戏读档异常。
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path
from typing import Any

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class NBTError(ValueError):
    pass


class Byte(int):
    """有符号 8 位整数。"""


class Short(int):
    """有符号 16 位整数。"""


class Int(int):
    """有符号 32 位整数。"""


class Long(int):
    """有符号 64 位整数。"""


class Float(float):
    """32 位浮点数。"""


class Double(float):
    """64 位浮点数。"""


class NBTString(str):
    """NBT 字符串（Java modified UTF-8）。"""


class ByteArray(list):
    """TAG_Byte_Array，元素是 -128..127。"""


class IntArray(list):
    """TAG_Int_Array。"""


class LongArray(list):
    """TAG_Long_Array。"""


class NBTList(list):
    """TAG_List，保留元素类型以便空列表也能正确回写。"""

    def __init__(self, iterable=(), element_type: int | None = None) -> None:
        super().__init__(iterable)
        self.element_type = element_type


# ---------------------------------------------------------------------------
# Java modified UTF-8


def _decode_modified_utf8(data: bytes) -> str:
    codes: list[int] = []
    index = 0
    size = len(data)
    while index < size:
        first = data[index]
        if first < 0x80:
            codes.append(first)
            index += 1
        elif first & 0xE0 == 0xC0 and index + 1 < size:
            codes.append(((first & 0x1F) << 6) | (data[index + 1] & 0x3F))
            index += 2
        elif first & 0xF0 == 0xE0 and index + 2 < size:
            codes.append(
                ((first & 0x0F) << 12)
                | ((data[index + 1] & 0x3F) << 6)
                | (data[index + 2] & 0x3F)
            )
            index += 3
        elif first & 0xF8 == 0xF0 and index + 3 < size:
            codes.append(
                ((first & 0x07) << 18)
                | ((data[index + 1] & 0x3F) << 12)
                | ((data[index + 2] & 0x3F) << 6)
                | (data[index + 3] & 0x3F)
            )
            index += 4
        else:
            raise NBTError("字符串包含无法识别的 UTF-8 字节")

    chars: list[str] = []
    index = 0
    while index < len(codes):
        code = codes[index]
        if 0xD800 <= code <= 0xDBFF and index + 1 < len(codes):
            low = codes[index + 1]
            if 0xDC00 <= low <= 0xDFFF:
                chars.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                index += 2
                continue
        chars.append(chr(code))
        index += 1
    return "".join(chars)


def _encode_modified_utf8(text: str) -> bytes:
    out = bytearray()

    def write_three(code: int) -> None:
        out.append(0xE0 | (code >> 12))
        out.append(0x80 | ((code >> 6) & 0x3F))
        out.append(0x80 | (code & 0x3F))

    for char in text:
        code = ord(char)
        if code == 0:
            out.extend(b"\xc0\x80")
        elif code < 0x80:
            out.append(code)
        elif code < 0x800:
            out.append(0xC0 | (code >> 6))
            out.append(0x80 | (code & 0x3F))
        elif code < 0x10000:
            write_three(code)
        else:
            value = code - 0x10000
            write_three(0xD800 + (value >> 10))
            write_three(0xDC00 + (value & 0x3FF))
    return bytes(out)


# ---------------------------------------------------------------------------
# 读取


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        end = self.pos + size
        if end > len(self.data):
            raise NBTError("NBT 数据提前结束")
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def u1(self) -> int:
        return self.take(1)[0]

    def i1(self) -> int:
        return struct.unpack(">b", self.take(1))[0]

    def u2(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def i2(self) -> int:
        return struct.unpack(">h", self.take(2))[0]

    def i4(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def i8(self) -> int:
        return struct.unpack(">q", self.take(8))[0]

    def f4(self) -> float:
        return struct.unpack(">f", self.take(4))[0]

    def f8(self) -> float:
        return struct.unpack(">d", self.take(8))[0]

    def string(self) -> NBTString:
        length = self.u2()
        return NBTString(_decode_modified_utf8(self.take(length)))

    def payload(self, tag: int) -> Any:
        if tag == TAG_BYTE:
            return Byte(self.i1())
        if tag == TAG_SHORT:
            return Short(self.i2())
        if tag == TAG_INT:
            return Int(self.i4())
        if tag == TAG_LONG:
            return Long(self.i8())
        if tag == TAG_FLOAT:
            return Float(self.f4())
        if tag == TAG_DOUBLE:
            return Double(self.f8())
        if tag == TAG_BYTE_ARRAY:
            return ByteArray(self.i1() for _ in range(self.i4()))
        if tag == TAG_STRING:
            return self.string()
        if tag == TAG_LIST:
            element_type = self.u1()
            length = self.i4()
            return NBTList(
                (self.payload(element_type) for _ in range(length)),
                element_type=element_type,
            )
        if tag == TAG_COMPOUND:
            compound: dict[str, Any] = {}
            while True:
                child = self.u1()
                if child == TAG_END:
                    return compound
                name = str(self.string())
                compound[name] = self.payload(child)
        if tag == TAG_INT_ARRAY:
            return IntArray(self.i4() for _ in range(self.i4()))
        if tag == TAG_LONG_ARRAY:
            return LongArray(self.i8() for _ in range(self.i4()))
        raise NBTError(f"未知的 NBT 标签类型 {tag}")


def parse_nbt(data: bytes) -> dict[str, Any]:
    """解析未压缩的 NBT 数据，返回根 Compound。"""
    reader = _Reader(data)
    tag = reader.u1()
    if tag != TAG_COMPOUND:
        raise NBTError("根标签不是 Compound")
    reader.string()  # 根名称，通常为空
    value = reader.payload(TAG_COMPOUND)
    return value


def read_nbt(path: Path | str, compressed: bool | None = None) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if compressed is None:
        compressed = raw[:2] == b"\x1f\x8b"
    if compressed:
        try:
            raw = gzip.decompress(raw)
        except OSError as error:
            raise NBTError(f"无法解压 NBT 文件：{error}") from error
    return parse_nbt(raw)


# ---------------------------------------------------------------------------
# 写入


def _tag_of(value: Any) -> int:
    kind = type(value)
    if kind is Byte or kind is bool:
        return TAG_BYTE
    if kind is Short:
        return TAG_SHORT
    if kind is Int:
        return TAG_INT
    if kind is Long:
        return TAG_LONG
    if kind is Float:
        return TAG_FLOAT
    if kind is Double:
        return TAG_DOUBLE
    if kind is ByteArray:
        return TAG_BYTE_ARRAY
    if kind is IntArray:
        return TAG_INT_ARRAY
    if kind is LongArray:
        return TAG_LONG_ARRAY
    if kind is NBTString or kind is str:
        return TAG_STRING
    if kind is NBTList or kind is list or kind is tuple:
        return TAG_LIST
    if kind is dict:
        return TAG_COMPOUND
    if isinstance(value, bool):
        return TAG_BYTE
    if isinstance(value, int):
        return TAG_INT
    if isinstance(value, float):
        return TAG_DOUBLE
    if isinstance(value, str):
        return TAG_STRING
    if isinstance(value, (list, tuple)):
        return TAG_LIST
    if isinstance(value, dict):
        return TAG_COMPOUND
    raise NBTError(f"无法写入的 NBT 值类型：{kind.__name__}")


def _list_element_type(items: list[Any], declared: int | None) -> int:
    if declared is not None:
        return declared
    if not items:
        return TAG_END
    return _tag_of(items[0])


def _write_payload(out: bytearray, tag: int, value: Any) -> None:
    if tag == TAG_BYTE:
        out.extend(struct.pack(">b", int(value)))
    elif tag == TAG_SHORT:
        out.extend(struct.pack(">h", int(value)))
    elif tag == TAG_INT:
        out.extend(struct.pack(">i", int(value)))
    elif tag == TAG_LONG:
        out.extend(struct.pack(">q", int(value)))
    elif tag == TAG_FLOAT:
        out.extend(struct.pack(">f", float(value)))
    elif tag == TAG_DOUBLE:
        out.extend(struct.pack(">d", float(value)))
    elif tag == TAG_BYTE_ARRAY:
        out.extend(struct.pack(">i", len(value)))
        for item in value:
            out.extend(struct.pack(">b", int(item)))
    elif tag == TAG_STRING:
        encoded = _encode_modified_utf8(str(value))
        out.extend(struct.pack(">H", len(encoded)))
        out.extend(encoded)
    elif tag == TAG_LIST:
        declared = getattr(value, "element_type", None)
        items = list(value)
        element_type = _list_element_type(items, declared)
        out.append(element_type)
        out.extend(struct.pack(">i", len(items)))
        for item in items:
            _write_payload(out, element_type if element_type else _tag_of(item), item)
    elif tag == TAG_COMPOUND:
        for name, child in value.items():
            child_tag = _tag_of(child)
            out.append(child_tag)
            encoded_name = _encode_modified_utf8(str(name))
            out.extend(struct.pack(">H", len(encoded_name)))
            out.extend(encoded_name)
            _write_payload(out, child_tag, child)
        out.append(TAG_END)
    elif tag == TAG_INT_ARRAY:
        out.extend(struct.pack(">i", len(value)))
        for item in value:
            out.extend(struct.pack(">i", int(item)))
    elif tag == TAG_LONG_ARRAY:
        out.extend(struct.pack(">i", len(value)))
        for item in value:
            out.extend(struct.pack(">q", int(item)))
    else:
        raise NBTError(f"无法写入的 NBT 标签类型 {tag}")


def write_nbt(root: dict[str, Any]) -> bytes:
    out = bytearray()
    out.append(TAG_COMPOUND)
    out.extend(struct.pack(">H", 0))
    _write_payload(out, TAG_COMPOUND, root)
    return bytes(out)


def write_nbt_file(
    path: Path | str,
    root: dict[str, Any],
    compressed: bool = True,
    backup: bool = False,
) -> None:
    """原子写入 NBT 文件；backup=True 时先把原文件复制为 *_old。"""
    target = Path(path)
    data = write_nbt(root)
    if compressed:
        data = gzip.compress(data, compresslevel=6, mtime=0)
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup and target.exists():
        old = target.with_name(target.name + "_old")
        try:
            old.write_bytes(target.read_bytes())
        except OSError:
            pass
    temporary = target.with_name(target.name + ".dkl-tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
