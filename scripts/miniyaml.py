#!/usr/bin/env python3
"""YAML 子集读写器。

本技能只用 YAML 存一份 `workspace.yaml`，为了让文案老师的机器上不需要安装
任何第三方包，这里实现一个刚好够用的子集，而不是引入 PyYAML。

支持的语法（也就是 `assets/workspace.template.yaml` 用到的全部语法）：

- ``键: 标量``
- ``键:`` 后跟缩进块（嵌套映射）
- ``键:`` 后跟 ``- 标量`` 列表
- ``键:`` 后跟 ``- 子键: 值`` 列表（映射列表）
- 单引号或双引号包裹的字符串
- 整行注释，以及标量后由空格分隔的行尾注释
- 空容器字面量 ``{}`` 与 ``[]``

不支持的语法（写进文件会抛 ``YamlError``，而不是静默读错）：锚点与别名、
多行块标量（``|`` / ``>``）、非空的流式集合（``[a, b]`` / ``{a: b}``）、多文档。
"""

from __future__ import annotations

from typing import Any

__all__ = ["YamlError", "loads", "dumps", "load_file", "dump_file"]

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"", "null", "~"}
_UNSUPPORTED_PREFIX = ("|", ">", "[", "{", "&", "*", "!")


class YamlError(ValueError):
    """YAML 子集之外的语法，或结构不合法。"""


# --- 读 ---------------------------------------------------------------------


