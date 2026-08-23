#!/usr/bin/env python3
"""官方来源白名单：这个模块是"什么算官方来源"的唯一判定点。

散文里写"只查已确认的官方渠道"是无效约束——它无法被检验，失败还是静默的。
所以判定收在这里一次：``check_url`` 是证据研究员每次联网前必须过的那道闸，
``normalize_domain`` 是白名单写入时的唯一归一化入口。

拒绝的几类伪装都来自真实事故形态，不是假想。下面用 RFC 2606 保留域 example.com
举例（真实白名单里写的是客户官网），攻击侧只写主机名，前面自行补 https://：

- ``example.com.attacker.test``       后缀伪装：字符串 ``in`` 判断会放过它
- ``example.com@attacker.test``       用户名伪装：肉眼看着像官网
- ``attacker.test/https://example.com``  路径伪装
- ``127.0.0.1/...``                   IP 直连
- 白名单里写 ``*.example.com``         通配符白名单等于没有白名单

匹配规则只有一条：主机名等于白名单域名，或者是它的子域。**白名单是主机级的**——
写 ``example.com/news`` 不会把范围限制到那个目录，路径会被丢掉并告知。

这个模块不发起任何网络请求，只做字符串与主机名判断。真正的抓取由证据研究员用
WebFetch 完成，而它必须先过这里。
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

# RFC 1035 主机名字符集；末尾必须含点，排除 localhost 这类单标签名
_HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")


class OfficialSourceError(ValueError):
    """域名格式无效，或 URL 不属于已确认的官方来源。"""


def normalize_domain(value: str) -> str:
    """把老师给的官网地址归一化成纯 ASCII 主机名。

    带 scheme、带 ``www.``、带路径、带尾点、大小写混写、国际化域名都接受——老师
    会直接粘贴新闻中心的链接，为此报错只会让人绕过白名单。**路径会被丢掉**，因为
    白名单是主机级的：写 ``example.com/news`` 不代表只允许那个目录。丢弃动作由
    ``split_source`` 报给调用方，让老师知道实际生效的是什么。

    用户名密码、通配符和 IP 一律拒绝——这三样都是伪装官网的常见手法，没有正当用途。

    Raises:
        OfficialSourceError: 格式无效。
    """
    return split_source(value)[0]


def split_source(value: str) -> tuple[str, str]:
    """返回 (主机名, 归一化说明)。说明为空串表示输入本来就是纯主域。"""
    raw = (value or "").strip()
    if not raw:
        raise OfficialSourceError("官方域名不能为空")
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in {"http", "https"}:
        raise OfficialSourceError(f"官方来源只支持 http/https：{value}")
    if parsed.username or parsed.password:
        raise OfficialSourceError(
            f"官方来源不能带用户名密码——`https://官网@真实主机/` 是最常见的一种伪装：{value}"
        )
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise OfficialSourceError(f"取不出主机名：{value}")
    if "*" in host:
        raise OfficialSourceError(f"不接受通配符域名（通配白名单等于没有白名单）：{value}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise OfficialSourceError(f"官方来源要填域名，不用 IP：{value}")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError) as exc:
        raise OfficialSourceError(f"域名无法解析为 ASCII：{value}") from exc
    if not _HOSTNAME.fullmatch(ascii_host) or "." not in ascii_host:
        raise OfficialSourceError(f"域名格式无效：{value}")
    notes: list[str] = []
    path_part = "".join(
        part for part in (
            parsed.path if parsed.path not in ("", "/") else "",
            f"?{parsed.query}" if parsed.query else "",
            f"#{parsed.fragment}" if parsed.fragment else "",
        )
    )
    if path_part:
        notes.append(f"路径「{path_part}」已忽略")
    # 粘贴 www.example.com/news 之后，example.com/... 也必须能过。
    # 不归一化到主域的话，白名单会把同一个官网的另一半挡在外面，而症状是"官网也
    # 被拒了"——老师下一步就会绕过白名单。
    if ascii_host.startswith("www.") and ascii_host.count(".") >= 2:
        ascii_host = ascii_host[4:]
        notes.append("`www.` 前缀已归一化到主域")
    return ascii_host, "；".join(notes)


def hosts(data: dict[str, Any]) -> list[str]:
    """工作空间配置里的白名单主机名。格式坏掉的条目跳过，不让整次检索崩掉。"""
    out: list[str] = []
    for item in data.get("official_sources") or []:
        url = item.get("url") if isinstance(item, dict) else item
        if not url:
            continue
        try:
            host = normalize_domain(str(url))
        except OfficialSourceError:
            continue
        if host not in out:
            out.append(host)
    return out


def check_url(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """判断 ``url`` 是否落在这个工作空间确认过的官方域名内。

    Returns:
        ``{"allowed": True, "url", "host", "matched_domain"}``

    Raises:
        OfficialSourceError: URL 形态非法，或主机名不在白名单内。
    """
    parsed = urlsplit((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise OfficialSourceError(f"只允许 http/https：{url}")
    if parsed.username or parsed.password:
        raise OfficialSourceError(
            f"URL 不能带用户名密码——`https://官网@真实主机/` 是最常见的一种伪装：{url}"
        )
    if not parsed.hostname:
        raise OfficialSourceError(f"取不出主机名：{url}")
    try:
        host = parsed.hostname.rstrip(".").lower().encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError) as exc:
        raise OfficialSourceError(f"主机名无法解析：{url}") from exc

    whitelist = hosts(data)
    if not whitelist:
        raise OfficialSourceError(
            "这个工作空间还没有确认任何官方域名。"
            "先在访谈里问清楚，再 bind --official https://<官网> 写进白名单。"
        )
    matched = next((d for d in whitelist if host == d or host.endswith(f".{d}")), None)
    if matched is None:
        raise OfficialSourceError(
            f"{host} 不在已确认的官方域名里（白名单：{'、'.join(whitelist)}）。"
            f"搜索结果自称官网也不算——标成缺口，不要用媒体转述静默替代。"
        )
    return {"allowed": True, "url": url, "host": host, "matched_domain": matched}
