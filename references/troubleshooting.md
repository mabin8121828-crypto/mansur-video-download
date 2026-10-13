# 故障处理与扩展

## 先确定失败步骤

读取任务报告的 `stage`：`environment` 为依赖问题；`metadata` 为链接解析或鉴权问题；`download` 为数据下载问题；`convert` 为格式处理问题；`probe`、`verify`、`decode` 为成品检查问题；`lock` 为同一任务或同名文件正在被使用。

运行 `python scripts/download_media.py doctor` 查看实际使用的可执行文件和版本。优先使用当前 Python 安装的 yt-dlp 模块，没有模块时才查找可执行文件。环境变量可覆盖工具位置：

- `MANSUR_YT_DLP_BIN`
- `MANSUR_FFMPEG_BIN`
- `MANSUR_FFPROBE_BIN`
- `MANSUR_NODE_BIN`

变量值必须是对应可执行文件的完整路径。安装 Python 依赖用 `python -m pip install -r requirements.txt`。FFmpeg 与 ffprobe 需可执行；YouTube 挑战问题还应检查 Node。版本过旧导致解析失败时，可更新当前 Python 的 yt-dlp，而不是只更新 PATH 中另一份程序。

## Cookie 与 YouTube 挑战

明确提供 `--cookies "COOKIE_FILE"` 时只使用该文件的副本；不要同时传浏览器来源。Cookie 需为 yt-dlp 支持的导出文件格式，且登录状态仍有效。

自动模式不读取浏览器。对 YouTube，可通过 `MANSUR_VIDEO_COOKIE_FILE` 配置导出文件，先尝试公开解析，必要时才使用其副本。

`--cookies-from-browser "chrome"` 等显式选项允许 yt-dlp 正常读取浏览器登录状态。Windows 的数据库占用或加密机制可能导致读取失败；改用用户自行导出的 Cookie 文件。不要把 Cookie 内容写进聊天、Issue、仓库或发布包。

发现 Node 时，脚本明确传递其路径并启用 yt-dlp 的 `ejs:github` 远程组件。这可能从 yt-dlp 的 GitHub 来源加载挑战处理组件；对应机制见 [yt-dlp EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。

## 成品冲突与完整性

同名成品存在时，脚本验证后复用，不再重新下载视频。格式异常、解码错误或明显时长差异会报告失败并保留旧文件。

若想保留旧文件并再次获取另一个版本，使用新的输出目录或成品名称。不要根据一个返回码就声称成品完整；需要 `full_decode_verified: true`。

`prepare` 不删除输入源文件。下载模式的源文件仅在本次独立目录中；成品验证通过后才清理该目录。失败目录可能包含排错信息，继续处理时由维护者确认具体目录。

发布使用支持硬链接的文件系统，例如 NTFS、ext4 或 APFS。若目标介质不支持硬链接，请先保存到支持的本地文件系统，再把已经验证的 MP4 复制到目标介质。

## 可选抖音/TikTok 后端

先尝试 yt-dlp；它无法正常下载且用户明确配置本地后端时，才运行扩展。配置方法：

```text
python scripts/download_media.py download "SHORT_VIDEO_URL" --short-video-backend "TRUSTED_BACKEND.py"
```

或使用 `MANSUR_SHORT_VIDEO_BACKEND` 环境变量。该模块是会执行的本地 Python 代码，只应配置自己信任的后端。项目不自动下载、安装或向其他在线解析站发送链接。

后端实现 `download_url(url, options)`，其中 `options` 包括：

- `outputDir`：本次独立工作目录，成品源文件应放在这里。
- `cookiesFile`：明确提供 Cookie 时的副本路径。
- `cookiesFromBrowser`：明确指定的浏览器来源。

后端返回字典，成功下载时 `ok` 为 `true`，并在 `outputDir` 下产生至少一个完整视频文件。`downloaded: false` 或 `status: queued` 不算下载成功。建议文件名含平台视频 ID；文件需使用完整扩展名，不能把分片命名为最终 MP4。

扩展在单独进程中运行，有时间限制。源文件仍会经过本项目的格式转换与完整验证。后端的 Python 依赖、浏览器或其他工具由该后端自己的安装说明负责；主 Skill 不假定它们已存在。
