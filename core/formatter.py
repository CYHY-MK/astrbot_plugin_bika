"""文本格式化：把接口返回的 JSON 渲染成适合聊天窗口阅读的文本。"""

from __future__ import annotations

from .constants import RANK_LABELS
from .util import clamp_text, human_count


def comic_title(comic: dict) -> str:
    return str(comic.get("title") or "无标题")


def comic_author(comic: dict) -> str:
    author = comic.get("author")
    if isinstance(author, dict):
        return str(author.get("name") or "")
    return str(author or "")


def comic_likes(comic: dict) -> int:
    for key in ("totalLikes", "likesCount", "likes"):
        value = comic.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def comic_views(comic: dict) -> int:
    for key in ("totalViews", "viewsCount", "views"):
        value = comic.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _tags(comic: dict, limit: int = 6) -> str:
    tags = comic.get("tags") or []
    if not isinstance(tags, list):
        return ""
    return " / ".join(str(t).strip() for t in tags[:limit] if str(t).strip())


def _categories(comic: dict) -> str:
    cats = comic.get("categories") or []
    if not isinstance(cats, list):
        return ""
    return " / ".join(str(c) for c in cats if c)


def format_rank(comics: list[dict], tt: str, page: int = 1, with_tip: bool = True) -> str:
    """排行榜文本。"""
    label = RANK_LABELS.get(tt, tt)
    if not comics:
        return f"📊 哔咔{label}暂无数据，稍后再试试吧。"

    lines = [f"📊 哔咔{label} Top {len(comics)}"]
    offset = (max(1, page) - 1) * len(comics)
    for index, comic in enumerate(comics, start=1):
        rank = offset + index
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")
        title = clamp_text(comic_title(comic), 40)
        author = clamp_text(comic_author(comic), 24) or "未知作者"
        lines.append(f"{medal} {title}")
        lines.append(
            f"    👤 {author} ｜ 👁 {human_count(comic_views(comic))}"
            f" ｜ ❤ {human_count(comic_likes(comic))}"
        )
    if with_tip:
        lines.append("")
        lines.append("💡 回复「/bika 详情 序号」查看详情，或「/bika 下载 序号 章节」直接下载")
    return "\n".join(lines)


def format_search(result: dict, keyword: str) -> str:
    """搜索结果文本。"""
    docs = result.get("docs") or []
    total = result.get("total") or len(docs)
    page = result.get("page") or 1
    pages = result.get("pages") or 1
    if not docs:
        return f"🔍 没有找到与「{keyword}」相关的漫画。"

    lines = [f"🔍 「{keyword}」搜索结果：共 {total} 部，第 {page}/{pages} 页"]
    offset = (page - 1) * len(docs)
    for index, comic in enumerate(docs, start=1):
        lines.append(f"{offset + index}. {clamp_text(comic_title(comic), 44)}")
        author = clamp_text(comic_author(comic), 24) or "未知作者"
        lines.append(f"    👤 {author} ｜ 👁 {human_count(comic_views(comic))}")
    lines.append("")
    lines.append("💡 回复「/bika 详情 序号」查看详情")
    return "\n".join(lines)


def format_detail(comic: dict) -> str:
    """漫画详情文本。"""
    title = comic_title(comic)
    lines = [f"📖 {title}"]
    author = comic_author(comic)
    if author:
        lines.append(f"👤 作者：{clamp_text(author, 40)}")
    cats = _categories(comic)
    if cats:
        lines.append(f"📂 分类：{cats}")
    tags = _tags(comic)
    if tags:
        lines.append(f"🏷️ 标签：{tags}")
    lines.append(
        f"📄 页数：{comic.get('pagesCount') or '?'} ｜ "
        f"📚 章节：{comic.get('epsCount') or '?'} ｜ "
        f"👁 {human_count(comic_views(comic))} ｜ ❤ {human_count(comic_likes(comic))}"
    )
    desc = clamp_text(str(comic.get("description") or ""), 160)
    if desc:
        lines.append(f"📝 简介：{desc}")
    comic_id = comic.get("_id") or ""
    if comic_id:
        lines.append("")
        lines.append(f"🆔 {comic_id}")
        lines.append(f"⬇️ 下载：/bika 下载 {comic_id} 1（或 /bika 下载 {comic_id} all）")
    return "\n".join(lines)


