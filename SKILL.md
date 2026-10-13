---
name: mansur-video-download
description: "下载用户提供链接的在线视频，并交付经过完整解码验证的 iPhone 兼容 MP4；也用于把已有本地视频转换为兼容格式或检查下载成品。适用于 YouTube、X、Facebook 及 yt-dlp 支持的平台。仅分析视频、创作视频、字幕翻译或去水印任务不触发。"
license: "MIT"
metadata:
  author: "Mansur"
  version: "0.1.0"
---

# Mansur Video Download

将用户提供的链接保存为可核查的本地成品。复用随附脚本执行下载、必要的兼容转换和整段解码检查。

## 输入与输出

- 使用用户指定的输出目录、Cookie 文件和画质。没有目录要求时，脚本默认保存到 `~/Downloads/MansurVideos`，也可通过 `MANSUR_VIDEO_OUTPUT_DIR` 配置。
- 默认输出 MP4、H.264、yuv420p、AAC 和 faststart；原视频无声音时保持无声音。兼容的画面只重新封装，不重新编码。
- 默认优先 1080p，按画面短边排序以保留竖屏 `1080×1920`。用户要求最高画质时传 `--max-height 0`；这个参数是偏好，不保证平台一定提供对应画质。
- 默认只下载给出的单条视频。只有用户要求播放列表时才传 `--playlist`。
- 用户明确要求原始封装或其他编码时，尊重该要求，使用底层 yt-dlp 保存其指定格式并验证，不强制做 iPhone 转换。

## 执行

将下面的 `<skill-dir>` 换成当前 Skill 的实际目录，命令参数分别传递，正确引用路径和 URL。

```text
python "<skill-dir>/scripts/download_media.py" doctor
python "<skill-dir>/scripts/download_media.py" download "URL" --output-dir "OUTPUT_DIR"
python "<skill-dir>/scripts/download_media.py" download "URL" --output-dir "OUTPUT_DIR" --cookies "COOKIE_FILE"
python "<skill-dir>/scripts/download_media.py" prepare "SOURCE_FILE" --output-dir "OUTPUT_DIR" --name "OUTPUT_NAME"
python "<skill-dir>/scripts/download_media.py" verify "FINAL_FILE"
```

初次使用或环境报错时运行 `doctor`，确认实际调用的下载器、FFmpeg、ffprobe 和 Node。Python 依赖列在根目录 `requirements.txt`；FFmpeg/ffprobe 和 Node 需要本机可执行文件。不要把另一份 yt-dlp 的版本当成本次运行版本。依赖缺失或平台返回鉴权错误时，按 [故障处理](references/troubleshooting.md) 定位。

指定 Cookie 文件时直接使用其临时副本；自动模式先尝试公开视频，需要时使用 `MANSUR_VIDEO_COOKIE_FILE`。仅在用户指定时传 `--cookies-from-browser`。保留 Cookie 原文件，不在回答里显示 Cookie 内容。

抖音/TikTok 默认也先使用 yt-dlp。如果已有可信本地后端，可用 `--short-video-backend "BACKEND_PY_FILE"` 接入实现 `download_url(url, options)` 的模块。公开包不包含私人后端。没有后端且平台无法解析时，准确报告失败，不把待补全记录当作已下载。后端配置见故障处理文档。

## 成功判定与交付

脚本为每次任务创建独立工作目录。只将编码、时长和完整解码检查都通过的文件放入成品目录；已有成品验证后复用，异常时不覆盖。成功后仅清理本次工作目录；失败保留本次源文件和排错信息，同时删除 Cookie 副本。

每个任务的 JSON 报告位于输出目录 `._download_reports`。阅读其 `ok`、`stage` 和 `files`；退出码为零但没有有效成品，不算下载成功。任务仍在运行时继续跟踪到完成或明确失败。

用用户的对话语言交付绝对文件链接、大小、时长、分辨率，以及编码和完整解码检查结果。发生失败时说明具体步骤和必要的下一步。编码兼容检查不等于已在用户手机上实测。

默认不额外去水印，不烧录或翻译字幕。需要平台现有字幕和封面时加 `--extras`，下载字幕文件不能称作已经把字幕加进画面。
