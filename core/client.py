"""哔咔漫画接口客户端（基于 aiohttp）。

封装了签名、登录、鉴权与线路切换，对外只暴露语义化的异步方法。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import aiohttp
from astrbot.api import logger

from .constants import (
    ACCEPT_JSON,
    API_BASES,
    APP_CHANNEL,
    APP_PLATFORM,
    APP_UUID,
    APP_VERSION,
    AUTH_ERROR_CODES,
    DEFAULT_API_BASE,
    DEFAULT_UA,
    EPS_PAGE_LIMIT,
    IMAGE_QUALITIES,
    PAGES_PAGE_LIMIT,
    PICA_API_KEY,
    RANK_CATEGORY,
    WEB_REFERER,
)
from .util import build_image_url, build_signature, new_nonce, new_time


class BikaError(Exception):
    """哔咔接口调用失败。"""

    def __init__(self, message: str, code: Any = None, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint


class BikaAuthError(BikaError):
    """登录态失效 / 账号密码错误。"""


class BikaNetworkError(BikaError):
    """网络不可达 / 被 Cloudflare 拦截。"""


def decode_token_payload(token: str) -> dict:
    """解析 JWT 的 payload（不做签名校验，仅用于取过期时间与昵称）。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class BikaClient:
    """哔咔接口客户端。

    一个实例对应一个登录态；插件里全局复用一个实例。
    """

    def __init__(
        self,
        account: str = "",
        password: str = "",
        api_base: str = "",
        proxy: str = "",
        timeout: int = 30,
        image_quality: str = "medium",
        max_retry: int = 2,
    ) -> None:
        self.account = (account or "").strip()
        self.password = password or ""
        self.timeout = max(5, int(timeout or 30))
        self.image_quality = image_quality if image_quality in IMAGE_QUALITIES else "medium"
        self.max_retry = max(0, int(max_retry or 0))
        self.proxy = (proxy or "").strip()

        self._api_base = (api_base or DEFAULT_API_BASE).strip().rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._login_lock = asyncio.Lock()

        self.token: str = ""
        self.token_expire_at: int = 0
        self.user: dict = {}

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    @property
    def api_base(self) -> str:
        return self._api_base

    @property
    def mirror_domain(self) -> str:
        """当前线路的根域名，用于图片镜像替换。"""
        from .util import domain_of

        return domain_of(self._api_base)

    def _candidate_bases(self) -> list[str]:
        bases = [self._api_base]
        for base in API_BASES:
            if base not in bases:
                bases.append(base)
        return bases

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout, connect=12)
            kwargs: dict[str, Any] = {"timeout": timeout}
            if self.proxy:
                kwargs["proxy"] = self.proxy
            self._session = aiohttp.ClientSession(**kwargs)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------
    # 登录态
    # ------------------------------------------------------------------
    @property
    def token_valid(self) -> bool:
        if not self.token:
            return False
        if self.token_expire_at <= 0:
            return True
        return self.token_expire_at > int(time.time()) + 120

    def load_token(self, token: str, expire_at: int = 0) -> None:
        """从本地缓存恢复登录态。"""
        self.token = (token or "").strip()
        if expire_at:
            self.token_expire_at = int(expire_at)
        elif self.token:
            self.token_expire_at = int(decode_token_payload(self.token).get("exp") or 0)

    def token_payload(self) -> dict:
        return decode_token_payload(self.token) if self.token else {}

    async def ensure_login(self, force: bool = False) -> str:
        """确保处于登录状态，返回 token。"""
        if not force and self.token_valid:
            return self.token
        async with self._login_lock:
            if not force and self.token_valid:
                return self.token
            await self.login()
            return self.token

    async def login(self) -> str:
        """账号密码登录。"""
        if not self.account or not self.password:
            raise BikaAuthError(
                "未配置哔咔账号或密码",
                hint="请在插件配置中填写「账号」与「密码」后重载插件",
            )
        data = await self._request(
            "POST",
            "auth/sign-in",
            body={"email": self.account, "password": self.password},
            auth=False,
        )
        token = (data.get("data") or {}).get("token") or ""
        if not token:
            raise BikaAuthError("登录失败：响应中没有 token")
        self.token = token
        self.token_expire_at = int(decode_token_payload(token).get("exp") or 0)
        self.user = self.token_payload()
        logger.info(
            "哔咔登录成功：%s（%s）",
            self.user.get("name") or self.account,
            self.user.get("email") or "",
        )
        return token

    # ------------------------------------------------------------------
    # 请求
    # ------------------------------------------------------------------
    def _headers(self, path: str, method: str, with_token: bool) -> dict[str, str]:
        ts = new_time()
        nonce = new_nonce()
        headers = {
            "api-key": PICA_API_KEY,
            "app-channel": APP_CHANNEL,
            "app-uuid": APP_UUID,
            "app-version": APP_VERSION,
            "app-platform": APP_PLATFORM,
            "accept": ACCEPT_JSON,
            "content-type": "application/json; charset=UTF-8",
            "time": ts,
            "nonce": nonce,
            "image-quality": self.image_quality,
            "signature": build_signature(path, method, ts, nonce),
            "referer": WEB_REFERER,
            "user-agent": DEFAULT_UA,
        }
        if with_token and self.token:
            headers["authorization"] = self.token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        auth: bool = True,
        _relogin: bool = False,
    ) -> dict:
        """发起一次接口请求，自动处理线路切换与重新登录。"""
        if auth:
            await self.ensure_login()

        last_error: Exception | None = None
        for base in self._candidate_bases():
            try:
                result = await self._request_once(base, method, path, body, auth)
            except BikaAuthError:
                if auth and not _relogin:
                    logger.info("哔咔登录态失效，尝试重新登录")
                    await self.ensure_login(force=True)
                    return await self._request(method, path, body, auth=auth, _relogin=True)
                raise
            except BikaNetworkError as exc:
                last_error = exc
                if base != self._api_base:
                    logger.warning("哔咔线路 %s 不可用：%s", base, exc.message)
                continue

            if base != self._api_base:
                logger.info("哔咔接口切换到备用线路 %s", base)
                self._api_base = base
            return result

        raise BikaNetworkError(
            f"所有线路均不可用：{last_error.message if last_error else '未知网络错误'}",
            hint="请检查网络连接，或在插件配置中填写代理地址（如 http://127.0.0.1:7897）",
        )

    async def _request_once(
        self,
        base: str,
        method: str,
        path: str,
        body: dict | None,
        auth: bool,
    ) -> dict:
        url = f"{base}/{path}"
        session = self._get_session()
        try:
            async with session.request(
                method,
                url,
                headers=self._headers(path, method, auth),
                json=body if method != "GET" else None,
                **({"proxy": self.proxy} if self.proxy else {}),
            ) as resp:
                text = await resp.text(errors="ignore")
                status = resp.status
        except asyncio.TimeoutError as exc:
            raise BikaNetworkError("请求超时") from exc
        except aiohttp.ClientError as exc:
            raise BikaNetworkError(f"网络错误：{exc}") from exc

        low = text.lstrip().lower()
        if low.startswith("<!doctype") or "just a moment" in low[:600]:
            raise BikaNetworkError(
                "被 Cloudflare 拦截（403 Just a moment）",
                hint="nonce 格式或请求头不符合要求，请升级插件；也可尝试配置代理",
            )

        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BikaNetworkError(f"响应解析失败（HTTP {status}）") from exc
        if not isinstance(result, dict):
            raise BikaNetworkError(f"响应格式异常（HTTP {status}）")

        code = result.get("code", -1)
        if code == 200:
            return result

        message = str(result.get("message") or "未知错误")
        error = str(result.get("error") or "").strip()
        if status == 401 or error in AUTH_ERROR_CODES:
            raise BikaAuthError(
                f"登录态无效：{message}",
                code=error or status,
                hint="请检查账号密码，或使用 /bika 登录 重新登录",
            )
        if status == 403:
            raise BikaNetworkError(
                f"接口拒绝访问（403）：{message}",
                hint="该线路可能被风控，尝试更换线路或配置代理",
            )
        raise BikaError(f"接口返回错误：{message}（code={code}）", code=error or code)

    # ------------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------------
    async def leaderboard(self, tt: str = "H24") -> list[dict]:
        """排行榜（tt: H24 / D7 / D30）。"""
        result = await self._request("GET", f"comics/leaderboard?tt={tt}&ct={RANK_CATEGORY}")
        comics = (result.get("data") or {}).get("comics") or []
        if not isinstance(comics, list):
            comics = []
        return comics

    async def categories(self) -> list[dict]:
        result = await self._request("GET", "categories")
        data = (result.get("data") or {}).get("categories") or []
        return data if isinstance(data, list) else []

    async def search(self, keyword: str, page: int = 1, sort: str = "ua") -> dict:
        """搜索漫画，返回 ``{docs, total, pages, page}``。"""
        payload = {"keyword": keyword, "categories": [], "sort": sort}
        result = await self._request(
            "POST", f"comics/advanced-search?page={max(1, page)}", body=payload
        )
        return self._extract_comic_list(result)

    async def comic(self, comic_id: str) -> dict:
        result = await self._request("GET", f"comics/{comic_id}")
        comic = (result.get("data") or {}).get("comic") or {}
        if not comic:
            raise BikaError("未找到该漫画（可能已被下架或封禁）")
        return comic

    async def episodes(self, comic_id: str, page: int = 1) -> dict:
        result = await self._request("GET", f"comics/{comic_id}/eps?page={max(1, page)}")
        eps = (result.get("data") or {}).get("eps") or {}
        docs = eps.get("docs") or []
        return {
            "docs": docs if isinstance(docs, list) else [],
            "total": int(eps.get("total") or 0),
            "page": int(eps.get("page") or page),
            "pages": int(eps.get("pages") or 1),
            "limit": int(eps.get("limit") or EPS_PAGE_LIMIT),
        }

    async def all_episodes(self, comic_id: str) -> list[dict]:
        """拉取全部章节（自动翻页，按 order 升序）。"""
        first = await self.episodes(comic_id, 1)
        docs: list[dict] = list(first["docs"])
        total_pages = first["pages"] or 1
        # 避免异常数据导致死循环
        total_pages = min(total_pages, 50)
        for page in range(2, total_pages + 1):
            part = await self.episodes(comic_id, page)
            if not part["docs"]:
                break
            docs.extend(part["docs"])
        docs.sort(key=lambda x: int(x.get("order") or 0))
        return docs

    async def pages(self, comic_id: str, order: int, page: int = 1) -> dict:
        result = await self._request(
            "GET", f"comics/{comic_id}/order/{order}/pages?page={max(1, page)}"
        )
        pages = (result.get("data") or {}).get("pages") or {}
        docs = pages.get("docs") or []
        return {
            "docs": docs if isinstance(docs, list) else [],
            "total": int(pages.get("total") or 0),
            "page": int(pages.get("page") or page),
            "pages": int(pages.get("pages") or 1),
            "limit": int(pages.get("limit") or PAGES_PAGE_LIMIT),
        }

    async def all_pages(self, comic_id: str, order: int) -> list[dict]:
        """拉取某一话的全部图片，保持阅读顺序。"""
        first = await self.pages(comic_id, order, 1)
        docs: list[dict] = list(first["docs"])
        total_pages = min(int(first["pages"] or 1), 200)
        for page in range(2, total_pages + 1):
            part = await self.pages(comic_id, order, page)
            if not part["docs"]:
                break
            docs.extend(part["docs"])
        return docs

    @staticmethod
    def page_image_url(page: dict) -> str:
        media = page.get("media") or {}
        return build_image_url(media.get("fileServer") or "", media.get("path") or "")

    # ------------------------------------------------------------------
    # 收藏 / 签到
    # ------------------------------------------------------------------
    async def toggle_favourite(self, comic_id: str) -> str:
        """收藏 / 取消收藏，返回 ``favourite`` 或 ``un_favourite``。"""
        result = await self._request("POST", f"comics/{comic_id}/favourite")
        return str((result.get("data") or {}).get("action") or "")

    async def favourites(self, page: int = 1, sort: str = "ua") -> dict:
        """我的收藏列表。"""
        result = await self._request("GET", f"users/favourite?s={sort}&page={max(1, page)}")
        return self._extract_comic_list(result)

    async def punch_in(self) -> dict:
        """每日签到，返回 ``{"status": "ok"|"fail", ...}``。"""
        result = await self._request("POST", "users/punch-in")
        data = result.get("data") or {}
        res = data.get("res") if isinstance(data.get("res"), dict) else data
        return res if isinstance(res, dict) else {}

    @staticmethod
    def _extract_comic_list(result: dict) -> dict:
        comics = (result.get("data") or {}).get("comics") or {}
        docs = comics.get("docs") or []
        return {
            "docs": docs if isinstance(docs, list) else [],
            "total": int(comics.get("total") or 0),
            "page": int(comics.get("page") or 1),
            "pages": int(comics.get("pages") or 1),
        }

    # ------------------------------------------------------------------
    # 图片下载
    # ------------------------------------------------------------------
    async def download_image(self, url: str, referer: str = "") -> bytes:
        """下载图片，失败时自动重试。"""
        session = self._get_session()
        headers = {
            "user-agent": DEFAULT_UA,
            "accept": "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8",
            "referer": referer or WEB_REFERER,
        }
        last_error: Exception | None = None
        # 图片重试次数比接口更保守，避免坏图把整话拖慢
        attempts = 1 + min(self.max_retry, 1)
        for attempt in range(attempts):
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=min(self.timeout, 25), connect=10),
                    **({"proxy": self.proxy} if self.proxy else {}),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if data:
                            return data
                        last_error = BikaError("图片内容为空")
                    else:
                        last_error = BikaError(f"图片下载失败（HTTP {resp.status}）")
            except asyncio.TimeoutError as exc:
                last_error = BikaError("图片下载超时")
                _ = exc
            except aiohttp.ClientError as exc:
                last_error = BikaError(f"图片下载网络错误：{exc}")
            if attempt < attempts - 1:
                await asyncio.sleep(0.6 * (attempt + 1))
        raise BikaError(str(last_error) if last_error else "图片下载失败")
