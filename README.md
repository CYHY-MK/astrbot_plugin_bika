# 哔咔漫画 · AstrBot 插件

在 QQ / Telegram / Discord 等聊天平台里刷哔咔排行榜、搜本子、把整话漫画直接下到群里或私聊，
还支持收藏、每日签到和日/周/月榜定时推送。

> 接口基于哔咔网页版 <https://manhuabika.com/>，默认走网页版镜像线路，
> 不需要改 hosts、不需要官方 App，大陆网络可直连。

---

## ⚠️ AI 生成声明

本插件**完全由 AI 编写**（需求提出、代码生成、调试与测试均由 AI 完成，人类仅提需求与试用）。
代码、文档、Logo 均为 AI 生成产物，不含任何第三方闭源代码。

如果本插件的任何内容侵犯了您的权益，请通过邮箱联系删除：
**3033980089@qq.com**（QQ 邮箱）。

## 免责声明

本插件仅用于个人学习与技术研究，请勿用于商业用途。
漫画内容版权归原作者及平台所有，请支持正版。
使用者需自行遵守所在地区法律法规与平台服务条款。

---

## 功能

| 功能 | 说明 |
| --- | --- |
| 📊 排行榜 | 日榜 / 周榜 / 月榜，Top N 列表 + 封面拼图 |
| ⏰ 定时推送 | 每天 / 每周 / 每月自动把对应榜单推到指定群或私聊 |
| 🔍 搜索 | 关键词搜索，支持翻页 |
| 📖 详情 | 作者、分类、标签、页数、章节数、播放 / 爱心数、简介 |
| 📚 章节 | 查看全部话数与更新时间 |
| ⬇️ 下载 | 按话下载，支持 `1` / `1-3` / `1,3,5` / `最新3` / `all` |
| 📤 发送 | 直接发到当前群聊或私聊，也可以推送到其它会话 |
| 📦 打包 | 逐张图片 / ZIP / PDF / 长图 四种方式（PDF 自动分卷） |
| 💗 收藏 | 一键收藏 / 取消收藏，查看收藏夹并翻页 |
| ✅ 签到 | 哔咔每日签到 |
| 🔐 登录 | 配置账号密码自动登录，token 本地缓存，过期自动续期 |

下载的图片会缓存到 `data/plugin_data/astrbot_plugin_bika/downloads/`，
超过保留天数后自动清理；同一话重复下载会直接复用缓存。

---

## 安装

1. 把整个 `astrbot_plugin_bika` 文件夹放进 AstrBot 的 `data/plugins/` 目录；
2. 在 WebUI 的「插件」页面点一次刷新（或重载插件）；
3. 打开插件配置，填写哔咔账号与密码；
4. 在聊天里发送 `/bika 帮助` 验证。

依赖：`aiohttp`（AstrBot 自带）、`Pillow`（PDF / 长图 / 封面拼图需要，AstrBot 环境一般已自带）。

---

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `account` | 空 | 哔咔账号（邮箱或用户名） |
| `password` | 空 | 哔咔密码（不会在聊天中回显） |
| `api_base` | `https://picaapi.go2778.com` | 接口线路，连不上自动切备用线路 |
| `proxy` | 空 | 代理，如 `http://127.0.0.1:7897` |
| `image_host` | 镜像 | 镜像会把 `storage-*.picacomic.com` 换成当前线路域名 |
| `image_quality` | `medium` | `medium` / `original` / `low` |
| `pack_format` | 图片 | 默认发送格式：图片 / zip / pdf / 长图 |
| `send_batch` | 3 | 一条消息发几张图（QQ 风控严时用 1~3） |
| `send_interval` | 1.2 | 相邻消息最小间隔（秒），报「未返回消息回执」时调到 2~3 |
| `max_image_mb` | 3 | 单张图片上限，超过会压缩 |
| `max_send_mb` | 50 | ZIP / PDF 单文件上限 |
| `max_images_per_task` | 200 | 单次指令最多下载图片数 |
| `max_chapters_per_task` | 3 | 单次指令最多下载话数 |
| `concurrency` | 6 | 图片下载并发 |
| `rank_size` | 10 | 排行榜显示条数 |
| `rank_collage` | 开 | 排行榜封面拼图 |
| `rank_push_size` | 10 | 定时推送展示条数 |
| `push_daily_enable` / `push_daily_time` | 开 / `09:00` | 日榜每天推送 |
| `push_weekly_enable` / `push_weekly_time` / `push_weekly_day` | 开 / `09:00` / 1 | 周榜推送（1=周一） |
| `push_monthly_enable` / `push_monthly_time` / `push_monthly_day` | 开 / `09:00` / 1 | 月榜推送（每月几号） |
| `push_max_delay_hours` | 6 | 到点时机器人没运行，窗口内启动会补发 |
| `push_targets` | `[]` | 推送目标，格式 `别名=unified_msg_origin` |
| `admin_only` | 关 | 仅管理员可用（AstrBot 管理员或 `admin_ids`） |
| `admin_ids` | 空 | 手动指定管理员 ID，逗号分隔 |
| `allow_group` / `allow_private` | 开 | 是否允许在群聊 / 私聊使用 |
| `cache_days` | 7 | 缓存保留天数，0 表示不清理 |

`push_targets` 里的 `unified_msg_origin`（umo）可以直接用 `/bika 状态` 查到，
例如 `二群=<BOT名>:GroupMessage:123456789`（前缀因部署而异，以状态里显示的为准）。
定时推送会发到列表里的**所有**目标。

---

## 指令

