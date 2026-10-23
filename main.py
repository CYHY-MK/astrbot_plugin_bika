"""哔咔漫画 AstrBot 插件。

功能
----
* 排行榜（日榜 / 周榜 / 月榜）
* 关键词搜索、漫画详情、章节列表
* 按章节下载漫画图片，并发送到当前群聊 / 私聊（也支持推送到配置里的其它会话）
* 打包方式：逐张图片 / ZIP / PDF / 长图

依赖：aiohttp（AstrBot 自带）、Pillow（PDF、长图、封面拼图需要）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .core.client import BikaAuthError, BikaClient, BikaError, BikaNetworkError
from .core.constants import PACK_FORMATS, RANK_LABELS, RANK_TYPES, SORTS
from .core.downloader import ChapterDownloader, ChapterResult
from .core.formatter import (
    comic_title,
    format_categories,
    format_category_comics,
    format_detail,
    format_episodes,
    format_favourites,
    format_rank,
    format_search,
)
from .core.packer import (
    long_image_from_images,
    pdf_from_images_parts,
    pillow_available,
    prepare_for_send,
    rank_collage,
    zip_images,
)
from .core.util import (
    human_size,
    safe_filename,
    to_mirror_url,
)

PLUGIN_NAME = "astrbot_plugin_bika"

COMMAND_NAMES = {"bika", "哔咔", "bkr", "pica"}

HELP_TEXT = """📚 哔咔漫画插件 · 指令一览

/bika 排行 [日|周|月] [数量]
    查看排行榜（默认日榜前 10）
/bika 分区 [序号|名称] [排序] [页码]
    浏览分区；不带参数时列出全部分区
/bika 搜索 <关键词> [页码]
    搜索漫画
/bika 详情 <漫画ID|本页序号>
    查看漫画详情
/bika 章节 <漫画ID|本页序号> [页码]
    查看章节列表
/bika 下载 <漫画ID|本页序号> [章节] [格式] [@目标]
    下载并发送漫画
    · 章节：1 / 1-3 / 1,3,5 / 最新3 / all（默认最新 1 话）
    · 格式：图片（默认）/ zip / pdf / 长图
    · 目标：@别名 / @g群号 / @uQQ号，缺省发到当前会话
/bika 状态
    查看登录状态、线路、推送与缓存占用
/bika 登录
    用配置里的账号重新登录
/bika 收藏 <漫画ID|本页序号>
    收藏 / 取消收藏
/bika 我的收藏 [页码]
    查看收藏夹
/bika 签到
    哔咔每日签到
/bika 推送测试 [日|周|月]
    立即向 push_targets 推送一次排行榜（验证定时推送配置）

示例：
    /bika 排行 周
    /bika 分区
    /bika 分区 長篇 最多爱心
    /bika 搜索 女仆
    /bika 下载 3 1-2 zip
    /bika 收藏 1
    /bika 签到
