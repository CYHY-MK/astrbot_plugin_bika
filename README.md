# 哔咔漫画 · AstrBot 插件

哔咔漫画插件 - 排行榜/分区浏览/搜索/详情/章节/收藏/签到/整话下载，支持日/周/月榜定时推送，打包支持图片/zip/pdf/长图。

> 对接第三方漫画平台，内容由平台提供；请遵守所在地法律法规，勿在未成年人可接触的场景使用。
> 本项目完全由 AI 编写，如有侵权请联系 **3033980089@qq.com**（QQ 邮箱）。

## 功能

- 📊 **排行榜**：日榜 / 周榜 / 月榜，Top N + 封面拼图
- ⏰ **定时推送**：日/周/月榜按时推到指定会话
- 📂 **分区浏览**：列出全部分区，按分区 + 排序 + 页码浏览
- 🔍 **搜索**：关键词搜索，支持翻页
- 📖 **详情 / 📚 章节**：作者、分类、标签、页数、章节列表
- ⬇️ **下载**：按话下载（`1` / `1-3` / `最新3` / `all`），发送到群聊或私聊
- 📦 **打包**：逐张图片 / ZIP / PDF / 长图
- 🔒 **加密**：ZIP（AES-256）/ PDF（打开密码），密码随消息提示
- 💗 **收藏**：收藏 / 取消收藏、收藏夹翻页
- ✅ **签到**：每日签到

## 安装

1. 把 `astrbot_plugin_bika` 文件夹放进 AstrBot 的 `data/plugins/`；
2. 在 WebUI 插件页重载插件；
3. 在插件配置里填写哔咔账号与密码；
4. 发送 `/bika 帮助` 验证。

依赖：`aiohttp`（AstrBot 自带）、`Pillow`（PDF / 长图 / 封面拼图）、`pyzipper`（ZIP 加密）、`pypdf`（PDF 加密）。

## 配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `account` / `password` | 空 | 哔咔账号与密码 |
| `api_base` | `https://picaapi.go2778.com` | 接口线路，失败自动切备用 |
| `proxy` | 空 | 代理，如 `http://127.0.0.1:7897` |
| `image_host` | 镜像 | 图片域名换成镜像线路，大陆更稳 |
| `image_quality` | `medium` | `medium` / `original` / `low` |
| `pack_format` | 图片 | 图片 / zip / pdf / 长图 |
| `pack_password` | 空 | 非空时 ZIP 加密 / PDF 加打开密码 |
| `pack_password_random` / `pack_password_length` | 关 / 6 | 每个文件随机密码 |
| `pack_password_notify` | 开 | 在消息里提示密码 |
| `send_batch` / `send_interval` | 3 / 1.2 | 发送节流，降低 QQ 风控 |
| `push_targets` | `[]` | 定时推送目标，格式 `别名=unified_msg_origin` |
| `push_daily_*` / `push_weekly_*` / `push_monthly_*` | 开 / 09:00 | 日/周/月榜推送开关与时间 |
| `allow_group` | 关 | 默认仅私聊可用 |

完整配置项见插件配置面板。

## 指令

```
/bika 排行 [日|周|月] [数量]
/bika 分区 [序号|名称] [排序] [页码]
/bika 搜索 <关键词> [页码]
/bika 详情 <漫画ID|本页序号>
/bika 章节 <漫画ID|本页序号> [页码]
/bika 下载 <漫画ID|本页序号> [章节] [格式] [@目标]
/bika 收藏 <漫画ID|本页序号>
/bika 我的收藏 [页码]
/bika 签到
/bika 状态
/bika 推送测试 [日|周|月]
/bika 帮助
```

示例：

```
/bika 排行 周
/bika 分区 長篇 最多爱心 2
/bika 详情 3
/bika 下载 3 1-2 zip
/bika 下载 3 1 zip #abcd12     ← 临时密码
```

**加密说明**：`pack_password` 非空（或指令末尾加 `#密码`）时，
ZIP 使用 AES-256 加密（需要 7-Zip / WinRAR / Bandizip 等支持 AES 的解压工具打开），
PDF 加打开密码；密码会随文件消息一起提示。图片 / 长图无法加密。

**文件上传失败？** QQ 的 `highway upload transport failed / peer idle` 属于文件上传通道超时，
常见于内容风控或网络抖动。可以：

1. 改用 `zip` 并设置密码（加密后内容不可扫描，成功率更高）；
2. 调小 `max_send_mb`、把大文件拆成多话分批发送；
3. 重试一次；仍失败则检查 NapCat / QQ 版本与网络。

## 说明

- 接口签名：`HMAC-SHA256(SECRET, lower(path + time + nonce + method + API_KEY))`，`nonce` 为 32 位网页版字符集随机串（镜像站 Cloudflare 会校验该格式）。
- 图片地址由 `fileServer + /static/ + path` 拼接，默认替换为当前线路镜像域名。
- 下载缓存在 `data/plugin_data/astrbot_plugin_bika/`，按 `cache_days` 自动清理。

## License

MIT（详见 [LICENSE](LICENSE)）
