"""`/闲鱼 列表` 指令清单的离线测试（不调后端）。

验证三件事：
1. 清单覆盖 COMMAND_SPECS 里的每一条指令，且菜单 / 清单 / 真实 handler 三者不脱节
2. `/闲鱼`、`/闲鱼 列表`、`/闲鱼列表` 三种写法各走各的分支
3. 未知子命令给提示而不是静默，也不落到大模型

    python tests/test_command_list.py
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

failures: list[str] = []


def plugin_with(**overrides):
    cfg = {
        "base_url": "http://127.0.0.1:8080",
        "admin_qq": ["10001"],
        "search_cooldown": 0,
        **overrides,
    }
    return plugin_main.XianyuAutoDeliverPlugin(Context(), cfg)


def text_of(out: list) -> str:
    return out[0].chain[0]["text"] if out else ""


async def say(handler, message: str, sender: str = "10001", private: bool = True) -> str:
    umo = f"onebot:private_{sender}" if private else "onebot:group_555"
    event = AstrMessageEvent(message, sender_id=sender, umo=umo, private=private)
    return text_of([item async for item in handler(event)])


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        failures.append(name)


def registered_commands() -> set[str]:
    """从真实 handler 上把 @filter.command 的指令名 + 别名全部捞出来。"""
    names: set[str] = set()
    cls = plugin_main.XianyuAutoDeliverPlugin
    for attr in dir(cls):
        func = getattr(cls, attr)
        name = getattr(func, "_command", None)
        if not name:
            continue
        names.add(name)
        names.update(getattr(func, "_alias", set()) or set())
    return names


async def main() -> int:
    p = plugin_with()
    specs = plugin_main.COMMAND_SPECS

    print("=" * 16, "清单覆盖度", "=" * 16)
    listing = await say(p.cmd_help, "闲鱼 列表")
    for spec in specs:
        check(f"清单含用法 {spec.usage}", spec.usage in listing)
        check(f"清单含说明（{spec.usage}）", spec.desc in listing)
    check("有总条数", f"共 {len(specs)} 条" in listing)
    check("长文本可控（< 3000 字）", len(listing) < 3000, f"实际 {len(listing)} 字")

    print("\n" + "=" * 16, "菜单 / 清单 / handler 不脱节", "=" * 16)
    menu = plugin_main.MENU_TEXT
    real = registered_commands()
    for spec in specs:
        path = spec.usage.lstrip("/").split()[0]
        check(f"handler 存在：{path}", path in real, f"已注册：{sorted(real)}")
        check(f"菜单含 {spec.usage}", spec.usage in menu)
        for alias in spec.alias:
            check(f"别名已注册：{alias}", alias in real)

    print("\n" + "=" * 16, "三种写法各走各的分支", "=" * 16)
    check("/闲鱼 → 菜单", await say(p.cmd_help, "闲鱼") == menu)
    check("/闲鱼 列表 → 清单", await say(p.cmd_help, "闲鱼 列表") == listing)
    check("/闲鱼列表 → 清单（无空格别名）", await say(p.cmd_help, "闲鱼列表") == listing)
    check("/闲鱼 指令 → 清单", await say(p.cmd_help, "闲鱼 指令") == listing)
    check("/闲鱼 list → 清单", await say(p.cmd_help, "闲鱼 list") == listing)
    check("/闲鱼 帮助 → 菜单", await say(p.cmd_help, "闲鱼 帮助") == menu)
    unknown = await say(p.cmd_help, "闲鱼 乱写")
    check("未知子命令给提示", "没有「乱写」这个子命令" in unknown, unknown[:60])
    check("未知子命令附菜单", menu in unknown)

    print("\n" + "=" * 16, "按发送者标注身份", "=" * 16)
    admin_view = await say(p.cmd_help, "闲鱼 列表", sender="10001")
    check("管理员 → 👑 管理员", "👑 管理员" in admin_view)
    other = await say(p.cmd_help, "闲鱼 列表", sender="99999")
    check("普通用户 → 👤 普通用户", "👤 普通用户" in other)
    p_white = plugin_with(search_access_mode="whitelist", search_whitelist=["30001"])
    white_no = await say(p_white.cmd_help, "闲鱼 列表", sender="99999")
    check("白名单模式下普通用户 → ❌ 不能搜索", "❌ 你不能搜索" in white_no)
    white_ok = await say(p_white.cmd_help, "闲鱼 列表", sender="30001")
    check("白名单用户在名单内 → ✅ 可以搜索", "✅ 你可以搜索" in white_ok)

    print("\n" + "=" * 16, "权限标注", "=" * 16)
    admin_specs = [s for s in specs if s.perm == plugin_main.PERM_ADMIN]
    check(f"管理员指令 {len(admin_specs)} 条都在菜单里带 🔐", all(f"{s.usage} — " in menu for s in admin_specs))
    check("菜单里有 🔐 图例", "🔐 = 仅管理员" in menu)
    check(
        "搜索指令标成公开但按搜索权限",
        plugin_main.PERM_LABELS[plugin_main.PERM_SEARCH].startswith("🌐"),
    )
    check("清单标注每条权限", listing.count("🔐 仅管理员") >= len(admin_specs))

    await p.terminate()
    await p_white.terminate()
    print()
    if failures:
        print(f"❌ {len(failures)} 项未通过：{failures}")
        return 1
    print("✅ 指令清单测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