"""


class BikaPlugin(Star):
    """哔咔漫画：排行榜、分区、搜索、下载与发送。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 数据目录：data/plugin_data/astrbot_plugin_bika
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            base = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        except Exception:  # pragma: no cover - 兼容旧版本
            base = Path.cwd() / "data" / "plugin_data" / PLUGIN_NAME
        self.data_dir = base
        self.download_dir = self.data_dir / "downloads"
        self.pack_dir = self.data_dir / "packs"
        self.session_file = self.data_dir / "session.json"
        for path in (self.download_dir, self.pack_dir):
            path.mkdir(parents=True, exist_ok=True)

        self._client: BikaClient | None = None
        self._client_signature: tuple = ()
        self._saved_token = ""
        # 每个会话最近一次排行榜 / 搜索结果，用于「序号」引用
        self._last_results: dict[str, dict] = {}
        # 每个会话最近一次分区列表，用于「/bika 分区 序号」引用
        self._last_categories: dict[str, tuple[float, list[dict]]] = {}
        self._cleanup_task: asyncio.Task | None = None
        self._bg_tasks: set[asyncio.Task] = set()
        self._last_send_at: float = 0.0
        self._push_state_file = self.data_dir / "push_state.json"
        self._schedule_task: asyncio.Task | None = None

        self._load_session()

    # ==================================================================
    # 生命周期 / 配置
    # ==================================================================
    async def initialize(self) -> None:
        """插件加载完成后异步预热（不阻塞启动）。"""
        try:
            self._cleanup_cache()
        except Exception as exc:  # pragma: no cover
            logger.warning("清理哔咔缓存失败：%s", exc)
        # 排行榜定时推送
        if self._schedule_task is None or self._schedule_task.done():
            self._schedule_task = asyncio.create_task(self._schedule_loop())
            self._bg_tasks.add(self._schedule_task)
            self._schedule_task.add_done_callback(self._bg_tasks.discard)

    async def terminate(self) -> None:
        """插件卸载 / 重载时关闭连接。"""
        if self._schedule_task and not self._schedule_task.done():
            self._schedule_task.cancel()
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

    def _cfg(self, key: str, default=None):
        value = self.config.get(key, default)
        return default if value is None else value

    @property
    def account(self) -> str:
        return str(self._cfg("account", "")).strip()

    @property
    def password(self) -> str:
        return str(self._cfg("password", ""))

    def _client_options(self) -> dict:
        return {
            "account": self.account,
            "password": self.password,
            "api_base": str(self._cfg("api_base", "")).strip(),
            "proxy": str(self._cfg("proxy", "")).strip(),
            "timeout": int(self._cfg("timeout", 30)),
            "image_quality": str(self._cfg("image_quality", "medium")),
            "max_retry": int(self._cfg("max_retry", 2)),
        }

    def _ensure_client(self) -> BikaClient:
        """按当前配置获取客户端；配置变化时自动重建。"""
        options = self._client_options()
        signature = tuple(sorted(options.items()))
        if self._client is not None and signature == self._client_signature:
            return self._client

        if self._client is not None:
            task = asyncio.create_task(self._client.close())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        client = BikaClient(**options)
        if self._saved_token and self._session_account == options["account"]:
            client.load_token(self._saved_token, self._session_expire_at)
        self._client = client
        self._client_signature = signature
        return client

    # ------------------------------------------------------------------
    # 登录态持久化
    # ------------------------------------------------------------------
    def _load_session(self) -> None:
        self._saved_token = ""
        self._session_account = ""
        self._session_expire_at = 0
        try:
            if self.session_file.exists():
                data = json.loads(self.session_file.read_text("utf-8"))
                self._saved_token = str(data.get("token") or "")
                self._session_account = str(data.get("account") or "")
                self._session_expire_at = int(data.get("expire_at") or 0)
        except Exception as exc:
            logger.warning("读取哔咔登录缓存失败：%s", exc)

    def _save_session(self) -> None:
        if self._client is None or not self._client.token:
            return
        try:
            self.session_file.write_text(
                json.dumps(
                    {
                        "account": self._client.account,
                        "token": self._client.token,
                        "expire_at": self._client.token_expire_at,
                        "saved_at": int(time.time()),
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
            self._saved_token = self._client.token
            self._session_account = self._client.account
            self._session_expire_at = self._client.token_expire_at
        except Exception as exc:
            logger.warning("保存哔咔登录缓存失败：%s", exc)

    # ==================================================================
    # 通用工具
    # ==================================================================
    def _split_args(self, event: AstrMessageEvent) -> list[str]:
        text = (event.message_str or "").strip()
        text = text.lstrip("/／!！")
        parts = [p for p in re.split(r"\s+", text) if p]
        if parts and parts[0].lower() in COMMAND_NAMES:
            parts = parts[1:]
        return parts

    def _permission_error(self, event: AstrMessageEvent) -> str | None:
        is_group = bool(event.get_group_id())
        if is_group and not bool(self._cfg("allow_group", False)):
            return "❌ 已关闭群聊使用，请在私聊中使用。"
        if not is_group and not bool(self._cfg("allow_private", True)):
            return "❌ 已关闭私聊使用，请在群聊中使用。"
        if not bool(self._cfg("admin_only", False)):
            return None
        admin_ids = {
            item.strip()
            for item in str(self._cfg("admin_ids", "")).replace("，", ",").split(",")
            if item.strip()
        }
        sender_id = str(event.get_sender_id() or "")
        if event.is_admin() or (sender_id and sender_id in admin_ids):
            return None
        return "❌ 该插件已开启仅管理员可用。"

    def _push_targets(self) -> dict[str, str]:
        targets: dict[str, str] = {}
        raw = self._cfg("push_targets", []) or []
        if isinstance(raw, str):
            raw = [raw]
        for item in raw:
            if not isinstance(item, str) or "=" not in item:
                continue
            alias, umo = item.split("=", 1)
            alias, umo = alias.strip(), umo.strip()
            if alias and umo:
                targets[alias] = umo
        return targets

    def _map_image_url(self, url: str) -> str:
        mode = str(self._cfg("image_host", "镜像")).lower()
        if mode in {"直连", "direct", "origin"}:
            return url
        client = self._client
        domain = client.mirror_domain if client else ""
        return to_mirror_url(url, domain) if domain else url

    def _remember(self, event: AstrMessageEvent, kind: str, docs: list[dict]) -> None:
        self._last_results[event.unified_msg_origin] = {
            "kind": kind,
            "docs": docs,
            "ts": time.time(),
        }

    def _resolve_comic_id(self, event: AstrMessageEvent, token: str) -> str:
        """把用户输入解析成漫画 ID：对象 ID 直接返回，否则按上一条结果的序号取。"""
        token = (token or "").strip()
        if re.fullmatch(r"[0-9a-fA-F]{16,32}", token):
            return token
        if token.isdigit():
            cached = self._last_results.get(event.unified_msg_origin)
            if cached and time.time() - cached["ts"] < 1800:
                docs = cached["docs"]
                index = int(token) - 1
                if 0 <= index < len(docs):
                    comic_id = docs[index].get("_id")
                    if comic_id:
                        return str(comic_id)
            raise BikaError(
                f"序号 {token} 无法解析",
                hint="先发一次「/bika 排行」或「/bika 搜索 关键词」，再用序号引用",
            )
        raise BikaError("漫画 ID 格式不正确", hint="ID 形如 6ad24b390ce6506fcecf8398")

    async def _send_raw(
        self,
        event: AstrMessageEvent,
        target: str,
        chain: list,
    ) -> None:
        """真正执行一次发送（可能抛异常）。"""
        if target and target != event.unified_msg_origin:
            await self.context.send_message(target, MessageChain(chain=chain))
        else:
            await event.send(MessageChain(chain=chain))

    async def _throttle(self) -> None:
        """两次发送之间保持最小间隔，降低被 QQ 风控/限流的概率。"""
        interval = float(self._cfg("send_interval", 1.2) or 0)
        if interval <= 0:
            return
        delta = time.monotonic() - self._last_send_at
        if delta < interval:
            await asyncio.sleep(interval - delta)
        self._last_send_at = time.monotonic()

    async def _send(
        self,
        event: AstrMessageEvent,
        target: str,
        chain: list,
        *,
        retries: int = 1,
        allow_text_fallback: bool = True,
    ) -> bool:
        """发送消息链，失败自动重试；图片/文件发不出去时退化为纯文本。

        QQ 侧偶发 ``retcode=100 / 未返回消息回执``（多为频率限制或图片被拦），
        这里全部吞掉并记录日志，避免一条消息失败导致整个下载任务中断。

        Returns:
            是否发送成功（含降级成功）
        """
        await self._throttle()
        for attempt in range(max(0, retries) + 1):
            try:
                await self._send_raw(event, target, chain)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "哔咔消息发送失败（第 %s/%s 次，%s）：%s",
                    attempt + 1,
                    retries + 1,
                    type(exc).__name__,
                    exc,
                )
                if attempt < retries:
                    await asyncio.sleep(2.0 + attempt)

        # 多图消息容易在 QQ 侧失败：拆成单图再试一次
        images = [item for item in chain if isinstance(item, Comp.Image)]
        if len(images) > 1:
            ok_any = False
            for image in images:
                ok_any = (
                    await self._send(
                        event,
                        target,
                        [image],
                        retries=0,
                        allow_text_fallback=False,
                    )
                    or ok_any
                )
            if ok_any:
                logger.info("哔咔多图消息已拆分为单图发送")
                return True

        # 降级：只发文字，至少让用户看到结果
        has_media = any(isinstance(item, (Comp.Image, Comp.File)) for item in chain)
        texts = [
            item.text
            for item in chain
            if isinstance(item, Comp.Plain) and getattr(item, "text", "").strip()
        ]
        if allow_text_fallback and has_media and texts:
            try:
                await self._throttle()
                await self._send_raw(event, target, [Comp.Plain("\n".join(texts))])
                logger.info("哔咔消息已降级为纯文本发送")
                return False
            except Exception as exc:
                logger.error("哔咔消息降级发送仍然失败：%s", exc)
        return False

    # ------------------------------------------------------------------
    # 封面
    # ------------------------------------------------------------------
    async def _fetch_cover(self, client: BikaClient, comic: dict) -> bytes | None:
        thumb = comic.get("thumb") or {}
        url = BikaClient.page_image_url({"media": thumb})
        if not url:
            return None
        try:
            return await client.download_image(self._map_image_url(url))
        except BikaError as exc:
            logger.debug("封面下载失败：%s", exc)
            return None

    # ==================================================================
    # 指令入口
    # ==================================================================
    @filter.command("bika", alias={"哔咔", "bkr"})
    async def bika_command(self, event: AstrMessageEvent):
        """哔咔漫画主命令。"""
        args = self._split_args(event)
        if not args:
            yield event.plain_result(HELP_TEXT)
            return

        permission_error = self._permission_error(event)
        if permission_error:
            yield event.plain_result(permission_error)
            return

        sub = args[0].lower()
        rest = args[1:]

        if sub in {"帮助", "help", "?", "菜单"}:
            yield event.plain_result(HELP_TEXT)
            return

        client = self._ensure_client()

        try:
            if sub in {"排行", "rank", "top"}:
                await self._cmd_rank(event, client, rest)
            elif sub in {"分区", "category", "categories", "分区浏览"}:
                await self._cmd_categories(event, client, rest)
            elif sub in {"搜索", "search"}:
                await self._cmd_search(event, client, rest)
            elif sub in {"详情", "info", "漫画"}:
                await self._cmd_detail(event, client, rest)
            elif sub in {"章节", "eps", "目录"}:
                await self._cmd_episodes(event, client, rest)
            elif sub in {"下载", "dl", "download"}:
                await self._cmd_download(event, client, rest)
            elif sub in {"状态", "status"}:
                yield event.plain_result(await self._status_text(client, event))
            elif sub in {"收藏", "fav", "favourite", "favorite"}:
                await self._cmd_favourite(event, client, rest)
            elif sub in {"我的收藏", "收藏夹", "favorites", "favlist"}:
                await self._cmd_favourites(event, client, rest)
            elif sub in {"签到", "punch", "signin"}:
                await self._cmd_punch(event, client)
            elif sub in {"推送测试", "pushtest"}:
                await self._cmd_push_test(event, client, rest)
            elif sub in {"登录", "login"}:
                await client.login()
                self._save_session()
                payload = client.token_payload()
                yield event.plain_result(f"✅ 登录成功：{payload.get('name') or client.account}")
            else:
                yield event.plain_result(f"❓ 未知子命令：{sub}\n\n{HELP_TEXT}")
        except BikaAuthError as exc:
            hint = f"\n{exc.hint}" if exc.hint else ""
            yield event.plain_result(f"🔒 {exc.message}{hint}")
        except BikaNetworkError as exc:
            hint = f"\n{exc.hint}" if exc.hint else ""
            yield event.plain_result(f"🌐 {exc.message}{hint}")
        except BikaError as exc:
            hint = f"\n{exc.hint}" if exc.hint else ""
            yield event.plain_result(f"❌ {exc.message}{hint}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - 兜底
            logger.exception("哔咔插件执行出错")
            detail = str(exc)
            if "retcode" in detail or "ActionFailed" in type(exc).__name__:
                yield event.plain_result(
                    "⚠️ 消息发送失败：QQ 没有确认收件（常见于频率限制或图片被拦截）。\n"
                    "插件已自动重试并降级；可稍后重发，或改用 "
                    "`/bika 下载 <ID> <章节> zip` 避开图片风控。"
                )
            else:
                yield event.plain_result(f"💥 执行失败：{detail}")
        finally:
            self._save_session()

    # ==================================================================
    # 排行榜
    # ==================================================================
    async def _cmd_rank(self, event: AstrMessageEvent, client: BikaClient, args: list[str]) -> None:
        tt = "H24"
        size = int(self._cfg("rank_size", 10))
        for arg in args:
            key = arg.lower()
            if key in RANK_TYPES:
                tt = RANK_TYPES[key]
            elif arg.isdigit() and key not in RANK_TYPES:
                size = max(1, min(int(arg), 30))

        comics = await client.leaderboard(tt)
        if not comics:
            await self._send(event, "", [Comp.Plain("📊 排行榜暂无数据。")])
            return

        comics = comics[:size]
        self._remember(event, "rank", comics)
        text = format_rank(comics, tt)

        # 封面拼图单独发一条：图片被拦/失败也不影响文字榜单送达
        if bool(self._cfg("rank_collage", True)) and pillow_available():
            top = comics[: min(len(comics), 10)]
            covers = [
                data
                for data in await asyncio.gather(
                    *(self._fetch_cover(client, comic) for comic in top)
                )
                if data
            ]
            if covers:
                out = self.pack_dir / f"rank_{tt}_{int(time.time())}.jpg"
                try:
                    await asyncio.to_thread(rank_collage, covers, out)
                    await self._send(
                        event,
                        "",
                        [Comp.Image.fromFileSystem(str(out))],
                        retries=0,
                        allow_text_fallback=False,
                    )
                except Exception as exc:
                    logger.warning("生成排行榜封面拼图失败：%s", exc)

        await self._send(event, "", [Comp.Plain(text)])

    # ==================================================================
    # 分区浏览
    # ==================================================================
    async def _cmd_categories(
        self, event: AstrMessageEvent, client: BikaClient, args: list[str]
    ) -> None:
        categories = await client.categories()
        if not categories:
            await self._send(event, "", [Comp.Plain("📂 暂时没有拿到分区数据。")])
            return

        # 无参数：列出全部分区，并缓存供「序号」引用
        if not args:
            self._last_categories[event.unified_msg_origin] = (
                time.time(),
                categories,
            )
            await self._send(event, "", [Comp.Plain(format_categories(categories))])
            return

        category = self._resolve_category(event, categories, args[0])
        if category is None:
            return

        sort = "ua"
        page = 1
        sort_values = set(SORTS.values())
        for arg in args[1:]:
            key = arg.lower()
            if key in SORTS:
                sort = SORTS[key]
            elif key in sort_values:
                sort = key
            elif arg.isdigit():
                page = max(1, int(arg))

        result = await client.comics(category, page=page, sort=sort)
        self._remember(event, "category", result["docs"])
        sort_label = next((k for k, v in SORTS.items() if v == sort), sort)
        await self._send(
            event,
            "",
            [Comp.Plain(format_category_comics(result, category, sort_label))],
        )

    def _resolve_category(
        self,
        event: AstrMessageEvent,
        categories: list[dict],
        token: str,
    ) -> str | None:
        """把「序号」或「名称」解析成分区标题；失败时已回复错误信息。"""
        token = (token or "").strip()
        if not token:
            return None

        if token.isdigit():
            cached = self._last_categories.get(event.unified_msg_origin)
            if not cached or time.time() - cached[0] > 3600:
                raise BikaError(
                    "分区序号需要先查看列表",
                    hint="先发送 /bika 分区 获取分区编号，再发送 /bika 分区 <序号>",
                )
            docs = cached[1]
            index = int(token) - 1
            if not 0 <= index < len(docs):
                raise BikaError(f"分区序号超出范围（共 {len(docs)} 个）")
            return str(docs[index].get("title") or "")

        lowered = token.lower()
        exact = [c for c in categories if str(c.get("title") or "").lower() == lowered]
        if exact:
            return str(exact[0].get("title") or "")
        fuzzy = [c for c in categories if lowered in str(c.get("title") or "").lower()]
        if fuzzy:
            return str(fuzzy[0].get("title") or "")
        raise BikaError(
            f"没有找到分区「{token}」",
            hint="发送 /bika 分区 查看全部分区名称",
        )

    # ==================================================================
    # 搜索
    # ==================================================================
    async def _cmd_search(
        self, event: AstrMessageEvent, client: BikaClient, args: list[str]
    ) -> None:
        if not args:
            await self._send(event, "", [Comp.Plain("🔍 用法：/bika 搜索 <关键词> [页码]")])
            return
        words = list(args)
        page = 1
        if len(words) > 1 and words[-1].isdigit():
            page = max(1, int(words.pop()))
        keyword = " ".join(words).strip()
        if not keyword:
            await self._send(event, "", [Comp.Plain("🔍 用法：/bika 搜索 <关键词> [页码]")])
            return

        result = await client.search(keyword, page=page)
        docs = result["docs"][:]
        self._remember(event, "search", docs)
        text = format_search(result, keyword)

        cover = await self._fetch_cover(client, docs[0]) if docs else None
        if cover:
            path = self.pack_dir / f"search_{int(time.time())}.jpg"
            path.write_bytes(cover)
            await self._send(
                event,
                "",
                [Comp.Image.fromFileSystem(str(path))],
                retries=0,
                allow_text_fallback=False,
            )
        await self._send(event, "", [Comp.Plain(text)])

    # ==================================================================
    # 详情
    # ==================================================================
    async def _cmd_detail(
        self, event: AstrMessageEvent, client: BikaClient, args: list[str]
    ) -> None:
        if not args:
            await self._send(event, "", [Comp.Plain("📖 用法：/bika 详情 <漫画ID|序号>")])
            return
        comic_id = self._resolve_comic_id(event, args[0])
        comic = await client.comic(comic_id)
        text = format_detail(comic)
        cover = await self._fetch_cover(client, comic)
        if cover:
            path = self.pack_dir / f"detail_{comic_id}.jpg"
            path.write_bytes(cover)
            await self._send(
                event,
                "",
                [Comp.Image.fromFileSystem(str(path))],
                retries=0,
                allow_text_fallback=False,
            )
        await self._send(event, "", [Comp.Plain(text)])

    # ==================================================================
    # 章节列表
    # ==================================================================
    async def _cmd_episodes(
        self, event: AstrMessageEvent, client: BikaClient, args: list[str]
    ) -> None:
        if not args:
            await self._send(event, "", [Comp.Plain("📚 用法：/bika 章节 <漫画ID|序号> [页码]")])
            return
        comic_id = self._resolve_comic_id(event, args[0])
        page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        comic = await client.comic(comic_id)
        eps = await client.episodes(comic_id, page)
        docs = sorted(eps["docs"], key=lambda x: int(x.get("order") or 0))
        text = format_episodes(comic, docs, eps["page"], eps["pages"])
        await self._send(event, "", [Comp.Plain(text)])

    # ==================================================================
    # 收藏 / 签到
    # ==================================================================
    async def _cmd_favourite(
        self, event: AstrMessageEvent, client: BikaClient, args: list[str]
    ) -> None:
        if not args:
            await self._send(event, "", [Comp.Plain("💗 用法：/bika 收藏 <漫画ID|本页序号>")])
            return
        comic_id = self._resolve_comic_id(event, args[0])
        action = await client.toggle_favourite(comic_id)
        try:
            comic = await client.comic(comic_id)
            title = comic_title(comic)
        except BikaError:
            title = comic_id
        if action == "favourite":
            text = f"💗 已收藏《{title}》\n🆔 {comic_id}"
        elif action == "un_favourite":
            text = f"💔 已取消收藏《{title}》\n🆔 {comic_id}"
        else:
            text = f"ℹ️ 已提交操作（{action or '未知'}）\n🆔 {comic_id}"
        await self._send(event, "", [Comp.Plain(text)])

    async def _cmd_favourites(
        self, event: AstrMessageEvent, client: BikaClient, args: list[str]
    ) -> None:
        page = int(args[0]) if args and args[0].isdigit() else 1
        result = await client.favourites(page)
        docs = result["docs"]
        self._remember(event, "favourite", docs)
        if docs:
            cover = await self._fetch_cover(client, docs[0])
            if cover:
                path = self.pack_dir / f"fav_{int(time.time())}.jpg"
                path.write_bytes(cover)
                await self._send(
                    event,
                    "",
                    [Comp.Image.fromFileSystem(str(path))],
                    retries=0,
                    allow_text_fallback=False,
                )
        await self._send(event, "", [Comp.Plain(format_favourites(result))])

    async def _cmd_punch(self, event: AstrMessageEvent, client: BikaClient) -> None:
        res = await client.punch_in()
        status = str(res.get("status") or "").lower()
        if status == "ok":
            text = "✅ 签到成功！"
            extra = {
                k: v for k, v in res.items() if k != "status" and isinstance(v, (int, float, str))
            }
            if extra:
                text += "\n" + "，".join(f"{k}: {v}" for k, v in extra.items())
        elif status == "fail":
            text = "ℹ️ 今天已经签到过了～"
        else:
            text = f"ℹ️ 签到返回：{res or '空响应'}"
        await self._send(event, "", [Comp.Plain(text)])

    async def _cmd_push_test(
        self, event: AstrMessageEvent, client: BikaClient, args: list[str]
    ) -> None:
        targets = self._push_targets()
        if not targets:
            await self._send(
                event,
                "",
                [
                    Comp.Plain(
                        "❌ 还没有配置推送目标。\n"
                        "请在插件配置的 push_targets 里添加，例如：\n"
                        "二群=<BOT名>:GroupMessage:123456789\n"
                        "（发送 /bika 状态 可以查看当前会话的 umo，直接复制即可）"
                    )
                ],
            )
            return
        tt = "H24"
        for arg in args:
            key = arg.lower()
            if key in RANK_TYPES:
                tt = RANK_TYPES[key]
        label = f"手动推送 · {RANK_LABELS.get(tt, tt)}"
        ok = await self._push_rank(tt, label)
        await self._send(
            event,
            "",
            [Comp.Plain(f"📮 推送完成：成功 {ok}/{len(targets)} 个目标")],
        )

    # ==================================================================
    # 下载
    # ==================================================================
    async def _cmd_download(
        self, event: AstrMessageEvent, client: BikaClient, args: list[str]
    ) -> None:
        if not args:
            await self._send(
                event,
                "",
                [Comp.Plain("⬇️ 用法：/bika 下载 <漫画ID|序号> [章节] [格式] [@目标]")],
            )
            return

        comic_id = self._resolve_comic_id(event, args[0])
        rest = args[1:]
        fmt = str(self._cfg("pack_format", "图片"))
        target_alias = ""
        chapter_spec = ""

        for arg in rest:
            low = arg.lower()
            if low.startswith("@"):
                target_alias = arg[1:]
            elif low in {f.lower() for f in PACK_FORMATS} or low in {"image", "images"}:
                fmt = {
                    "image": "图片",
                    "images": "图片",
                    "图片": "图片",
                    "zip": "zip",
                    "pdf": "pdf",
                    "长图": "长图",
                }.get(low, fmt)
            else:
                chapter_spec = arg

        if fmt not in PACK_FORMATS:
            fmt = "图片"
        if fmt in {"pdf", "长图"} and not pillow_available():
            await self._send(
                event,
                "",
                [Comp.Plain("❌ 服务器未安装 Pillow，暂时无法生成该格式。")],
            )
            return

        target = ""
        if target_alias:
            target, error = self._resolve_push_target(event, target_alias)
            if error:
                await self._send(event, "", [Comp.Plain(error)])
                return

        comic = await client.comic(comic_id)
        episodes = await client.all_episodes(comic_id)
        if not episodes:
            await self._send(event, "", [Comp.Plain("❌ 该漫画没有可下载的章节。")])
            return

        selected = self._parse_chapter_spec(chapter_spec, episodes)
        if not selected:
            await self._send(
                event,
                "",
                [Comp.Plain("❌ 没有匹配的章节。可用：1 / 1-3 / 1,3,5 / 最新3 / all")],
            )
            return

        max_chapters = max(1, int(self._cfg("max_chapters_per_task", 3)))
        if len(selected) > max_chapters:
            selected = selected[:max_chapters]
            logger.info("章节数超过上限，已截断为 %s 话", max_chapters)
            await self._send(
                event,
                "",
                [Comp.Plain(f"⚠️ 单次最多下载 {max_chapters} 话，已自动截取前 {max_chapters} 话。")],
            )

        max_images = max(1, int(self._cfg("max_images_per_task", 200)))
        title = comic_title(comic)
        target_note = f"（推送到 {target_alias}）" if target else ""
        start_text = (
            f"⬇️ 开始下载《{title}》{target_note}\n"
            f"章节：{'、'.join(str(e.get('order')) for e in selected)}\n"
            f"格式：{fmt}　线路：{client.api_base}"
        )
        if fmt == "图片":
            estimated = self._estimate_images(comic, selected, max_images)
            batch = max(1, min(int(self._cfg("send_batch", 3)), 10))
            if estimated / batch > 20:
                start_text += (
                    f"\n\n💡 预计发送约 {max(1, estimated // batch)} 条图片消息，"
                    "若遇发送失败可改用 `zip` 格式。"
                )
        await self._send(event, "", [Comp.Plain(start_text)])

        results = await self._download_chapters(event, client, comic_id, selected, max_images)
        if not results:
            await self._send(event, "", [Comp.Plain("❌ 下载失败，没有拿到任何图片。")])
            return

        await self._deliver(event, client, comic, results, fmt, target)
        if target:
            await self._send(
                event,
                "",
                [Comp.Plain(f"✅ 已推送到「{target_alias}」。")],
            )

    @staticmethod
    def _estimate_images(comic: dict, selected: list[dict], limit: int) -> int:
        """粗略估算本次要发的图片数（用于提前提示消息条数）。"""
        try:
            pages = int(comic.get("pagesCount") or 0)
            eps = int(comic.get("epsCount") or 0)
        except (TypeError, ValueError):
            return 0
        if pages <= 0 or eps <= 0:
            return 0
        per_ep = max(1, pages // max(1, eps))
        return min(limit, per_ep * max(1, len(selected)))

    def _resolve_push_target(self, event: AstrMessageEvent, alias: str) -> tuple[str, str]:
        """解析推送目标，返回 ``(umo, 错误信息)``。

        支持三种写法：
        * ``@别名``：配置项 ``push_targets`` 里的别名；
        * ``@g群号``：直接推送到该群；
        * ``@uQQ号``：直接推送给该用户（私聊）。
        """
        alias = (alias or "").strip().lstrip("@")
        if not alias:
            return "", ""
        targets = self._push_targets()
        if alias in targets:
            return targets[alias], ""
        # 大小写不敏感地匹配别名
        for key, umo in targets.items():
            if key.lower() == alias.lower():
                return umo, ""
        match = re.fullmatch(r"([gG]|[uU])(\d{4,})", alias)
        if match:
            current = event.unified_msg_origin or ""
            platform_id = current.split(":", 1)[0] if ":" in current else ""
            if not platform_id:
                return "", "❌ 无法识别当前平台，暂时不能用 @g / @u 直接指定目标。"
            kind = "GroupMessage" if match.group(1).lower() == "g" else "FriendMessage"
            return f"{platform_id}:{kind}:{match.group(2)}", ""
        return (
            "",
            f"❌ 未找到推送目标「{alias}」。可用 @别名（在 push_targets 中配置）、"
            "@g群号 或 @uQQ号。",
        )

    def _parse_chapter_spec(self, spec: str, episodes: list[dict]) -> list[dict]:
        """解析章节参数，返回按 order 升序的章节列表。"""
        orders = sorted(int(e.get("order") or 0) for e in episodes)
        if not orders:
            return []
        spec = (spec or "").strip()
        if not spec:
            # 缺省只下最新一话，避免误发整本
            return episodes[-1:]
        if spec.lower() == "all" or spec in {"全部", "所有"}:
            return episodes

        if spec.startswith("最新"):
            tail = spec[2:].strip()
            count = int(tail) if tail.isdigit() else 1
            count = max(1, count)
            return episodes[-count:]

        wanted: set[int] = set()
        for part in re.split(r"[,，]", spec):
            part = part.strip()
            if not part:
                continue
            match = re.fullmatch(r"(\d+)\s*[-~]\s*(\d+)", part)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
                wanted.update(range(min(start, end), max(start, end) + 1))
            elif part.isdigit():
                wanted.add(int(part))
        return [e for e in episodes if int(e.get("order") or 0) in wanted]

    async def _download_chapters(
        self,
        event: AstrMessageEvent,
        client: BikaClient,
        comic_id: str,
        episodes: list[dict],
        max_images: int,
    ) -> list[ChapterResult]:
        """逐话下载，进度与完成提示都发到当前会话（内容稍后发到目标会话）。"""
        concurrency = max(1, min(int(self._cfg("concurrency", 6)), 16))
        downloader = ChapterDownloader(
            client, concurrency=concurrency, url_transform=self._map_image_url
        )
        results: list[ChapterResult] = []
        remaining = max_images
        comic_dir = self.download_dir / safe_filename(comic_id, "comic", 40)

        for episode in episodes:
            if remaining <= 0:
                await self._send(
                    event,
                    "",
                    [Comp.Plain(f"⚠️ 已达单次下载上限 {max_images} 张，剩余章节未下载。")],
                )
                break
            order = int(episode.get("order") or 0)
            ep_title = str(episode.get("title") or f"第{order}话")
            out_dir = comic_dir / f"{order:03d}_{safe_filename(ep_title, 'ep', 30)}"

            last_notice = 0
            total_pages = 1

            async def progress(done: int, total: int, failed: int, _title: str = ep_title) -> None:
                nonlocal last_notice, total_pages
                total_pages = total
                if not bool(self._cfg("progress_notify", True)):
                    return
                if total < int(self._cfg("progress_min_pages", 30)):
                    return
                percent = int(done * 100 / max(1, total))
                step = max(5, int(self._cfg("progress_step_pct", 25)))
                if percent >= last_notice + step or done == total:
                    last_notice = percent
                    await self._send(
                        event,
                        "",
                        [Comp.Plain(f"⏳ {_title} 下载中 {done}/{total}（{percent}%）")],
                    )

            result = await downloader.download(
                comic_id,
                order,
                out_dir,
                title=ep_title,
                progress=progress,
                limit=remaining,
            )
            if result.images:
                remaining -= len(result.images)
                results.append(result)
                await self._send(
                    event,
                    "",
                    [
                        Comp.Plain(
                            f"✅ {ep_title} 完成：{len(result.images)} 张 / {result.size_text}"
                        )
                    ],
                )
            else:
                await self._send(
                    event,
                    "",
                    [Comp.Plain(f"❌ {ep_title} 下载失败（{total_pages} 页均未成功）")],
                )
        return results

    # ==================================================================
    # 发送
    # ==================================================================
    async def _deliver(
        self,
        event: AstrMessageEvent,
        client: BikaClient,
        comic: dict,
        results: list[ChapterResult],
        fmt: str,
        target: str,
    ) -> None:
        comic_id = str(comic.get("_id") or "")
        title = comic_title(comic)
        max_send_mb = int(self._cfg("max_send_mb", 50))
        stats = {"sent": 0, "failed": 0, "streak": 0, "aborted": False}
        total_images = sum(len(r.images) for r in results)

        async def send(chain: list, retries: int = 1, fallback: bool = True) -> bool:
            """发送并累计成功/失败次数，连续失败时标记中止。"""
            ok = await self._send(
                event, target, chain, retries=retries, allow_text_fallback=fallback
            )
            if ok:
                stats["sent"] += 1
                stats["streak"] = 0
            else:
                stats["failed"] += 1
                stats["streak"] += 1
                if stats["streak"] >= 2:
                    stats["aborted"] = True
            return ok

        try:
            if fmt == "zip":
                for result in results:
                    out = self.pack_dir / f"{safe_filename(title)}_{result.order:03d}.zip"
                    await asyncio.to_thread(
                        zip_images,
                        result.images,
                        out,
                        f"{safe_filename(title)}_{result.order:03d}/",
                    )
                    size = out.stat().st_size
                    if size > max_send_mb * 1024 * 1024:
                        await self._send(
                            event,
                            target,
                            [
                                Comp.Plain(
                                    f"⚠️ {out.name} 体积 {human_size(size)}，"
                                    f"超过 {max_send_mb}MB 限制，已跳过发送。"
                                )
                            ],
                        )
                        continue
                    await send(
                        [
                            Comp.Plain(f"📦 {title} · 第 {result.order} 话"),
                            Comp.File(name=out.name, file=str(out)),
                        ]
                    )
                    if stats["aborted"]:
                        break

            elif fmt == "pdf":
                for result in results:
                    stem = f"{safe_filename(title)}_{result.order:03d}"
                    outs = await asyncio.to_thread(
                        pdf_from_images_parts,
                        result.images,
                        self.pack_dir,
                        stem,
                    )
                    for part_index, out in enumerate(outs, start=1):
                        size = out.stat().st_size
                        if size > max_send_mb * 1024 * 1024:
                            await self._send(
                                event,
                                target,
                                [
                                    Comp.Plain(
                                        f"⚠️ {out.name} 体积 {human_size(size)}，"
                                        f"超过 {max_send_mb}MB 限制，已跳过发送。"
                                    )
                                ],
                            )
                            continue
                        part = f"（{part_index}/{len(outs)}）" if len(outs) > 1 else ""
                        await send(
                            [
                                Comp.Plain(f"📄 {title} · 第 {result.order} 话{part}"),
                                Comp.File(name=out.name, file=str(out)),
                            ]
                        )
                        if stats["aborted"]:
                            break
                    if stats["aborted"]:
                        break

            elif fmt == "长图":
                for result in results:
                    stem = f"{safe_filename(title)}_{result.order:03d}"
                    outs = await asyncio.to_thread(
                        long_image_from_images,
                        result.images,
                        self.pack_dir,
                        stem,
                    )
                    for out in outs:
                        size = out.stat().st_size
                        if size > max_send_mb * 1024 * 1024:
                            out = prepare_for_send(out, max_send_mb * 1024 * 1024, 1200)
                        await send(
                            [
                                Comp.Plain(f"🖼️ {title} · 第 {result.order} 话"),
                                Comp.Image.fromFileSystem(str(out)),
                            ]
                        )
                        if stats["aborted"]:
                            break
                    if stats["aborted"]:
                        break

            else:  # 逐张图片
                batch = max(1, min(int(self._cfg("send_batch", 5)), 10))
                compress = bool(self._cfg("compress_send", True))
                max_bytes = int(self._cfg("max_image_mb", 5)) * 1024 * 1024
                for result in results:
                    await send(
                        [
                            Comp.Plain(
                                f"📖 {title} · 第 {result.order} 话（{len(result.images)} 张）"
                            )
                        ]
                    )
                    for index in range(0, len(result.images), batch):
                        if stats["aborted"]:
                            break
                        chunk = result.images[index : index + batch]
                        chain: list = []
                        for path in chunk:
                            send_path = (
                                await asyncio.to_thread(prepare_for_send, path, max_bytes)
                                if compress
                                else path
                            )
                            chain.append(Comp.Image.fromFileSystem(str(send_path)))
                        await send(chain)
                    if stats["aborted"]:
                        break
        except Exception as exc:
            logger.exception("哔咔打包/发送失败")
            await self._send(
                event,
                "",
                [Comp.Plain(f"❌ 打包或发送失败：{exc}")],
                retries=0,
            )

        if stats["aborted"]:
            await self._send(
                event,
                "",
                [
                    Comp.Plain(
                        "⚠️ 连续发送失败，已停止剩余消息（通常是 QQ 频率限制或图片被拦截）。\n"
                        "图片已缓存在服务器，可改用 `/bika 下载 <ID> <章节> zip` "
                        "或稍后重发。"
                    )
                ],
                retries=0,
                allow_text_fallback=False,
            )

        summary = f"🎉 《{title}》发送完成，共 {len(results)} 话 / {total_images} 张\n🆔 {comic_id}"
        if stats["failed"]:
            summary = (
                f"⚠️ 《{title}》部分消息发送失败（成功 {stats['sent']} 条 / "
                f"失败 {stats['failed']} 条）\n"
                f"🆔 {comic_id}\n"
                "可改用 zip 格式或稍后重试。"
            )
        await self._send(event, target, [Comp.Plain(summary)], retries=0)

    # ==================================================================
    # 定时推送（日榜 / 周榜 / 月榜）
    # ==================================================================
    async def _schedule_loop(self) -> None:
        """后台循环：每分钟检查一次是否有到点的排行榜推送。"""
        await asyncio.sleep(20)
        while True:
            try:
                await self._check_schedules()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("哔咔定时推送检查出错")
            await asyncio.sleep(60)

    @staticmethod
    def _parse_hhmm(text, default: tuple[int, int] = (9, 0)) -> tuple[int, int]:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(text or "").strip())
        if not match:
            return default
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return default
        return hour, minute

    async def _check_schedules(self) -> None:
        """按配置检查日/周/月榜是否到点，到点则推送并记录状态。"""
        if not self._push_targets():
            return
        now = datetime.now()
        state = self._load_push_state()
        week = now.isocalendar()
        jobs = [
            (
                "daily",
                "push_daily_enable",
                "push_daily_time",
                "H24",
                now.strftime("%Y-%m-%d"),
                None,
            ),
            (
                "weekly",
                "push_weekly_enable",
                "push_weekly_time",
                "D7",
                f"{week.year}-W{week.week:02d}",
                ("weekday", int(self._cfg("push_weekly_day", 1))),
            ),
            (
                "monthly",
                "push_monthly_enable",
                "push_monthly_time",
                "D30",
                now.strftime("%Y-%m"),
                ("day", int(self._cfg("push_monthly_day", 1))),
            ),
        ]
        for kind, enable_key, time_key, tt, period_key, day_filter in jobs:
            if not bool(self._cfg(enable_key, True)):
                continue
            if state.get(kind) == period_key:
                continue
            if day_filter is not None:
                mode, value = day_filter
                if mode == "weekday" and now.isoweekday() != value:
                    continue
                if mode == "day" and now.day != value:
                    continue
            hour, minute = self._parse_hhmm(self._cfg(time_key, "09:00"))
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < due:
                continue
            if now - due > timedelta(hours=int(self._cfg("push_max_delay_hours", 6))):
                continue
            try:
                sent = await self._push_rank(tt, f"{kind} 定时推送")
            except BikaError as exc:
                logger.warning("哔咔定时推送失败：%s", exc.message)
                continue
            logger.info("哔咔%s定时推送完成，成功 %s 个目标", kind, sent)
            state[kind] = period_key
            self._save_push_state(state)

    async def _push_rank(self, tt: str, label: str) -> int:
        """把某个榜单推送到所有 push_targets，返回成功的目标数。"""
        client = self._ensure_client()
        size = max(1, min(int(self._cfg("rank_push_size", 10)), 30))
        comics = (await client.leaderboard(tt))[:size]
        if not comics:
            return 0
        text = f"📅 哔咔{label}\n" + format_rank(comics, tt, with_tip=False)

        image_path: Path | None = None
        if bool(self._cfg("rank_collage", True)) and pillow_available():
            covers = [
                data
                for data in await asyncio.gather(
                    *(self._fetch_cover(client, comic) for comic in comics[:10])
                )
                if data
            ]
            if covers:
                out = self.pack_dir / f"rank_push_{tt}_{int(time.time())}.jpg"
                try:
                    await asyncio.to_thread(rank_collage, covers, out)
                    image_path = out
                except Exception as exc:
                    logger.warning("生成推送封面拼图失败：%s", exc)

        sent = 0
        for umo in self._push_targets().values():
            if image_path is not None:
                await self._push_to(umo, [Comp.Image.fromFileSystem(str(image_path))])
            if await self._push_to(umo, [Comp.Plain(text)]):
                sent += 1
        return sent

    async def _push_to(self, umo: str, chain: list) -> bool:
        """主动发送到指定会话（不带 event）。"""
        await self._throttle()
        for attempt in range(2):
            try:
                await self.context.send_message(umo, MessageChain(chain=chain))
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("哔咔主动推送失败（第 %s/2 次，%s）：%s", attempt + 1, umo, exc)
                if attempt == 0:
                    await asyncio.sleep(2)
        return False

    def _load_push_state(self) -> dict:
        try:
            if self._push_state_file.exists():
                data = json.loads(self._push_state_file.read_text("utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("读取哔咔推送状态失败：%s", exc)
        return {}

    def _save_push_state(self, state: dict) -> None:
        try:
            self._push_state_file.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
        except Exception as exc:
            logger.warning("保存哔咔推送状态失败：%s", exc)

    # ==================================================================
    # 状态
    # ==================================================================
    async def _status_text(self, client: BikaClient, event: AstrMessageEvent) -> str:
        payload = client.token_payload() if client.token else {}
        lines = ["🩺 哔咔插件状态"]
        if client.token:
            expire = client.token_expire_at
            left = max(0, expire - int(time.time())) // 3600 if expire else -1
            lines.append(f"👤 账号：{payload.get('name') or client.account}")
            lines.append("🔑 登录：正常" + (f"（约 {left} 小时后过期）" if left >= 0 else ""))
        else:
            lines.append("🔑 登录：未登录（发送任意查询指令会自动登录）")
        lines.append(f"🌐 线路：{client.api_base}")
        lines.append(f"🖼️ 图片：{self._cfg('image_quality', 'medium')}")
        lines.append(f"📦 默认格式：{self._cfg('pack_format', '图片')}")
        total_size, count = self._cache_stats()
        lines.append(f"💾 缓存：{count} 个文件 / {human_size(total_size)}")
        lines.append(f"🧩 会话：{event.unified_msg_origin}")
        lines.append("⏰ 定时推送：" + self._schedule_summary())
        targets = self._push_targets()
        if targets:
            lines.append("📮 推送目标：" + "、".join(targets.keys()))
        else:
            lines.append("📮 推送目标：未配置（push_targets）")
        return "\n".join(lines)

    def _schedule_summary(self) -> str:
        parts = []
        if bool(self._cfg("push_daily_enable", True)):
            hour, minute = self._parse_hhmm(self._cfg("push_daily_time", "09:00"))
            parts.append(f"日榜 {hour:02d}:{minute:02d}")
        if bool(self._cfg("push_weekly_enable", True)):
            hour, minute = self._parse_hhmm(self._cfg("push_weekly_time", "09:00"))
            day = int(self._cfg("push_weekly_day", 1))
            parts.append(
                f"周榜 周{'一二三四五六日'[max(1, min(day, 7)) - 1]} {hour:02d}:{minute:02d}"
            )
        if bool(self._cfg("push_monthly_enable", True)):
            hour, minute = self._parse_hhmm(self._cfg("push_monthly_time", "09:00"))
            parts.append(f"月榜 {int(self._cfg('push_monthly_day', 1))} 日 {hour:02d}:{minute:02d}")
        return "；".join(parts) if parts else "未开启"

    # ==================================================================
    # 缓存
    # ==================================================================
    def _cache_stats(self) -> tuple[int, int]:
        total = 0
        count = 0
        for root in (self.download_dir, self.pack_dir):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    count += 1
                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue
        return total, count

    def _cleanup_cache(self) -> None:
        """删除超过 cache_days 天的下载与打包文件。"""
        days = int(self._cfg("cache_days", 7))
        if days <= 0:
            return
        deadline = time.time() - days * 86400
        removed = 0
        for root in (self.download_dir, self.pack_dir):
            if not root.exists():
                continue
            for path in sorted(root.rglob("*"), reverse=True):
                try:
                    if path.is_file() and path.stat().st_mtime < deadline:
                        path.unlink()
                        removed += 1
                    elif path.is_dir() and not any(path.iterdir()):
                        path.rmdir()
                except OSError:
                    continue
        if removed:
            logger.info("哔咔插件清理了 %s 个过期缓存文件", removed)
