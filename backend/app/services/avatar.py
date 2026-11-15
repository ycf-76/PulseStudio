"""用户头像解析。

策略（面向国内用户）：
  1. QQ 邮箱 → QQ 头像（公开接口，无需申请）
  2. 其他邮箱 → Gravatar identicon（每个邮箱唯一图案，无需注册）
  3. 无邮箱 → 本地默认头像 SVG
"""
from __future__ import annotations

import hashlib

_DEFAULT_AVATAR = "/images/avatar/@man.svg"


def resolve_avatar_url(email: str | None) -> str:
    """根据邮箱解析头像 URL。

    Args:
        email: 用户邮箱，可为 None。

    Returns:
        头像 URL 字符串，绝不会返回空串。
    """
    if not email:
        return _DEFAULT_AVATAR

    email = email.strip().lower()

    if email.endswith("@qq.com"):
        qq_num = email.split("@")[0]
        if qq_num.isdigit():
            return f"https://q1.qlogo.cn/g?b=qq&nk={qq_num}&s=640"

    md5_hash = hashlib.md5(email.encode("utf-8")).hexdigest()
    return f"https://www.gravatar.com/avatar/{md5_hash}?s=200&d=identicon"