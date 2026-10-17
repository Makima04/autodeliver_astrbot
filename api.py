"""AutoDeliver 管理后台接口的异步客户端。

设计要点：
- 只依赖 httpx，不 import astrbot，便于脱离 AstrBot 单独跑冒烟测试；
- Token 失效（401）时自动用配置的管理员账号重新登录并重试一次；
- 所有异常统一收敛为 ``ApiError``，把后端 ``{"code","message"}`` 的 message 透出来，
  这样 QQ 里看到的就是「尚未绑定闲鱼账号」这类人话，而不是 500。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

import httpx


class ApiError(Exception):
    """AutoDeliver 接口错误。"""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志展示
        if self.status_code:
            return f"[{self.status_code}] {self.message}"
        return self.message


class AutoDeliverClient:
    """AutoDeliver 后台 API 封装（全部为协程方法）。"""

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 30.0,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.base_url = (base_url or "http://127.0.0.1:8080").rstrip("/")
        self.username = (username or "").strip() or None
        self.password = password or None
        self.timeout = timeout
        self._token: Optional[str] = (token or "").strip() or None
        self._on_token = on_token
        self._login_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            headers={"Accept": "application/json"},
        )

    # ------------------------------------------------------------------ 基础
    @property
    def token(self) -> Optional[str]:
        return self._token

    def set_token(self, token: Optional[str], persist: bool = True) -> None:
        self._token = (token or "").strip() or None
        if persist and self._token and self._on_token:
            try:
                self._on_token(self._token)
            except Exception:  # 持久化失败不影响主流程
                pass

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Any = None,
        auth: bool = True,
        _retry: bool = True,
    ) -> Any:
        headers = {}
        if json is not None:
            headers["Content-Type"] = "application/json"
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            resp = await self._http.request(
                method, path, json=json, params=params, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise ApiError(f"请求 AutoDeliver 超时（{self.base_url}）：{exc}") from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                f"连不上 AutoDeliver（{self.base_url}），请确认后台已启动：{exc}"
            ) from exc

        # 会话过期：自动重新登录后重试一次
        if resp.status_code == 401 and auth and _retry:
            await self.login()
            return await self._request(
                method, path, json=json, params=params, auth=auth, _retry=False
            )

        if resp.status_code >= 400:
            raise ApiError(self._error_message(resp), resp.status_code, self._safe_json(resp))

        if not resp.content:
            return None
        return self._safe_json(resp)

    @staticmethod
    def _safe_json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text[:500]}

    def _error_message(self, resp: httpx.Response) -> str:
        data = self._safe_json(resp)
        if isinstance(data, dict):
            for key in ("message", "error", "detail", "raw"):
                value = data.get(key)
                if value:
                    return str(value)
        return f"HTTP {resp.status_code}"

    async def ensure_token(self) -> None:
        """没有 token 时用配置的账号密码登录一次。"""
        if self._token:
            return
        if not (self.username and self.password):
            raise ApiError("尚未登录 AutoDeliver：请先执行 /闲鱼登录 <用户名> <密码>，或在插件配置中填写账号密码 / 静态 Token")
        await self.login()

    # ------------------------------------------------------------------ 认证
    async def login(self, username: Optional[str] = None, password: Optional[str] = None) -> dict:
        """管理员登录，成功后缓存并持久化 token。"""
        user = (username or self.username or "").strip()
        pwd = password or self.password or ""
        if not user or not pwd:
            raise ApiError("登录需要用户名与密码：/闲鱼登录 <用户名> <密码>")

        async with self._login_lock:
            data = await self._request(
                "POST",
                "/api/auth/login",
                json={"username": user, "password": pwd},
                auth=False,
                _retry=False,
            )
        token = (data or {}).get("token")
        if not token:
            raise ApiError("登录失败：后端未返回 token")
        self.username = user
        self.password = pwd
        self.set_token(token)
        return data

    async def me(self) -> dict:
        await self.ensure_token()
        return await self._request("GET", "/api/auth/me")

    async def logout(self) -> Any:
        if not self._token:
            return None
        try:
            return await self._request("POST", "/api/auth/logout", _retry=False)
        finally:
            self.set_token(None)

    # ------------------------------------------------------------------ 闲鱼账号
    async def list_accounts(self) -> list:
        await self.ensure_token()
        return await self._request("GET", "/api/xianyu/accounts") or []

    async def get_account_status(self) -> dict:
        await self.ensure_token()
        return await self._request("GET", "/api/xianyu/account") or {}

    async def delete_account(self, account_id: int) -> dict:
        await self.ensure_token()
        return await self._request("DELETE", f"/api/xianyu/accounts/{int(account_id)}")

    async def verify_account(self, account_id: int) -> dict:
        await self.ensure_token()
        return await self._request("POST", f"/api/xianyu/accounts/{int(account_id)}/verify")

    async def login_cookie(
        self,
        raw_cookie: str,
        seller_nick: Optional[str] = None,
    ) -> dict:
        """Cookie 登录：写入 / 更新一个闲鱼账号（后端按 seller_id 复用同一账号）。"""
        await self.ensure_token()
        payload: dict[str, Any] = {
            "auth_mode": "cookie",
            "raw_cookie": raw_cookie.strip(),
        }
        if seller_nick:
            payload["seller_nick"] = seller_nick
        return await self._request("POST", "/api/xianyu/login", json=payload)

    async def login_open_api(
        self,
        app_key: str,
        app_secret: str,
        access_token: str,
    ) -> dict:
        await self.ensure_token()
        payload: dict[str, Any] = {
            "auth_mode": "open_api",
            "app_key": app_key,
            "app_secret": app_secret,
            "access_token": access_token,
        }
        return await self._request("POST", "/api/xianyu/login", json=payload)

    # ------------------------------------------------------------------ 扫码登录
    async def qrcode_generate(self) -> dict:
        await self.ensure_token()
        return await self._request("POST", "/api/xianyu/qrcode/generate")

    async def qrcode_poll(
        self, t: str, ck: str, session_cookies: Optional[str] = None
    ) -> dict:
        await self.ensure_token()
        return await self._request(
            "POST",
            "/api/xianyu/qrcode/poll",
            json={"t": t, "ck": ck, "session_cookies": session_cookies},
        )

    # ------------------------------------------------------------------ 商品 / 搜索
    async def search(
        self,
        keyword: str,
        account_id: Optional[int] = None,
        min_price_yuan: Optional[float] = None,
        max_price_yuan: Optional[float] = None,
        sort_by: Optional[str] = None,
    ) -> dict:
        await self.ensure_token()
        payload: dict[str, Any] = {"keyword": keyword.strip()}
        if account_id:
            payload["account_id"] = int(account_id)
        if min_price_yuan is not None:
            payload["min_price_yuan"] = float(min_price_yuan)
        if max_price_yuan is not None:
            payload["max_price_yuan"] = float(max_price_yuan)
        if sort_by:
            payload["sort_by"] = sort_by
        return await self._request("POST", "/api/xianyu/search", json=payload) or {}

    async def list_items(self, account_id: int) -> list:
        await self.ensure_token()
        return await self._request("GET", f"/api/xianyu/accounts/{int(account_id)}/items") or []

    async def sync_items(self, account_id: int) -> dict:
        await self.ensure_token()
        return (
            await self._request("POST", f"/api/xianyu/accounts/{int(account_id)}/items/sync")
            or {}
        )

    # ------------------------------------------------------------------ 发货 / 订单
    async def scan_auto_deliver(self, account_id: Optional[int] = None) -> dict:
        await self.ensure_token()
        payload = {"account_id": int(account_id)} if account_id else {}
        return await self._request("POST", "/api/xianyu/auto-deliver/scan", json=payload) or {}

    async def order_stats(self) -> dict:
        await self.ensure_token()
        return await self._request("GET", "/api/orders/stats") or {}