```
/bika 排行 [日|周|月] [数量]
/bika 搜索 <关键词> [页码]
/bika 详情 <漫画ID|本页序号>
/bika 章节 <漫画ID|本页序号> [页码]
/bika 下载 <漫画ID|本页序号> [章节] [格式] [@目标]
/bika 收藏 <漫画ID|本页序号>
/bika 我的收藏 [页码]
/bika 签到
/bika 状态
/bika 登录
/bika 推送测试 [日|周|月]
/bika 帮助
```

别名：`哔咔`、`bkr`（例如 `/哔咔 排行 周`）。

**「本页序号」** 指最近一次排行榜 / 搜索 / 收藏列表里的第几条，方便不记 ID：

```
/bika 排行 周        → 得到列表
/bika 详情 3         → 看第 3 名的详情
/bika 收藏 3         → 收藏第 3 名
/bika 下载 3 1-2 zip → 下第 3 名的第 1~2 话，打成 zip
```

**下载参数说明**

- 章节：`1`、`1-3`、`1,3,5`、`最新3`、`all`（受上限约束）；缺省为最新 1 话。
- 格式：`图片`（默认）、`zip`、`pdf`、`长图`。
- 目标：
  - `@二群`：推送到 `push_targets` 里别名为「二群」的会话；
  - `@g123456789`：直接推送到该群；
  - `@u10001`：直接推送给该用户（私聊）；
  - 缺省发到当前会话。

**定时推送怎么用**

1. 在目标群里发一次 `/bika 状态`，复制返回的「会话」那一行；
2. 填进配置 `push_targets`，例如 `二群=<BOT名>:GroupMessage:123456789`；
3. 按需设置 `push_daily_time` / `push_weekly_day` / `push_monthly_day`；
4. 发 `/bika 推送测试 周` 验证通不通。

推送状态记录在 `data/plugin_data/astrbot_plugin_bika/push_state.json`，
同一个周期只会推一次，重启不会重复刷屏。

---

## 技术说明

### 接口与签名

哔咔网页版调用的是同一套 Pica 接口的镜像线路（`picaapi.<镜像域名>`）。
接口签名算法（由网页版前端 JS 逆向得到）：

```text
signature = HMAC-SHA256(
    key = SECRET_KEY,
    msg = (path + time + nonce + method + API_KEY).lower()
)
```

- `path` 不含前导斜杠，但**包含查询串**（如 `comics/leaderboard?tt=H24&ct=VC`）；
- `time` 为秒级时间戳，`nonce` 为 32 位随机串；
- 请求头还需带 `api-key`、`app-channel`、`app-uuid`、`app-version`、`app-platform`、
  `accept: application/vnd.picacomic.com.v1+json`、`image-quality` 等。

### Cloudflare 与 nonce 格式（重要）

镜像站前面挂着 Cloudflare WAF，它会校验 `nonce` 请求头的格式：
必须是 32 位、取自网页版那套字符表（不含 `i/l/o/u/v` 的小写字母 + `2~8` 数字）。
沿用旧版 App 的固定 hex nonce 会直接吃到 `403 Just a moment...`。
本插件已按网页版规则随机生成 nonce，无需额外处理。

### 图片线路

图片地址由 `fileServer + /static/ + path` 拼成。官方图片域名
`storage-b.picacomic.com`、`storage1.picacomic.com` 在大陆常被污染，
插件默认把域名替换成当前线路的镜像域名（如 `storage-b.go2778.com`）。

### 为什么不用官方 API

`picaapi.picacomic.com` 在大陆网络下通常无法连接（DNS 污染 / 连接超时），
这也是很多旧插件失效的原因。本插件默认走网页版镜像线路，
并保留自动切换备用线路与代理配置。

---

## 常见问题

**Q：提示「未配置哔咔账号或密码」**
在插件配置里填 `account` / `password` 后重载插件。

**Q：提示 403 / Just a moment**
请求被 Cloudflare 拦了。升级到最新版本（nonce 规则会变）；
仍不行可在配置里填一个可用代理，或换 `api_base` 线路。

**Q：报错 `ActionFailed ... retcode=100 ... group message delivery was not confirmed`**
这是 QQ 端没有确认收件，常见原因：短时间发太多图片/消息触发频率限制、群被禁言、或图片被内容审核拦截。
插件已内置容错：发送间隔节流、失败自动重试、多图消息拆成单图、图片发不出去时退回纯文本、
连续失败自动停止并提示。想更稳可以：把 `send_interval` 调到 2~3 秒、`send_batch` 调到 1~2，
或改用 `zip` / `pdf` 以文件形式发送。

**Q：签到提示「今天已经签到过了」**
正常现象，哔咔每天只能签到一次。

**Q：定时推送没发**
检查 `push_targets` 是否配置、目标 umo 是否正确（`/bika 状态` 可查）、
以及 `push_*_enable` 是否开启；可以用 `/bika 推送测试` 先验证通道。

**Q：图片下载总是超时**
检查 `image_host` 是否为「镜像」；也可以配置代理后重试。

**Q：发送图片很慢 / 群里有频率限制**
调小 `send_batch`（如 1~3）和 `concurrency`（如 3~4），调大 `send_interval`。

---

## 参考与致谢

- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [huashuiyue07/astrbot_plugin_pica](https://github.com/huashuiyue07/astrbot_plugin_pica)：功能设计上的参考，本插件为独立实现
- [PicaWeb](https://manhuabika.com/)：网页版前端，签名与接口细节由其 JS 逆向得到
- 旧版 `zhenxun_plugin_pica`：Pica 接口签名的早期公开实现

## License

MIT
