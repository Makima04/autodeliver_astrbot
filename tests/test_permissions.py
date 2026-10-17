"""权限矩阵自测（纯离线，不调后端）。

覆盖两套规则：
1. 敏感指令（登录/扫码/账号/商品/发货…）→ 只认 admin_qq 里的管理员；未配置时退化为仅私聊
2. 搜索指令 → all / blacklist / whitelist 三种模式，管理员始终放行

    python tests/test_permissions.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "stub"))
sys.path.insert(0, str(HERE.parent))

from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.star import Context  # noqa: E402

import main as plugin_main  # noqa: E402

BASE = {
    "base_url": "http://127.0.0.1:8080",
    "admin_qq": ["10001"],
    "search_cooldown": 0,
}

failures: list[str] = []


def plugin_with(**overrides):
    cfg = {**BASE, **overrides}
    return plugin_main.XianyuAutoDeliverPlugin(Context(), cfg)


def private(sender: str) -> AstrMessageEvent:
    return AstrMessageEvent("闲鱼", sender_id=sender, umo=f"onebot:private_{sender}", private=True)


def group(sender: str) -> AstrMessageEvent:
    return AstrMessageEvent("闲鱼", sender_id=sender, umo="onebot:group_555", private=False)


def expect(title: str, actual, want_allowed: bool) -> None:
    allowed = actual is None
    ok = allowed is want_allowed
    mark = "✅" if ok else "❌"
    detail = "放行" if allowed else f"拒绝：{str(actual).splitlines()[0]}"
    print(f"{mark} {title:<44} → {detail}")
    if not ok:
        failures.append(title)


async def main() -> int:
    print("=" * 18, "敏感指令（管理员）", "=" * 18)
    p = plugin_with()
    expect("管理员 10001 / 私聊", p._admin_denied(private("10001")), True)
    expect("管理员 10001 / 群聊", p._admin_denied(group("10001")), True)
    expect("普通用户 10002 / 私聊", p._admin_denied(private("10002")), False)
    expect("普通用户 10002 / 群聊", p._admin_denied(group("10002")), False)

    empty = plugin_with(admin_qq=[])
    expect("未配置名单 / 私聊（兼容旧行为）", empty._admin_denied(private("10009")), True)
    expect("未配置名单 / 群聊", empty._admin_denied(group("10009")), False)

    print("\n" + "=" * 18, "搜索：all", "=" * 18)
    p = plugin_with(search_access_mode="all")
    expect("陌生用户 / 群聊", p._search_denied(group("99999")), True)
    expect("陌生用户 / 私聊", p._search_denied(private("99999")), True)

    print("\n" + "=" * 18, "搜索：blacklist", "=" * 18)
    p = plugin_with(search_access_mode="blacklist", search_blacklist=["20001"])
    expect("黑名单 20001", p._search_denied(private("20001")), False)
    expect("普通用户 20002", p._search_denied(private("20002")), True)
    expect("管理员 10001（也在黑名单外）", p._search_denied(private("10001")), True)
    pb = plugin_with(search_access_mode="blacklist", search_blacklist=["10001"])
    expect("管理员 10001 被拉黑 → 仍放行", pb._search_denied(private("10001")), True)

    print("\n" + "=" * 18, "搜索：whitelist", "=" * 18)
    p = plugin_with(search_access_mode="whitelist", search_whitelist=["30001"])
    expect("白名单 30001", p._search_denied(private("30001")), True)
    expect("非白名单 30002", p._search_denied(private("30002")), False)
    expect("管理员 10001（不在白名单）→ 仍放行", p._search_denied(private("10001")), True)

    print("\n" + "=" * 18, "模式/名单写法容错", "=" * 18)
    for raw, want in [
        ("all", "all"),
        ("BLACKLIST", "blacklist"),
        ("白名单", "whitelist"),
        ("黑名单", "blacklist"),
        ("乱填的", "all"),
        ("", "all"),
    ]:
        got = plugin_with(search_access_mode=raw)._search_mode()
        ok = got == want
        print(f"{'✅' if ok else '❌'} search_access_mode={raw!r:<12} → {got}（期望 {want}）")
        if not ok:
            failures.append(f"模式解析 {raw!r}")

    comma = plugin_with(admin_qq="10001, 10002，10003")
    got = comma._admin_ids()
    ok = got == ["10001", "10002", "10003"]
    print(f"{'✅' if ok else '❌'} admin_qq 逗号/中文逗号 → {got}")
    if not ok:
        failures.append("admin_qq 字符串解析")

    umo_style = plugin_with(admin_qq=["onebot:private_10001"])
    got = umo_style._admin_ids()
    ok = got == ["10001"]
    print(f"{'✅' if ok else '❌'} admin_qq UMO 写法 → {got}")
    if not ok:
        failures.append("admin_qq UMO 解析")

    print("\n" + "=" * 18, "指令入口（拒绝时不触网）", "=" * 18)
    p = plugin_with(search_access_mode="whitelist", search_whitelist=["30001"])
    out = [t async for t in p.cmd_search(AstrMessageEvent("闲鱼搜索 switch", sender_id="30002"))]
    text = out[0].chain[0]["text"] if out else ""
    ok = "白名单" in text
    print(f"{'✅' if ok else '❌'} 非白名单用户搜 /闲鱼搜索 → {text.splitlines()[0] if text else '（无输出）'}")
    if not ok:
        failures.append("cmd_search 白名单拦截")

    out = [t async for t in p.cmd_qrcode(AstrMessageEvent("闲鱼扫码", sender_id="30001"))]
    text = out[0].chain[0]["text"] if out else ""
    ok = "管理员指令" in text
    print(
        f"{'✅' if ok else '❌'} 普通用户（白名单内）发 /闲鱼扫码 → "
        f"{text.splitlines()[0] if text else '（无输出）'}"
    )
    if not ok:
        failures.append("cmd_qrcode 管理员拦截")

    out = [t async for t in p.cmd_perms(AstrMessageEvent("闲鱼权限", sender_id="10001"))]
    text = out[0].chain[0]["text"] if out else ""
    print("\n---- /闲鱼权限 输出 ----")
    print(text)
    if "管理员名单" not in text or "搜索模式" not in text:
        failures.append("cmd_perms 输出")

    print()
    if failures:
        print(f"❌ {len(failures)} 项未通过：" + "、".join(failures))
        return 1
    print("✅ 权限矩阵全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
