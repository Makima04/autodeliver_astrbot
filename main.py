"""AstrBot 插件：把 AutoDeliver（闲鱼自动发货平台）接到 QQ / 其他 IM 里。

支持的能力：
- /闲鱼            指令总览（菜单）
- /闲鱼 列表       全部指令 + 功能说明（含别名 / 权限 / 备注）
- /闲鱼登录        登录 AutoDeliver 管理端（拿 Bearer token 并落盘复用）
- /闲鱼状态        平台 + 账号 + 订单概览
- /闲鱼账号        已绑定闲鱼账号列表
- /闲鱼扫码        生成登录二维码发到聊天里，后台自动轮询扫码结果
- /闲鱼cookie      用 Cookie 添加/更新闲鱼账号
- /闲鱼校验 <id>   远端验签一个闲鱼账号
- /闲鱼删除 <id>   删除闲鱼账号（二次确认）
- /闲鱼商品 <id>   查看该账号的商品
- /闲鱼同步 <id>   从闲鱼远端同步商品
- /闲鱼搜索 <词>   全网搜索 + 大模型行情总结
- /闲鱼发货 [id]   触发自动发货巡检
- /闲鱼订单        订单统计
- /闲鱼推送        把摘要主动推送到指定会话
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

# 兼容两种加载方式：包内相对导入 / 插件目录直接进 sys.path
try:
    from .api import ApiError, AutoDeliverClient
    from .summary import (
        SUMMARY_SYSTEM_PROMPT,
        build_fallback_summary,
        build_item_lines,
        build_llm_prompt,
        format_accounts,
        format_items,
        format_scan_result,
        format_status,
        money,
        parse_search_args,
    )
except ImportError:  # pragma: no cover
    from api import ApiError, AutoDeliverClient  # type: ignore
    from summary import (  # type: ignore
        SUMMARY_SYSTEM_PROMPT,
        build_fallback_summary,
        build_item_lines,
        build_llm_prompt,
        format_accounts,
        format_items,
        format_scan_result,
        format_status,
        money,
        parse_search_args,
    )

PLUGIN_NAME = "autodeliver_astrbot"


def _raw_message_text(event: AstrMessageEvent) -> str:
    """取唤醒前缀被剥掉之前的原始文本。

    AstrBot 的唤醒阶段会把 wake_prefix（现在含 ``/``）从 ``event.message_str``
    里剥掉，所以判断「这条消息到底是不是斜杠开头」只能看 ``message_obj``。
    """
    obj = getattr(event, "message_obj", None)
    raw = getattr(obj, "message_str", None) if obj is not None else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ""


class SlashCommandOnly(filter.CustomFilter):
    """只匹配「原始消息以 / 开头」的事件。

    必须卡在过滤器这一层：AstrBot 的唤醒阶段只要有任何 handler 的 filter 通过，
    就会把事件标记成「已唤醒」。如果用 ``@filter.regex(r"^")`` 兜底，群里每一条
    闲聊都会被判成唤醒命令，机器人就会对所有人说话。
    """

    def filter(self, event: AstrMessageEvent, cfg: Any) -> bool:
        return _raw_message_text(event).startswith("/")


# 搜索指令的三种访问模式
SEARCH_MODES = ("all", "blacklist", "whitelist")
SEARCH_MODE_LABELS = {
    "all": "任何人可用",
    "blacklist": "黑名单外可用",
    "whitelist": "仅白名单可用",
}

DEFAULTS: dict[str, Any] = {
    "base_url": "http://127.0.0.1:8080",
    "admin_username": "admin",
    "admin_password": "",
    "api_token": "",
    "admin_qq": [],
    "search_access_mode": "all",
    "search_blacklist": [],
    "search_whitelist": [],
    "enable_llm_summary": True,
    "summary_timeout": 45,
    "search_top_n": 5,
    "search_cooldown": 3,
    "qr_poll_interval": 3,
    "qr_poll_timeout": 180,
    "request_timeout": 30,
}

# 指令权限标记
PERM_ALL = "all"  # 任何人
PERM_SEARCH = "search"  # 受 search_access_mode 控制
PERM_ADMIN = "admin"  # 仅 admin_qq 名单

PERM_LABELS = {
    PERM_ALL: "🌐 任何人",
    PERM_SEARCH: "🌐 公开（按搜索权限）",
    PERM_ADMIN: "🔐 仅管理员",
}


@dataclass(frozen=True)
class CommandSpec:
    """一条指令的元数据。菜单（/闲鱼）与清单（/闲鱼 列表）都由它生成。"""

    group: str
    usage: str
    desc: str
    perm: str = PERM_ALL
    alias: tuple[str, ...] = ()
    note: str = ""


# 单一事实来源：新增指令时只改这里，菜单、清单、README 表格才对得上
COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("通用", "/闲鱼", "指令总览（菜单）", alias=("xy", "闲鱼帮助", "闲鱼菜单")),
    CommandSpec(
        "通用",
        "/闲鱼 列表",
        "全部指令与功能说明（就是本清单）",
        alias=("闲鱼列表", "闲鱼指令", "闲鱼清单"),
    ),
    CommandSpec(
        "平台",
        "/闲鱼登录 <用户名> <密码>",
        "登录 AutoDeliver 管理端，token 落盘复用；不带参数则用插件配置里的账号密码",
        PERM_ADMIN,
        alias=("闲鱼登陆",),
        note="带密码时仅私聊",
    ),
    CommandSpec("平台", "/闲鱼状态", "平台登录态 + 闲鱼账号 + 订单概览", PERM_ADMIN),
    CommandSpec(
        "平台",
        "/闲鱼权限",
        "查看当前生效的管理员名单与搜索模式",
        PERM_ADMIN,
        alias=("闲鱼权限查看",),
    ),
    CommandSpec(
        "账号管理",
        "/闲鱼扫码",
        "生成闲鱼登录二维码发到聊天里，后台轮询扫码结果并主动播报",
        PERM_ADMIN,
        alias=("闲鱼二维码",),
    ),
    CommandSpec(
        "账号管理",
        "/闲鱼cookie <Cookie>",
        "用 goofish 的 Cookie 添加 / 更新闲鱼账号并远端验签",
        PERM_ADMIN,
        alias=("闲鱼Cookie",),
        note="仅私聊",
    ),
    CommandSpec(
        "账号管理",
        "/闲鱼账号",
        "已绑定的闲鱼账号列表（状态 / 登录方式 / seller_id / 最近校验）",
        PERM_ADMIN,
    ),
    CommandSpec(
        "账号管理",
        "/闲鱼校验 <账号ID>",
        "调远端验签一个闲鱼账号，返回 valid 与说明",
        PERM_ADMIN,
        alias=("闲鱼验签",),
    ),
    CommandSpec(
        "账号管理",
        "/闲鱼删除 <账号ID>",
        "解绑并删除闲鱼账号（连登录凭据、连接日志一起删）",
        PERM_ADMIN,
        note="需 60 秒内再发「确认」",
    ),
    CommandSpec(
        "商品",
        "/闲鱼商品 [账号ID]",
        "查看该账号已入库的商品（价格由「分」转成元）",
        PERM_ADMIN,
        note="只有一个账号时可省略 ID",
    ),
    CommandSpec(
        "商品", "/闲鱼同步 <账号ID>", "从闲鱼远端拉取商品并入库", PERM_ADMIN
    ),
    CommandSpec(
        "订单 / 发货",
        "/闲鱼订单",
        "订单统计：累计 / 今日 / 待发货 / 已发货 / 成交额",
        PERM_ADMIN,
        alias=("闲鱼订单统计",),
    ),
    CommandSpec(
        "订单 / 发货",
        "/闲鱼发货 [账号ID]",
        "立即巡检闲鱼会话并执行自动发货",
        PERM_ADMIN,
        alias=("闲鱼巡检",),
        note="通常要几十秒",
    ),
    CommandSpec(
        "搜索",
        "/闲鱼搜索 <关键词> [--min 100] [--max 500] [--sort 价格升序] [--top 5]",
        "全网搜索 + 大模型行情总结",
        PERM_SEARCH,
        alias=("xy搜索", "闲鱼查价"),
        note="受 search_access_mode 控制",
    ),
    CommandSpec(
        "其它",
        "/闲鱼推送 绑定|取消|查看|清空",
        "把搜索摘要额外推送到指定会话（群指令触发、摘要回到自己 QQ）",
        PERM_ADMIN,
    ),
)

# /闲鱼 后面跟这些词时，输出详细清单而不是简版菜单
LIST_WORDS = {
    "列表",
    "清单",
    "指令",
    "命令",
    "全部",
    "所有",
    "list",
    "ls",
}

# 明确要看菜单的说法，避免被当成「未知子命令」
MENU_WORDS = {"帮助", "菜单", "说明", "help", "menu", "?", "？"}


def build_menu() -> str:
    """简版菜单：一行一条指令，群里刷屏也不至于太长。"""
    lines = [
        "🐟 闲鱼自动发货助手",
        f"共 {len(COMMAND_SPECS)} 条指令（🔐 = 仅管理员 QQ 可用）",
        "",
    ]
    group = ""
    for spec in COMMAND_SPECS:
        if spec.group != group:
            group = spec.group
            lines.append(f"【{group}】")
        tail = " 🔐" if spec.perm == PERM_ADMIN else ""
        lines.append(f"{spec.usage} — {spec.desc}{tail}")
    lines += [
        "",
        "搜索指令受 search_access_mode 控制：all=任何人 / blacklist=黑名单外 / whitelist=仅白名单",
        "发 /闲鱼 列表 可查看每条指令的详细说明、别名与使用限制",
    ]
    return "\n".join(lines)


MENU_TEXT = build_menu()
HELP_TEXT = MENU_TEXT  # 兼容旧引用


def _guard(func):
    """统一兜住 ApiError / 未知异常，避免单个指令把插件打挂。"""

    @functools.wraps(func)
    async def wrapper(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            async for item in func(self, event, *args, **kwargs):
                yield item
        except ApiError as exc:
            yield event.plain_result(f"❌ {exc.message}")
        except asyncio.TimeoutError:
            yield event.plain_result("❌ 请求超时，后台可能正忙或闲鱼接口较慢，请稍后再试")
        except Exception as exc:  # pragma: no cover - 兜底
            logger.exception(f"[xianyu-autodeliver] 指令执行失败: {exc}")
            yield event.plain_result(f"❌ 插件内部错误：{exc}")

    return wrapper


def _plainify(text: str) -> str:
    """把大模型可能带出来的 Markdown 记号清掉，QQ 里更好看。"""
    for token in ("**", "###", "##", "#", "`", "> "):
        text = text.replace(token, "")
    return text.strip()


def _strip_command(text: str) -> str:
    """从 message_str 中剥掉指令名，拿到剩余参数。"""
    text = (text or "").strip()
    while text.startswith("@"):
        parts = text.split(maxsplit=1)
        text = parts[1] if len(parts) > 1 else ""
    text = text.lstrip("/／!！")
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _png_from_qr_matrix(matrix: list, scale: int = 8) -> bytes:
    """把 qrcode 算出来的布尔矩阵直接编码成灰度 PNG。

    故意不经过 Pillow：AstrBot 官方镜像是 arm64 容器，插件依赖越少，
    容器重建后「二维码生成失败」的概率越低（qrcode 本身是纯 Python）。
    """
    import struct
    import zlib

    modules = len(matrix)
    size = modules * scale
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # PNG 行过滤器类型 0（None）
        row = matrix[y // scale]
        for x in range(size):
            raw.append(0 if row[x // scale] else 255)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)  # 8bit 灰度、无交错
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


class XianyuAutoDeliverPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config
        self.data_dir = self._resolve_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 删除确认、搜索冷却、后台任务引用
        self._pending_delete: dict[str, tuple[float, int]] = {}
        self._last_search: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

        self._token_path = self.data_dir / "auth.json"
        self._push_path = self.data_dir / "push_targets.json"
        self._qr_dir = self.data_dir / "qrcode"
        self._qr_dir.mkdir(parents=True, exist_ok=True)

        base_url = str(self._cfg("base_url"))
        token = self._load_token() or str(self._cfg("api_token") or "")
        self.client = AutoDeliverClient(
            base_url=base_url,
            token=token,
            username=str(self._cfg("admin_username") or ""),
            password=str(self._cfg("admin_password") or ""),
            timeout=float(self._cfg_num("request_timeout", 30)),
            on_token=self._save_token,
        )
        logger.info(
            f"[xianyu-autodeliver] 已加载，后端 {base_url}，数据目录 {self.data_dir}，"
            f"token={'有' if self.client.token else '无'}"
        )

    # ------------------------------------------------------------------ 基础设施
    def _resolve_data_dir(self) -> Path:
        """兼容不同 AstrBot 版本的插件数据目录解析。"""
        candidates = []
        try:
            candidates.append(lambda: StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            pass
        try:
            candidates.append(lambda: StarTools.get_data_dir())
        except Exception:
            pass
        try:
            candidates.append(lambda: self.get_tools().get_data_dir())
        except Exception:
            pass

        for factory in candidates:
            try:
                path = factory()
            except Exception:
                continue
            if path:
                return Path(path)

        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        except Exception:
            return Path("data") / "plugin_data" / PLUGIN_NAME

    def _cfg(self, key: str) -> Any:
        try:
            if self.config is not None and key in self.config:
                value = self.config[key]
                if value not in (None, ""):
                    return value
        except Exception:
            pass
        return DEFAULTS.get(key)

    def _cfg_num(self, key: str, fallback: float) -> float:
        """数字配置读取：0 是合法值，不能用 ``or`` 兜底（0 or x 会变成 x）。"""
        value = self._cfg(key)
        try:
            if value is None or value == "":
                return fallback
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _load_token(self) -> str | None:
        try:
            if self._token_path.exists():
                data = json.loads(self._token_path.read_text("utf-8"))
                return (data or {}).get("token") or None
        except Exception as exc:
            logger.warning(f"[xianyu-autodeliver] 读取本地 token 失败: {exc}")
        return None

    def _save_token(self, token: str) -> None:
        try:
            self._token_path.write_text(
                json.dumps({"token": token, "saved_at": int(time.time())}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.chmod(self._token_path, 0o600)
        except Exception as exc:
            logger.warning(f"[xianyu-autodeliver] 保存 token 失败: {exc}")

    def _load_push_targets(self) -> list[str]:
        try:
            if self._push_path.exists():
                data = json.loads(self._push_path.read_text("utf-8"))
                if isinstance(data, list):
                    return [str(x) for x in data]
        except Exception:
            pass
        return []

    def _save_push_targets(self, targets: list[str]) -> None:
        try:
            self._push_path.write_text(
                json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(f"[xianyu-autodeliver] 保存推送目标失败: {exc}")

    def _is_private(self, event: AstrMessageEvent) -> bool:
        try:
            if hasattr(event, "is_private_chat") and event.is_private_chat():
                return True
        except Exception:
            pass
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        return "private" in umo or "FriendMessage" in umo

    # ------------------------------------------------------------ 斜杠指令守卫
    @staticmethod
    def _matches_activated_command(event: AstrMessageEvent, body: str) -> bool:
        """body 是否命中本次事件里某个已激活的指令过滤器。"""
        try:
            handlers = event.get_extra("activated_handlers") or []
        except Exception:
            return True  # 拿不到激活列表就放行，宁可让 LLM 回，也别把正常指令吃掉
        body = body.strip()
        if not body:
            return False
        for handler in handlers:
            for event_filter in getattr(handler, "event_filters", []) or []:
                getter = getattr(event_filter, "get_complete_command_names", None)
                if getter is None:
                    continue
                try:
                    names = getter()
                except Exception:
                    continue
                for name in names or []:
                    if body == name or body.startswith(f"{name} "):
                        return True
        return False

    @filter.custom_filter(SlashCommandOnly, priority=-999)
    async def guard_unmatched_slash(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """兜底：``/`` 开头但没有任何指令认领的消息，直接吞掉，不落到大模型。

        没有这个守卫时，只要 wake_prefix 里含 ``/``，``/随便什么`` 都会唤醒
        LLM 并让模型回一句毫无意义的话。这里只在「确实没有指令匹配」时拦截，
        ``/help``、``/闲鱼扫码`` 这类正常指令不受影响。
        """
        raw = _raw_message_text(event)
        if not raw.startswith("/"):
            return

        body = raw.lstrip("/").strip()
        if self._matches_activated_command(event, body):
            return

        # 既 stop_event 又禁止默认 LLM：只停 event 不够，AstrBot 在「无结果」时
        # 仍会走 agent 分支，所以必须显式调用 should_call_llm(True)（语义是禁止默认 LLM）
        event.stop_event()
        try:
            event.should_call_llm(True)
        except Exception:
            pass

        if not body:
            yield event.plain_result("发 /闲鱼 查看全部指令")
        elif body.startswith(("闲鱼", "xy", "XY")):
            yield event.plain_result(f"🤔 没有「{body}」这个指令，发 /闲鱼 查看菜单")

    def _qq_list(self, key: str) -> list[str]:
        """读取配置里的 QQ 号列表，兼容 list / 逗号分隔字符串 / UMO 写法。"""
        raw = self._cfg(key) or []
        if isinstance(raw, str):
            raw = [x.strip() for x in raw.replace("，", ",").split(",")]
        ids: list[str] = []
        for item in raw:
            text = str(item).strip()
            if not text:
                continue
            if ":" in text:  # 兼容 onebot:private_123 这类 UMO 写法
                text = text.rsplit("_", 1)[-1].rsplit(":", 1)[-1]
            if text and text not in ids:
                ids.append(text)
        return ids

    def _admin_ids(self) -> list[str]:
        return self._qq_list("admin_qq")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        return str(event.get_sender_id()) in self._admin_ids()

    def _admin_denied(self, event: AstrMessageEvent) -> str | None:
        """敏感指令（登录 / 扫码 / 账号 / 商品 / 发货…）的准入判断。

        返回 None 表示放行，否则返回拒绝理由。
        """
        admins = self._admin_ids()
        sender = str(event.get_sender_id())
        if admins:
            if sender in admins:
                return None
            return (
                "⛔ 这是管理员指令，你没有权限执行\n"
                f"当前管理员：{'、'.join(admins)}\n"
                "（插件配置 admin_qq 可调整）"
            )
        # 未配置管理员名单时退化为「仅允许私聊」，避免群成员误删账号 / 误发货
        if self._is_private(event):
            return None
        return (
            "⛔ 尚未配置管理员名单，指令只能在私聊里使用\n"
            "请在插件配置 admin_qq 里填上自己的 QQ 号，之后即可在群里使用"
        )

    def _search_mode(self) -> str:
        """搜索指令的访问模式：all / blacklist / whitelist。"""
        raw = str(self._cfg("search_access_mode") or "all").strip().lower()
        alias = {
            "任何人": "all",
            "所有人": "all",
            "全部": "all",
            "公开": "all",
            "anyone": "all",
            "黑名单": "blacklist",
            "block": "blacklist",
            "deny": "blacklist",
            "白名单": "whitelist",
            "allow": "whitelist",
            "white": "whitelist",
        }
        mode = alias.get(raw, raw)
        return mode if mode in SEARCH_MODES else "all"

    def _search_denied(self, event: AstrMessageEvent) -> str | None:
        """搜索指令的准入判断：管理员永远放行，其余按模式判定。"""
        if self._is_admin(event):
            return None

        mode = self._search_mode()
        if mode == "all":
            return None

        sender = str(event.get_sender_id())
        if mode == "whitelist":
            if sender in self._qq_list("search_whitelist"):
                return None
            return (
                "⛔ 搜索目前是白名单模式，你不在名单内\n"
                "（由管理员在插件配置 search_whitelist 中添加）"
            )
        if sender in self._qq_list("search_blacklist"):
            return "⛔ 你已被限制使用搜索功能（由管理员配置）"
        return None

    async def _push(self, umo: str, text: str) -> None:
        try:
            await self.context.send_message(umo, MessageChain().message(text))
        except Exception as exc:
            logger.error(f"[xianyu-autodeliver] 主动推送失败({umo}): {exc}")

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def terminate(self):
        for task in list(self._tasks):
            task.cancel()
        try:
            await self.client.aclose()
        except Exception:
            pass
        logger.info("[xianyu-autodeliver] 插件已卸载，资源已释放")

    # ------------------------------------------------------------------ 指令：帮助
    @filter.command(
        "闲鱼",
        alias={"xy", "闲鱼帮助", "闲鱼菜单", "闲鱼列表", "闲鱼指令", "闲鱼清单"},
    )
    @_guard
    async def cmd_help(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """``/闲鱼`` 给菜单，``/闲鱼 列表`` 给逐条详解。"""
        raw = (event.message_str or "").strip().lstrip("/／!！")
        parts = raw.split(maxsplit=1)
        head = parts[0].strip() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        lowered = arg.lower()

        # /闲鱼列表 这类没有空格的别名，等价于 /闲鱼 列表
        if not arg and head in ("闲鱼列表", "闲鱼指令", "闲鱼清单"):
            lowered = "列表"

        if lowered in LIST_WORDS:
            yield event.plain_result(self._command_list_text(event))
            return
        if arg and lowered not in MENU_WORDS:
            yield event.plain_result(
                f"🤔 没有「{arg}」这个子命令，先看总览（详细说明发 /闲鱼 列表）\n\n{MENU_TEXT}"
            )
            return
        yield event.plain_result(MENU_TEXT)

    def _command_list_text(self, event: AstrMessageEvent) -> str:
        """生成「全部指令 + 功能」清单，并按发送者标注他现在能不能用。"""
        lines = [
            "🐟 闲鱼自动发货助手 · 指令清单",
            f"共 {len(COMMAND_SPECS)} 条｜🔐 仅管理员　🌐 公开",
            "",
        ]
        group = ""
        index = 0
        for spec in COMMAND_SPECS:
            if spec.group != group:
                group = spec.group
                lines.append(f"【{group}】")
            index += 1
            lines.append(f"{index}. {spec.usage}")
            meta = [spec.desc, PERM_LABELS.get(spec.perm, spec.perm)]
            if spec.alias:
                meta.append("别名 " + "、".join(spec.alias))
            if spec.note:
                meta.append(spec.note)
            lines.append("   " + "｜".join(meta))

        is_admin = self._admin_denied(event) is None
        mode = self._search_mode()
        can_search = self._search_denied(event) is None
        sender = str(event.get_sender_id())
        lines += [
            "",
            f"你的身份：{sender} → {'👑 管理员' if is_admin else '👤 普通用户（仅公开指令可用）'}",
            f"搜索模式：{mode}（{SEARCH_MODE_LABELS.get(mode, mode)}）→ "
            f"{'✅ 你可以搜索' if can_search else '❌ 你不能搜索'}",
            "自查权限：/闲鱼权限",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ 指令：权限自查
    @filter.command("闲鱼权限", alias={"闲鱼权限查看"})
    @_guard
    async def cmd_perms(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        admins = self._admin_ids()
        mode = self._search_mode()
        blacklist = self._qq_list("search_blacklist")
        whitelist = self._qq_list("search_whitelist")
        sender = str(event.get_sender_id())

        lines = ["🔐 当前权限配置", ""]
        if admins:
            lines.append(f"管理员名单（{len(admins)}）：{'、'.join(admins)}")
            lines.append(f"  你（{sender}）→ {'✅ 管理员' if sender in admins else '❌ 非管理员'}")
        else:
            lines.append("管理员名单：未配置")
            lines.append("  敏感指令只能私聊使用，群里一律拒绝")
        lines.append("")
        lines.append(f"搜索模式：{mode}（{SEARCH_MODE_LABELS.get(mode, mode)}）")
        if mode == "blacklist":
            lines.append(f"  黑名单（{len(blacklist)}）：{'、'.join(blacklist) or '空'}")
        elif mode == "whitelist":
            lines.append(f"  白名单（{len(whitelist)}）：{'、'.join(whitelist) or '空'}")
        can_search = self._search_denied(event) is None
        lines.append(f"  你（{sender}）→ {'✅ 可以搜索' if can_search else '❌ 不能搜索'}")
        lines.append("")
        lines.append("修改入口：AstrBot WebUI → 插件 → autodeliver_astrbot → 配置")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ 指令：平台登录
    @filter.command("闲鱼登录", alias={"闲鱼登陆"})
    @_guard
    async def cmd_login(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        args = _strip_command(event.message_str)
        parts = args.split()
        if len(parts) >= 2:
            if not self._is_private(event):
                yield event.plain_result("⛔ 带密码登录请在私聊里执行，避免群成员看到密码")
                return
            username, password = parts[0], parts[1]
        elif parts:
            yield event.plain_result("用法：/闲鱼登录 <用户名> <密码>")
            return
        else:
            username = str(self._cfg("admin_username") or "")
            password = str(self._cfg("admin_password") or "")
            if not password:
                yield event.plain_result(
                    "用法：/闲鱼登录 <用户名> <密码>\n"
                    "或在插件配置里填好 admin_username / admin_password 后直接发 /闲鱼登录"
                )
                return

        data = await self.client.login(username, password)
        expire = data.get("expires_at") or "（静态 token 无过期时间）"
        yield event.plain_result(
            f"✅ 已登录 AutoDeliver\n"
            f"管理员：{data.get('username')}（{data.get('role')}）\n"
            f"有效期至：{expire}\n"
            f"Token 已保存到本地，重启后自动复用。"
        )

    # ------------------------------------------------------------------ 指令：状态
    @filter.command("闲鱼状态")
    @_guard
    async def cmd_status(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        try:
            await self.client.ensure_token()
            token_ok = True
        except ApiError:
            token_ok = False

        accounts = await self.client.list_accounts() if token_ok else []
        stats = None
        if token_ok:
            try:
                stats = await self.client.order_stats()
            except ApiError:
                stats = None
        yield event.plain_result(
            format_status(accounts, stats, token_ok, str(self._cfg("base_url")))
        )

    # ------------------------------------------------------------------ 指令：账号列表
    @filter.command("闲鱼账号", alias={"闲鱼帐号"})
    @_guard
    async def cmd_accounts(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return
        accounts = await self.client.list_accounts()
        yield event.plain_result(format_accounts(accounts))

    # ------------------------------------------------------------------ 指令：扫码登录
    @filter.command("闲鱼扫码", alias={"闲鱼二维码"})
    @_guard
    async def cmd_qrcode(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        qr = await self.client.qrcode_generate()
        content = qr.get("code_content") or ""
        if not content:
            yield event.plain_result("❌ 后端没有返回二维码内容，请稍后重试")
            return

        image_path = self._qr_dir / f"qr_{int(time.time())}.png"
        rendered = self._render_qr(content, image_path)

        header = (
            "📱 请用手机闲鱼 App 扫下面的二维码\n"
            f"（{self._cfg('qr_poll_timeout')} 秒内有效，扫码后我会自动告知结果）"
        )
        if rendered:
            yield event.chain_result(
                [Comp.Plain(header), Comp.Image.fromFileSystem(str(image_path))]
            )
        else:
            yield event.plain_result(
                header
                + "\n⚠️ 未安装 qrcode 依赖，无法生成图片。请在 AstrBot 插件页重装依赖，或执行：\n"
                "pip install qrcode\n"
                f"或手工把下面链接转成二维码后扫描：\n{content}"
            )

        self._spawn(
            self._poll_qrcode(
                umo=event.unified_msg_origin,
                t=str(qr.get("t") or ""),
                ck=str(qr.get("ck") or ""),
                session_cookies=qr.get("session_cookies"),
            )
        )

    def _render_qr(self, content: str, out_path: Path) -> bool:
        try:
            import qrcode
        except ImportError:
            return False
        try:
            qr = qrcode.QRCode(border=2, box_size=1)
            qr.add_data(content)
            qr.make(fit=True)
            out_path.write_bytes(_png_from_qr_matrix(qr.get_matrix(), scale=8))
            # 只保留最近 10 张二维码图片
            for old in sorted(self._qr_dir.glob("qr_*.png"))[:-10]:
                try:
                    old.unlink()
                except OSError:
                    pass
            return True
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[xianyu-autodeliver] 二维码渲染失败: {exc}")
            return False

    async def _poll_qrcode(
        self, umo: str, t: str, ck: str, session_cookies: str | None
    ) -> None:
        interval = self._cfg_num("qr_poll_interval", 3)
        deadline = time.monotonic() + self._cfg_num("qr_poll_timeout", 180)
        scanned_notified = False

        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                data = await self.client.qrcode_poll(t, ck, session_cookies)
            except ApiError as exc:
                await self._push(umo, f"⚠️ 查询扫码状态失败：{exc.message}")
                return

            status = str(data.get("qr_status") or "").upper()
            if status == "CONFIRMED":
                account = data.get("account") or {}
                nick = account.get("seller_nick") or "未知昵称"
                await self._push(
                    umo,
                    "✅ 扫码登录成功！\n"
                    f"账号：{nick}\n"
                    f"ID：{account.get('seller_id') or '—'}\n"
                    f"状态：{account.get('status') or '—'}\n"
                    "发 /闲鱼账号 可以查看全部账号。",
                )
                return
            if status == "EXPIRED":
                await self._push(umo, "⌛ 二维码已过期，请重新发送 /闲鱼扫码")
                return
            if status == "SCANED" and not scanned_notified:
                scanned_notified = True
                await self._push(umo, "📱 已扫码，请在手机上点「确认登录」…")

        await self._push(umo, "⌛ 等待扫码超时，请重新发送 /闲鱼扫码")

    # ------------------------------------------------------------------ 指令：Cookie 登录
    @filter.command("闲鱼cookie", alias={"闲鱼Cookie"})
    @_guard
    async def cmd_cookie(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return
        if not self._is_private(event):
            yield event.plain_result("⛔ Cookie 属于敏感凭据，请在私聊里发送")
            return

        cookie = _strip_command(event.message_str)
        if not cookie or len(cookie) < 20:
            yield event.plain_result(
                "用法：/闲鱼cookie <从 goofish.com 复制出来的完整 Cookie>\n"
                "Cookie 很长，建议私聊发送，登录成功后请及时撤回消息。"
            )
            return

        account = await self.client.login_cookie(cookie)
        yield event.plain_result(
            f"✅ Cookie 已保存并完成远端验签\n"
            f"账号：{account.get('seller_nick') or '未解析昵称'}\n"
            f"ID：{account.get('id')}｜状态：{account.get('status')}\n"
            "建议现在撤回刚才那条 Cookie 消息。"
        )

    # ------------------------------------------------------------------ 指令：校验 / 删除
    @filter.command("闲鱼校验", alias={"闲鱼验签"})
    @_guard
    async def cmd_verify(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        args = _strip_command(event.message_str)
        if not args.isdigit():
            yield event.plain_result("用法：/闲鱼校验 <账号ID>（账号ID 用 /闲鱼账号 查）")
            return
        result = await self.client.verify_account(int(args))
        ok = "✅ 验签通过" if result.get("valid") else "⚠️ 验签未通过"
        yield event.plain_result(
            f"{ok}\n"
            f"模式：{result.get('auth_mode')}\n"
            f"说明：{result.get('message') or '—'}\n"
            f"接口：{result.get('endpoint') or '—'}"
        )

    @filter.command("闲鱼删除")
    @_guard
    async def cmd_delete(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        args = _strip_command(event.message_str)
        tokens = args.split()
        if not tokens or not tokens[0].isdigit():
            yield event.plain_result(
                "用法：/闲鱼删除 <账号ID>\n"
                "（会先列出账号信息，再发 /闲鱼删除 <账号ID> 确认 才会真正删除）"
            )
            return

        account_id = int(tokens[0])
        confirmed = len(tokens) > 1 and tokens[1] in ("确认", "confirm", "yes")
        sender = str(event.get_sender_id())

        if not confirmed:
            self._pending_delete[sender] = (time.monotonic(), account_id)
            accounts = await self.client.list_accounts()
            target = next((a for a in accounts if int(a.get("id", -1)) == account_id), None)
            if not target:
                yield event.plain_result(f"❌ 没有找到 ID 为 {account_id} 的闲鱼账号")
                return
            yield event.plain_result(
                f"⚠️ 即将解绑并删除闲鱼账号：\n"
                f"#{account_id} {target.get('seller_nick') or '未命名'}（{target.get('auth_mode')}）\n"
                f"该账号的登录凭据与连接日志会一起删除。\n"
                f"确认请发送：/闲鱼删除 {account_id} 确认\n"
                f"（60 秒内有效）"
            )
            return

        pending = self._pending_delete.get(sender)
        if not pending or pending[1] != account_id or time.monotonic() - pending[0] > 60:
            yield event.plain_result("⌛ 确认已超时，请重新发送 /闲鱼删除 <账号ID>")
            return

        self._pending_delete.pop(sender, None)
        await self.client.delete_account(account_id)
        yield event.plain_result(f"✅ 已删除闲鱼账号 #{account_id}")

    # ------------------------------------------------------------------ 指令：商品
    @filter.command("闲鱼商品")
    @_guard
    async def cmd_items(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        args = _strip_command(event.message_str)
        if not args.isdigit():
            accounts = await self.client.list_accounts()
            if len(accounts) == 1:
                args = str(accounts[0].get("id"))
            else:
                yield event.plain_result(
                    "用法：/闲鱼商品 <账号ID>\n（当前有多个账号，请指定 ID，用 /闲鱼账号 查看）"
                )
                return
        items = await self.client.list_items(int(args))
        yield event.plain_result(format_items(items, int(args)))

    @filter.command("闲鱼同步")
    @_guard
    async def cmd_sync(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        args = _strip_command(event.message_str)
        if not args.isdigit():
            yield event.plain_result("用法：/闲鱼同步 <账号ID>")
            return
        account_id = int(args)
        yield event.plain_result(f"⏳ 正在从闲鱼同步账号 #{account_id} 的商品…")
        result = await self.client.sync_items(account_id)
        items = result.get("items") or []
        yield event.plain_result(
            f"✅ 同步完成，共 {len(items)} 个商品（新增/更新 {result.get('synced_count', 0)} 个）\n"
            f"远端状态：{result.get('remote_status') or '—'}\n"
            f"发 /闲鱼商品 {account_id} 查看明细"
        )

    # ------------------------------------------------------------------ 指令：搜索
    @filter.command("闲鱼搜索", alias={"xy搜索", "闲鱼查价"})
    @_guard
    async def cmd_search(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._search_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        keyword, opts = parse_search_args(_strip_command(event.message_str))
        if not keyword:
            yield event.plain_result(
                "用法：/闲鱼搜索 <关键词> [--min 100] [--max 500] [--sort 价格升序] [--top 5]\n"
                "例如：/闲鱼搜索 switch\n"
                "      /闲鱼搜索 塞尔达 王国之泪 --max 300"
            )
            return

        sender = str(event.get_sender_id())
        now = time.monotonic()
        cooldown = self._cfg_num("search_cooldown", 3)
        if cooldown > 0:
            elapsed = now - self._last_search.get(sender, 0)
            if elapsed < cooldown:
                yield event.plain_result(f"⏳ 搜得太快了，{cooldown - elapsed:.0f} 秒后再试")
                return

        self._last_search[sender] = now
        filter_note = []
        if opts.get("min_price_yuan") is not None:
            filter_note.append(f"≥{money(opts['min_price_yuan'])}")
        if opts.get("max_price_yuan") is not None:
            filter_note.append(f"≤{money(opts['max_price_yuan'])}")
        suffix = f"（{'、'.join(filter_note)}）" if filter_note else ""
        yield event.plain_result(f"🔍 正在搜「{keyword}」{suffix}…")

        data = await self.client.search(
            keyword,
            account_id=opts.get("account_id"),
            min_price_yuan=opts.get("min_price_yuan"),
            max_price_yuan=opts.get("max_price_yuan"),
            sort_by=opts.get("sort_by"),
        )
        stats = data.get("stats") or {}
        items = data.get("items") or []

        if not items:
            yield event.plain_result(
                f"😕 「{keyword}」没有搜到结果{'' if not suffix else suffix}，换个关键词或放宽价格试试"
            )
            return

        summary = await self._summarize(event, keyword, stats, items, opts)
        top_n = int(opts.get("top_n") or self._cfg_num("search_top_n", 5))
        yield event.plain_result(summary)
        yield event.plain_result(build_item_lines(items, top_n))

        # 主动推送到配置的其他会话（例如群指令触发、摘要回到自己的 QQ）
        targets = [t for t in self._load_push_targets() if t != event.unified_msg_origin]
        for umo in targets:
            await self._push(umo, f"🐟 来自 {event.get_sender_name()} 的搜索：{keyword}\n\n{summary}")

    async def _summarize(
        self,
        event: AstrMessageEvent,
        keyword: str,
        stats: dict,
        items: list,
        opts: dict,
    ) -> str:
        fallback = build_fallback_summary(keyword, stats, items)
        if not self._cfg("enable_llm_summary"):
            return fallback

        try:
            provider = await self.context.get_using_provider_async(
                umo=event.unified_msg_origin
            )
        except Exception as exc:
            logger.warning(f"[xianyu-autodeliver] 获取模型提供商失败: {exc}")
            provider = None
        if provider is None:
            return fallback

        prompt = build_llm_prompt(keyword, stats, items, opts)
        timeout = self._cfg_num("summary_timeout", 45)
        try:
            response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, system_prompt=SUMMARY_SYSTEM_PROMPT),
                timeout=timeout,
            )
            text = _plainify(getattr(response, "completion_text", "") or "")
        except Exception as exc:
            logger.warning(f"[xianyu-autodeliver] 大模型总结失败，改用模板摘要: {exc}")
            text = ""

        if not text:
            return fallback
        # 模型偶尔会漏掉统计数字，这里补一行硬数据，保证可核对
        return (
            f"{text}\n\n"
            f"— 数据：样本 {stats.get('sample_count', 0)} 条，中位价 {money(stats.get('median_price_yuan'))}，"
            f"区间 {money(stats.get('min_price_yuan'))}~{money(stats.get('max_price_yuan'))}"
        )

    # ------------------------------------------------------------------ 指令：发货 / 订单
    @filter.command("闲鱼发货", alias={"闲鱼巡检"})
    @_guard
    async def cmd_scan(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        args = _strip_command(event.message_str)
        account_id = int(args) if args.isdigit() else None
        yield event.plain_result("🚚 正在巡检闲鱼会话并执行自动发货，这一步可能要几十秒…")
        result = await self.client.scan_auto_deliver(account_id)
        yield event.plain_result(format_scan_result(result))

    @filter.command("闲鱼订单", alias={"闲鱼订单统计"})
    @_guard
    async def cmd_orders(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return
        stats = await self.client.order_stats()
        yield event.plain_result(
            "📦 订单统计\n"
            f"累计 {stats.get('total', 0)}｜今日 {stats.get('today', 0)}\n"
            f"待发货 {stats.get('pending', 0)}｜已发货 {stats.get('delivered', 0)}\n"
            f"失败 {stats.get('failed', 0)}｜退款 {stats.get('refunded', 0)}\n"
            f"成交金额 {money(stats.get('revenue', 0))}｜今日 {money(stats.get('today_revenue', 0))}"
        )

    # ------------------------------------------------------------------ 指令：推送目标
    @filter.command("闲鱼推送")
    @_guard
    async def cmd_push(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if (reason := self._admin_denied(event)) is not None:
            yield event.plain_result(reason)
            return

        action = _strip_command(event.message_str).strip()
        umo = event.unified_msg_origin
        targets = self._load_push_targets()

        if action in ("绑定", "添加", "add", "on", "开"):
            if umo not in targets:
                targets.append(umo)
                self._save_push_targets(targets)
            yield event.plain_result(
                f"✅ 已把本会话加入推送目标（{len(targets)} 个）\n"
                "之后在别的群里搜到的东西，摘要会额外推到这里。"
            )
            return
        if action in ("取消", "移除", "remove", "off", "关"):
            targets = [t for t in targets if t != umo]
            self._save_push_targets(targets)
            yield event.plain_result(f"✅ 已把本会话从推送目标移除（剩余 {len(targets)} 个）")
            return
        if action in ("清空", "clear"):
            self._save_push_targets([])
            yield event.plain_result("✅ 推送目标已清空")
            return

        lines = ["📮 推送目标："]
        lines.extend(f"  · {t}" for t in targets) if targets else lines.append("  （空）")
        lines.append("\n/闲鱼推送 绑定｜取消｜清空")
        yield event.plain_result("\n".join(lines))
