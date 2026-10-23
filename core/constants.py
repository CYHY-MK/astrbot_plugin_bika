"""哔咔漫画（PicaWeb / manhuabika.com）接口常量。

说明
----
哔咔官方 App 的接口域名 ``picaapi.picacomic.com`` 在大陆网络下经常无法直连，
哔咔网页版 https://manhuabika.com/ 使用的是同一套接口的镜像线路，
本插件默认走网页版线路（``picaapi.go2778.com``），并保留备用线路。

接口签名算法（由网页版前端逆向得出）::

    signature = HMAC-SHA256(
        key = SECRET_KEY,
        msg = (path + time + nonce + method + API_KEY).lower()
    )

其中 ``path`` 是不含前导斜杠、但**包含查询串**的路径，例如
``comics/leaderboard?tt=H24&ct=VC``。

另有一个容易踩坑的点：镜像站前面挂着 Cloudflare WAF，它会检查
``nonce`` 请求头的格式——必须是 32 位、取自网页版那套字符表
（不含 i/l/o/u/v 的小写字母 + 2~8 的数字），否则会直接返回
``403 Just a moment...``。所以这里不能沿用旧版 App 的固定 hex nonce。
"""

from __future__ import annotations

# ---- 签名与请求头（网页版常量）----
PICA_API_KEY = "C69BAF41DA5ABD1FFEDC6D2FEA56B"
PICA_SECRET = r"""~d}$Q7$eIni=V)9\RK/P.RM4;9[7|@/CA}b~OW!3?EV`:<>M7pddUBL5n|0/*Cn"""

# nonce 字符表：网页版前端随机数表，Cloudflare 会校验该格式
NONCE_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
NONCE_LENGTH = 32

APP_CHANNEL = "1"
APP_UUID = "webUUIDv2"
APP_VERSION = "20251017"
APP_PLATFORM = "android"
ACCEPT_JSON = "application/vnd.picacomic.com.v1+json"
WEB_REFERER = "https://manhuabika.com/"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# ---- 接口线路（网页版：主线路 go2778，备用 acbbb）----
DEFAULT_API_BASE = "https://picaapi.go2778.com"
API_BASES = (
    "https://picaapi.go2778.com",
    "https://picaapi.acbbb.com",
)

# ---- 排行榜 ----
# 接口参数 tt 取值：H24 日榜 / D7 周榜 / D30 月榜
RANK_TYPES: dict[str, str] = {
    "日": "H24",
    "日榜": "H24",
    "h24": "H24",
    "周": "D7",
    "周榜": "D7",
    "d7": "D7",
    "月": "D30",
    "月榜": "D30",
    "d30": "D30",
}
RANK_LABELS: dict[str, str] = {
    "H24": "日榜（24 小时）",
    "D7": "周榜（7 天）",
    "D30": "月榜（30 天）",
}
# 排行榜评分维度：VC = 观看数
RANK_CATEGORY = "VC"

# ---- 排序方式（搜索 / 分区列表）----
SORTS: dict[str, str] = {
    "默认": "ua",
    "新到旧": "dd",
    "旧到新": "da",
    "最多爱心": "ld",
    "最多指名": "vd",
}

# ---- 认证错误码（响应里的 error 字段是字符串）----
AUTH_ERROR_CODES = {"401", "1005", "1008"}

# ---- 图片质量 ----
IMAGE_QUALITIES = ("original", "medium", "low")

# ---- 下载 / 打包 ----
PACK_FORMATS = ("图片", "zip", "pdf", "长图")

# ---- 分页 ----
EPS_PAGE_LIMIT = 40
PAGES_PAGE_LIMIT = 40