def _strip_comment(raw: str) -> str:
    """去掉行尾注释。引号内的 ``#`` 不算注释。"""
    out: list[str] = []
    quote: str | None = None
    for i, ch in enumerate(raw):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or raw[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(text: str) -> Any:
    """把一个标量字面量转成 Python 值。"""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text == "{}":
        return {}
    if text == "[]":
        return []
    if text[:1] in _UNSUPPORTED_PREFIX:
        raise YamlError(f"不支持的 YAML 标量语法: {text!r}")
    low = text.lower()
    if low in _NULL:
        return None
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _split_key(line: str) -> tuple[str, str] | None:
    """把 ``键: 值`` 拆成 (键, 值)。不是键值行时返回 None。"""
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == ":" and (i + 1 == len(line) or line[i + 1] in " \t"):
            key = line[:i].strip()
            if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
                key = key[1:-1]
            return key, line[i + 1:].strip()
    return None


class _Reader:
    """按缩进递归下降。行已预处理为 (缩进, 内容)。"""

    def __init__(self, lines: list[tuple[int, str]]) -> None:
        self.lines = lines
        self.pos = 0

    def peek(self) -> tuple[int, str] | None:
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    def parse_block(self, indent: int) -> Any:
        head = self.peek()
        if head is None:
            return None
        if head[1].startswith("- "):
            return self.parse_list(indent)
        return self.parse_map(indent)

    def parse_map(self, indent: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while True:
            cur = self.peek()
            if cur is None or cur[0] < indent:
                break
            if cur[0] > indent:
                raise YamlError(f"缩进不一致: {cur[1]!r}")
            if cur[1].startswith("- "):
                break
            pair = _split_key(cur[1])
            if pair is None:
                raise YamlError(f"不是合法的键值行: {cur[1]!r}")
            key, value = pair
            self.pos += 1
            if value:
                out[key] = _scalar(value)
                continue
            nxt = self.peek()
            if nxt is not None and nxt[0] > indent:
                out[key] = self.parse_block(nxt[0])
            elif nxt is not None and nxt[0] == indent and nxt[1].startswith("- "):
                # 列表与父键同缩进也是合法 YAML，手工编辑过的文件常见
                out[key] = self.parse_list(indent)
            else:
                out[key] = None
        return out

    def parse_list(self, indent: int) -> list[Any]:
        out: list[Any] = []
        while True:
            cur = self.peek()
            if cur is None or cur[0] < indent or not cur[1].startswith("- "):
                break
            if cur[0] > indent:
                raise YamlError(f"列表缩进不一致: {cur[1]!r}")
            item = cur[1][2:].strip()
            self.pos += 1
            pair = _split_key(item)
            if pair is None:
                out.append(_scalar(item))
                continue
            # ``- 键: 值`` 起头的映射列表项：后续同属该项的键缩进更深。
            # 缩进量从紧随其后的那一行推断，兼容 ``-   键: 值`` 这类对齐写法。
            follow = self.peek()
            if follow is not None and follow[0] > indent and not follow[1].startswith("- "):
                child_indent = follow[0]
            else:
                child_indent = indent + 2
            entry: dict[str, Any] = {}
            key, value = pair
            if value:
                entry[key] = _scalar(value)
            else:
                nxt = self.peek()
                if nxt is not None and nxt[0] > child_indent:
                    entry[key] = self.parse_block(nxt[0])
                else:
                    entry[key] = None
            while True:
                nxt = self.peek()
                if nxt is None or nxt[0] != child_indent or nxt[1].startswith("- "):
                    break
                sub = _split_key(nxt[1])
                if sub is None:
                    raise YamlError(f"不是合法的键值行: {nxt[1]!r}")
                self.pos += 1
                sub_key, sub_value = sub
                if sub_value:
                    entry[sub_key] = _scalar(sub_value)
                else:
                    deeper = self.peek()
                    if deeper is not None and deeper[0] > child_indent:
                        entry[sub_key] = self.parse_block(deeper[0])
                    else:
                        entry[sub_key] = None
            out.append(entry)
        return out


def loads(text: str) -> Any:
    """解析 YAML 子集文本。返回 dict / list / None。"""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        if raw.strip() in ("---", "..."):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError("缩进不能使用 Tab，请改为空格")
        body = _strip_comment(raw)
        if not body.strip():
            continue
        lines.append((len(body) - len(body.lstrip()), body.strip()))
    if not lines:
        return None
    reader = _Reader(lines)
    result = reader.parse_block(lines[0][0])
    if reader.pos != len(reader.lines):
        raise YamlError(f"第 {reader.pos + 1} 个有效行之后无法继续解析（缩进层级不一致）")
    return result


def load_file(path) -> Any:
    """读并解析一个 YAML 文件。"""
    from pathlib import Path

    return loads(Path(path).read_text(encoding="utf-8"))


# --- 写 ---------------------------------------------------------------------

_NEEDS_QUOTE_START = set("-?:,[]{}#&*!|>'\"%@`")


def _quote(value: str) -> str:
    """需要时给字符串加双引号。"""
    if value == "":
        return '""'
    if value[0] in _NEEDS_QUOTE_START or value[-1] in " \t":
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if ": " in value or " #" in value or value.startswith(" "):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if value.lower() in _TRUE | _FALSE | _NULL:
        return '"' + value + '"'
    try:
        float(value)
    except ValueError:
        return value
    return '"' + value + '"'


def _emit_scalar(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if "\n" in value:
            return _quote(value.replace("\n", " "))
        return _quote(value)
    raise YamlError(f"不支持写出该类型: {type(value).__name__}")


def _kv(pad: str, key: str, value: Any) -> str:
    """写一行 ``键: 值``。``None`` 写成空值，读回来仍是 ``None``。"""
    if value is None:
        return f"{pad}{key}:"
    return f"{pad}{key}: {_emit_scalar(value)}"


def _emit(value: Any, indent: int, out: list[str]) -> None:
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return
        for key, sub in value.items():
            if isinstance(sub, dict) and sub:
                out.append(f"{pad}{key}:")
                _emit(sub, indent + 2, out)
            elif isinstance(sub, list) and sub:
                out.append(f"{pad}{key}:")
                _emit(sub, indent + 2, out)
            elif isinstance(sub, (dict, list)):
                out.append(f"{pad}{key}: " + ("{}" if isinstance(sub, dict) else "[]"))
            else:
                out.append(_kv(pad, key, sub))
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                keys = list(item.items())
                if not keys:
                    out.append(f"{pad}- {{}}")
                    continue
                first_key, first_val = keys[0]
                if isinstance(first_val, (dict, list)):
                    raise YamlError("映射列表项的首个键不能是嵌套结构")
                first_line = _kv("", first_key, first_val)
                out.append(f"{pad}- {first_line}")
                for k, v in keys[1:]:
                    if isinstance(v, dict) and v:
                        out.append(f"{pad}  {k}:")
                        _emit(v, indent + 4, out)
                    elif isinstance(v, list) and v:
                        out.append(f"{pad}  {k}:")
                        _emit(v, indent + 4, out)
                    else:
                        out.append(_kv(pad + "  ", k, v))
            else:
                out.append(f"{pad}- {_emit_scalar(item)}")
        return
    out.append(f"{pad}{_emit_scalar(value)}")


def dumps(data: Any) -> str:
    """把 dict / list 写成 YAML 子集文本。"""
    out: list[str] = []
    _emit(data, 0, out)
    return "\n".join(out) + "\n"


def dump_file(path, data: Any) -> None:
    """把数据写成 YAML 文件（UTF-8，末尾带换行）。"""
    from pathlib import Path

    Path(path).write_text(dumps(data), encoding="utf-8")