def format_episodes(comic: dict, eps: list[dict], page: int = 1, pages: int = 1) -> str:
    """章节列表文本（按 order 升序）。"""
    title = comic_title(comic)
    if not eps:
        return f"📚 {title} 还没有章节数据。"
    lines = [f"📚 {title} · 共 {len(eps)} 话（第 {page}/{pages} 页）"]
    for ep in eps:
        order = ep.get("order")
        name = clamp_text(str(ep.get("title") or f"第{order}话"), 40)
        updated = str(ep.get("updated_at") or "")[:10]
        lines.append(f"{order}. {name}" + (f"（{updated}）" if updated else ""))
    comic_id = comic.get("_id") or ""
    lines.append("")
    lines.append(f"⬇️ /bika 下载 {comic_id} 1-3  或  /bika 下载 {comic_id} all")
    return "\n".join(lines)


def format_favourites(result: dict) -> str:
    """我的收藏列表文本。"""
    docs = result.get("docs") or []
    total = result.get("total") or len(docs)
    page = result.get("page") or 1
    pages = result.get("pages") or 1
    if not docs:
        return "📭 收藏夹是空的。\n用「/bika 收藏 <漫画ID|序号>」把漫画加进收藏夹。"
    lines = [f"💗 我的收藏：共 {total} 部（第 {page}/{pages} 页）"]
    offset = (page - 1) * len(docs)
    for index, comic in enumerate(docs, start=1):
        lines.append(f"{offset + index}. {clamp_text(comic_title(comic), 44)}")
        author = clamp_text(comic_author(comic), 24) or "未知作者"
        lines.append(f"    👤 {author} ｜ 👁 {human_count(comic_views(comic))}")
    lines.append("")
    lines.append("💡 回复「/bika 详情 序号」查看详情，「/bika 我的收藏 页码」翻页")
    return "\n".join(lines)


def format_categories(categories: list[dict]) -> str:
    """分区列表文本。"""
    if not categories:
        return "📂 暂时没有拿到分区数据。"
    lines = [f"📂 哔咔分区（共 {len(categories)} 个）"]
    for index, cat in enumerate(categories, start=1):
        title = str(cat.get("title") or cat.get("_id") or "").strip()
        if title:
            lines.append(f"{index}. {title}")
    lines.append("")
    lines.append("💡 /bika 分区 <序号|名称> [排序] [页码]")
    lines.append("   排序：默认 / 新到旧 / 旧到新 / 最多爱心 / 最多指名")
    lines.append("   例：/bika 分区 長篇 最多爱心")
    return "\n".join(lines)


def format_category_comics(result: dict, category: str, sort_label: str) -> str:
    """分区内的漫画列表文本。"""
    docs = result.get("docs") or []
    total = result.get("total") or len(docs)
    page = result.get("page") or 1
    pages = result.get("pages") or 1
    if not docs:
        return (
            f"📂 「{category}」暂无内容（可能是官方/活动分区，或该页没有数据）。\n"
            "可以试试 /bika 分区 换个分区。"
        )
    lines = [f"📂 {category} · {sort_label}（共 {total} 部，第 {page}/{pages} 页）"]
    offset = (page - 1) * len(docs)
    for index, comic in enumerate(docs, start=1):
        lines.append(f"{offset + index}. {clamp_text(comic_title(comic), 44)}")
        author = clamp_text(comic_author(comic), 24) or "未知作者"
        lines.append(f"    👤 {author} ｜ 👁 {human_count(comic_views(comic))}")
    lines.append("")
    lines.append(f"💡 /bika 详情 序号 查看详情；/bika 分区 {category} {sort_label} 2 翻页")
    return "\n".join(lines)
