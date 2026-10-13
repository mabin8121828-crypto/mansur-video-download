# Mansur Video Download

把在线视频下载、iPhone 格式处理和成品验证组合成一个可安装的 Codex Skill。

**重点是可交付的成品：下载程序显示成功后，还要确认文件存在、编码正确，并完整解码整段视频。**

Portable Codex skill for downloading videos, producing iPhone-compatible MP4 files, and verifying the complete result. Includes a Python CLI, isolated work directories, cookie-file copies, and regression tests.

## 功能

- 复用 yt-dlp 下载 YouTube、X、Facebook 及其支持的平台。
- 默认输出 MP4 / H.264 / AAC / yuv420p / faststart。无声音的视频保持无声音。
- 同画质优先兼容编码；已兼容的画面不重新编码。其他画面编码转换为 H.264。
- 默认优先 1080p，竖屏按短边计算，避免把 `1080×1920` 降到较低画质。
- 支持用户导出的 Cookie 文件，只使用副本；原文件不改写、不默认删除。
- Windows 和 POSIX 文件锁；已有成品验证后复用，不覆盖损坏的同名文件。
- 每次下载使用独立临时目录，成功后只清理本次目录。
- 结构化结果报告、整段解码检查、真实转换和本地 HTTP 下载测试。

平台能否下载取决于 yt-dlp 支持情况、页面状态和正常登录权限。抖音/TikTok 可接入用户已有的可信 Python 后端；公开项目不附带我们私人工作目录中的后端，也不承诺这些平台每个链接都能解析。不会额外去水印、翻译或烧录字幕。

## 安装

依赖：Python 3.10+、[yt-dlp](https://github.com/yt-dlp/yt-dlp)、[FFmpeg / ffprobe](https://ffmpeg.org/download.html)。YouTube 的 JavaScript 挑战处理建议安装 [Node.js](https://nodejs.org/en/download)。

```text
git clone https://github.com/mabin8121828-crypto/mansur-video-download.git
cd mansur-video-download
python -m pip install -r requirements.txt
python scripts/install_skill.py
python scripts/download_media.py doctor
```

安装器只复制公开包中声明的 Skill 文件，默认目标为 `~/.agents/skills/mansur-video-download`；不会复制视频、Cookie、日志或测试产物。指定其他位置：

```text
python scripts/install_skill.py --dest "CUSTOM_SKILL_DIRECTORY"
```

已有安装不会默认覆盖。检查更新后可执行 `python scripts/install_skill.py --update`；更新只涉及包内声明的文件，不删除目标目录中的其他文件。

安装目录依据 [Codex 官方 Skill 文档](https://developers.openai.com/codex/skills/) 的用户级 Skill 路径。Skill 未出现时重新打开 Codex。

在 Codex 中调用：

```text
使用 $mansur-video-download 下载这个链接，保存到我指定的目录，做成 iPhone 兼容 MP4。
```

## 直接使用

```text
python scripts/download_media.py download "VIDEO_URL"
python scripts/download_media.py download "VIDEO_URL" --output-dir "OUTPUT_DIR" --cookies "COOKIE_FILE"
python scripts/download_media.py download "VIDEO_URL" --max-height 0
python scripts/download_media.py prepare "SOURCE_FILE" --output-dir "OUTPUT_DIR" --name "FINAL_NAME"
python scripts/download_media.py verify "FINAL_FILE"
```

默认输出：`~/Downloads/MansurVideos`。可用环境变量 `MANSUR_VIDEO_OUTPUT_DIR` 修改默认值；`--output-dir` 优先。

`--max-height` 的历史名字保留，但实际按短边选择目标分辨率：`1080` 表示优先 1080p，`0` 表示优先最高可用画质。平台没有目标格式时会回退到可用格式。

明确要求播放列表时加 `--playlist`。需要平台字幕和封面时加 `--extras`；字幕只是单独文件，没有烧录进画面。

导出的 Cookie 可以逐次通过 `--cookies` 指定，也可在自己电脑上配置 `MANSUR_VIDEO_COOKIE_FILE`。公开视频自动模式先不带 Cookie 解析，必要时才使用所配置文件。浏览器读取仅通过明确的 `--cookies-from-browser chrome` 等参数开启。

Windows 路径和含空格的路径需加引号。脚本没有绑定作者的盘符、账号目录或私人 Cookie。

## 检查结果

```json
{
  "ok": true,
  "stage": "complete",
  "files": [{
    "video_codec": "h264",
    "audio_codec": "aac",
    "pixel_format": "yuv420p",
    "iphone_compatible": true,
    "faststart": true,
    "full_decode_verified": true
  }]
}
```

任务报告位于输出目录 `._download_reports`。失败时 `ok` 为 `false`，并记录失败步骤及保留的本次工作目录；Cookie 副本会清理。编码检查可以确认本项目要求的兼容格式，但无法证明某种手机传输工具一定会把文件导入相册。

## 验证与打包

```text
python -m pip install -r requirements-dev.txt
python scripts/validate_skill.py
python -m unittest discover -s tests -v
python scripts/package_skill.py
```

测试使用生成的短视频和本机 HTTP 服务，不需要私人 Cookie 或网上视频。发布包使用固定文件清单生成，不把整个工作目录打包。GitHub Actions 在 Windows 与 Linux 运行回归检查；具体结果以仓库 Actions 页面为准。

打包输出为 `dist/mansur-video-download-0.1.0.zip`，根目录包含 `mansur-video-download/SKILL.md`。

故障定位、环境覆盖与可选后端协议见 [references/troubleshooting.md](references/troubleshooting.md)。

## 来源与许可

MIT License，Copyright (c) 2026 Mansur。

工程化思路受到 [qiaomu-download](https://github.com/joeseesun/qiaomu-download) 的环境自检、分阶段结果和成品验证做法启发。执行代码来自我们自己的本地下载流程；yt-dlp、FFmpeg 与其他依赖各自保留原许可，发布包不包含它们的二进制文件。
