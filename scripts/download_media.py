"""Mansur Video Download: verified downloads with iPhone-compatible delivery."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

ROOT = Path(__file__).resolve().parent
VERSION = "0.1.0"
DEFAULT_OUTPUT = Path(os.environ.get("MANSUR_VIDEO_OUTPUT_DIR", str(Path.home() / "Downloads" / "MansurVideos")))
MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts"}


class WorkflowError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def safe_error(value: str) -> str:
    value = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", value)
    value = re.sub(r'''(?i)["']?(cookie|authorization|token|signature|credential)["']?(\s*[:=]\s*)[^\r\n]+''',
                   r"\1\2[redacted]", value)
    return value[-3000:]


def public_url(url: str) -> str:
    parts = urlsplit(url)
    public_keys = ({"v", "list", "index", "start_radio"} if (parts.hostname or "").lower()
                   in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"} else set())
    query = urlencode([(key, value if key in public_keys else "[redacted]")
                       for key, value in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_tool(name: str) -> str:
    override = os.environ.get("MANSUR_" + name.upper().replace("-", "_") + "_BIN")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file():
            raise WorkflowError("environment", f"Configured {name} executable does not exist")
        return str(path)
    local = ROOT / (name + (".exe" if os.name == "nt" else ""))
    found = str(local) if local.is_file() else shutil.which(name)
    if not found:
        raise WorkflowError("environment", f"Missing tool: {name}")
    return str(Path(found).resolve())


@dataclass
class Tools:
    ytdlp: list[str]
    ffmpeg: str
    ffprobe: str
    node: str | None

    @classmethod
    def discover(cls) -> Tools:
        ytdlp = ([find_tool("yt-dlp")] if os.environ.get("MANSUR_YT_DLP_BIN") else
                 [sys.executable, "-m", "yt_dlp"] if importlib.util.find_spec("yt_dlp")
                 else [find_tool("yt-dlp")])
        node = find_tool("node") if os.environ.get("MANSUR_NODE_BIN") else shutil.which("node")
        return cls(ytdlp, find_tool("ffmpeg"), find_tool("ffprobe"), node)

    def describe(self) -> dict:
        commands = {"yt_dlp": self.ytdlp + ["--version"], "ffmpeg": [self.ffmpeg, "-version"],
                    "ffprobe": [self.ffprobe, "-version"]}
        if self.node:
            commands["node"] = [self.node, "--version"]
        result = {}
        for name, command in commands.items():
            try:
                process = subprocess.run(command, capture_output=True, text=True,
                                         encoding="utf-8", errors="replace", timeout=20)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise WorkflowError("environment", f"Cannot run {name}: {exc}") from exc
            if process.returncode:
                raise WorkflowError("environment", f"Cannot run {name}")
            lines = (process.stdout or process.stderr).splitlines()
            result[name] = {"command": command[:-1], "version": lines[0] if lines else "unknown"}
        result["other_yt_dlp_candidates"] = list(dict.fromkeys(
            str(p.resolve()) for p in [ROOT / "yt-dlp.exe", Path(shutil.which("yt-dlp") or "__missing__"),
                Path(sys.executable).parent / "Scripts" / "yt-dlp.exe"] if p.is_file()))
        return result


def run(command: list[str], stage: str, work: Path, timeout: int) -> str:
    token = uuid.uuid4().hex[:8]
    stdout_path, stderr_path = work / f"{stage}_{token}.out", work / f"{stage}_{token}.err"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
        try:
            process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise WorkflowError(stage, "Interrupted" if isinstance(exc, KeyboardInterrupt)
                                else f"Timed out after {timeout}s") from exc
    error = stderr_path.read_text(encoding="utf-8", errors="replace")
    output = stdout_path.read_text(encoding="utf-8", errors="replace")
    if process.returncode or (stage == "decode" and error.strip()):
        raise WorkflowError(stage, safe_error(error or output) or f"Process exited with {process.returncode}")
    return output


@contextmanager
def download_lock(output: Path, identity: str):
    locks = output / "._download_locks"
    locks.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    with (locks / f"{digest}.lock").open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise WorkflowError("lock", "This download or output file is already in use") from exc
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def canonical_url(raw: str) -> str:
    parts = urlsplit(raw.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise WorkflowError("input", "Expected a video page URL")
    host = parts.hostname.lower().removeprefix("www.")
    if host in {"youtube.com", "m.youtube.com"} and parts.path == "/watch":
        video_id = parse_qs(parts.query).get("v", [""])[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    if host in {"youtube.com", "m.youtube.com"}:
        match = re.fullmatch(r"/(shorts|embed)/([^/]+)/?", parts.path)
        if match:
            return f"https://www.youtube.com/watch?v={match[2]}"
    if host == "youtu.be":
        return f"https://www.youtube.com/watch?v={parts.path.strip('/')}"
    if host in {"x.com", "twitter.com", "mobile.twitter.com"}:
        match = re.search(r"/status/(\d+)", parts.path)
        if match:
            return f"https://x.com/i/status/{match[1]}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def platform_prefix(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return "YT"
    if host in {"x.com", "www.x.com", "twitter.com", "mobile.twitter.com"}:
        return "X"
    if host == "douyin.com" or host.endswith(".douyin.com"):
        return "DY"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "TT"
    if host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.watch":
        return "FB"
    return "VIDEO"


def quality_sort(height: int) -> str:
    # yt-dlp's res field uses the smaller dimension, including portrait videos.
    return f"res:{height},vcodec:h264,acodec:aac" if height else "res,vcodec:h264,acodec:aac"


def probe(path: Path, tools: Tools, work: Path) -> dict:
    try:
        payload = json.loads(run([tools.ffprobe, "-v", "error", "-show_streams", "-show_format",
                                  "-of", "json", str(path)], "probe", work, 60))
    except json.JSONDecodeError as exc:
        raise WorkflowError("probe", "Invalid media inspection result") from exc
    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = float(payload.get("format", {}).get("duration") or video.get("duration") or 0)
    if (not video.get("width") or not video.get("height") or not math.isfinite(duration)
            or duration <= 0 or path.stat().st_size == 0):
        raise WorkflowError("probe", "File has no valid video, duration or data")
    return {"path": str(path.resolve()), "bytes": path.stat().st_size,
            "duration_seconds": round(duration, 3), "width": video["width"], "height": video["height"],
            "video_codec": video.get("codec_name"), "pixel_format": video.get("pix_fmt"),
            "audio_codec": audio.get("codec_name"), "frame_rate": video.get("avg_frame_rate")}


def compatible(info: dict) -> bool:
    return (info.get("video_codec") == "h264" and info.get("pixel_format") == "yuv420p"
            and info.get("audio_codec") in {None, "aac"})


def faststart(path: Path) -> bool:
    size, moov, mdat = path.stat().st_size, None, None
    with path.open("rb") as file:
        while file.tell() + 8 <= size:
            offset = file.tell()
            length, kind = struct.unpack(">I4s", file.read(8))
            header_size = 8
            if length == 1:
                extended = file.read(8)
                if len(extended) != 8:
                    return False
                length, header_size = struct.unpack(">Q", extended)[0], 16
            elif length == 0:
                length = size - offset
            if length < header_size or offset + length > size:
                return False
            if kind == b"moov":
                moov = offset
            elif kind == b"mdat" and mdat is None:
                mdat = offset
            file.seek(offset + length)
    return moov is not None and mdat is not None and moov < mdat


def verify(path: Path, tools: Tools, work: Path, timeout: int, iphone: bool = True) -> dict:
    info = probe(path, tools, work)
    if iphone and (path.suffix.lower() != ".mp4" or not compatible(info) or not faststart(path)):
        raise WorkflowError("verify", "Output must be MP4, H.264/yuv420p, AAC (or silent), with faststart")
    run([tools.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", str(path),
         "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"], "decode", work, timeout)
    return {**info, "iphone_compatible": compatible(info) and path.suffix.lower() == ".mp4",
            "faststart": faststart(path) if path.suffix.lower() == ".mp4" else False,
            "full_decode_verified": True}


def safe_stem(value: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(". ")[:160]
    if not stem or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])", stem.split(".")[0]):
        raise WorkflowError("input", "Invalid output name")
    return stem


def validate_metadata(info: dict, metadata: dict) -> None:
    expected = metadata.get("duration")
    if expected is not None:
        duration = float(expected)
        if math.isfinite(duration) and duration > 0 and abs(info["duration_seconds"] - duration) > max(2.0, duration * .02):
            raise WorkflowError("verify", "Video duration does not match the platform metadata; existing files left untouched")
    width, height = metadata.get("width"), metadata.get("height")
    if width and height and min(info["width"], info["height"]) + 1 < min(width, height):
        raise WorkflowError("verify", "Existing or downloaded video is below the requested resolution; use a new output directory")


def read_provenance(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise WorkflowError("publish", "Cannot read the existing source fingerprint") from exc
    if not isinstance(payload, dict):
        raise WorkflowError("publish", "Invalid source fingerprint record")
    return payload


def prepare_video(source: Path, output: Path, stem: str, tools: Tools, work: Path, timeout: int) -> dict:
    stem = safe_stem(stem)
    target = output / f"{stem}.mp4"
    provenance = output / f"{stem}.source.json"
    source_info = probe(source, tools, work)
    with download_lock(output, "file:" + (target.name.casefold() if os.name == "nt" else target.name)):
        if target.exists():
            info = verify(target, tools, work, timeout)
            if source.resolve() != target.resolve():
                if not provenance.is_file():
                    raise WorkflowError("publish", "Existing file has no source fingerprint; use a different name or directory")
                stored = read_provenance(provenance)
                if (stored.get("source_sha256") != file_sha256(source)
                        or stored.get("file_sha256") != file_sha256(target)):
                    raise WorkflowError("publish", "Same output name conflicts with a different source or changed file; existing file left untouched")
            elif provenance.is_file():
                stored = read_provenance(provenance)
                if stored.get("file_sha256") != file_sha256(target):
                    raise WorkflowError("publish", "Existing file no longer matches its saved fingerprint")
            tolerance = max(2.0, source_info["duration_seconds"] * .02)
            if abs(info["duration_seconds"] - source_info["duration_seconds"]) > tolerance:
                raise WorkflowError("publish", "Existing output has a different duration; left untouched")
            return {**info, "reused": True, "converted": False}
        pending = work / f"ready_{stem}.mp4"
        source_digest = file_sha256(source)
        if provenance.exists():
            raise WorkflowError("publish", "Source record already exists without its video; use a different name or directory")
        video_copy = source_info["video_codec"] == "h264" and source_info["pixel_format"] == "yuv420p"
        command = [tools.ffmpeg, "-nostdin", "-v", "error", "-n", "-i", str(source),
                   "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "0"]
        if video_copy:
            command += ["-c:v", "copy"]
        else:
            command += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        command += (["-c:a", "copy"] if source_info["audio_codec"] in {None, "aac"}
                    else ["-c:a", "aac", "-b:a", "192k"])
        command += ["-movflags", "+faststart", str(pending)]
        run(command, "convert", work, timeout)
        info = verify(pending, tools, work, timeout)
        if abs(info["duration_seconds"] - source_info["duration_seconds"]) > max(2.0, source_info["duration_seconds"] * .02):
            raise WorkflowError("verify", "Converted duration differs from the source")
        # Same-volume hard links publish atomically without replacing existing files.
        os.link(pending, target)
        fingerprints = {"source_sha256": source_digest, "file_sha256": file_sha256(target)}
        write_json_exclusive(provenance, fingerprints)
        return {**info, "path": str(target.resolve()), "reused": False,
                "converted": not compatible(source_info), **fingerprints}


def cleanup_job(work: Path, output: Path) -> None:
    root = (output / "._download_work").resolve()
    resolved = work.resolve()
    if resolved.parent != root or not re.fullmatch(r"[0-9a-f]{32}", work.name) or work.is_symlink():
        raise WorkflowError("cleanup", "Refusing to remove a directory outside this job")
    shutil.rmtree(resolved)


def ytdlp_base(tools: Tools, args, cookies: Path | None, browser: str | None) -> list[str]:
    command = tools.ytdlp + ["--ignore-config", "--no-overwrites", "--windows-filenames",
        "--socket-timeout", "20", "--retries", "3", "--fragment-retries", "3",
        "--ffmpeg-location", str(Path(tools.ffmpeg).parent),
        "--yes-playlist" if args.playlist else "--no-playlist"]
    if tools.node:
        command += ["--no-js-runtimes", "--js-runtimes", f"node:{tools.node}",
                    "--remote-components", "ejs:github"]
    if cookies:
        command += ["--cookies", str(cookies)]
    if browser:
        command += ["--cookies-from-browser", browser]
    return command


def cookie_strategies(args, prefix: str, work: Path) -> list[tuple[str, Path | None, str | None]]:
    source = Path(args.cookies).expanduser().resolve() if args.cookies else None
    if args.cookies_from_browser:
        return [("browser", None, args.cookies_from_browser)]
    if source is None and args.cookie_source in {"auto", "file"} and prefix == "YT":
        configured = os.environ.get("MANSUR_VIDEO_COOKIE_FILE")
        source = Path(configured).expanduser().resolve() if configured else None
    if source:
        if not source.is_file():
            raise WorkflowError("input", "Cookie file does not exist")
        copied = work / "private_cookies.txt"
        shutil.copyfile(source, copied)
        if args.cookies or args.cookie_source == "file":
            return [("cookie-file", copied, None)]
        return [("public", None, None), ("cookie-file", copied, None)]
    if args.cookie_source == "file":
        raise WorkflowError("input", "No cookie file found")
    return [("public", None, None)]


def metadata_items(payload: dict) -> list[dict]:
    if "entries" in payload:
        return [leaf for item in payload["entries"] or [] if isinstance(item, dict) for leaf in metadata_items(item)]
    return [payload]


def download_ytdlp(url: str, prefix: str, args, tools: Tools, work: Path, attempts: list) -> tuple[list[Path], dict]:
    last_error = "No extraction strategy available"
    for label, cookies, browser in cookie_strategies(args, prefix, work):
        base = ytdlp_base(tools, args, cookies, browser)
        selection = ["-f", args.format or "bestvideo+bestaudio/best"]
        if not args.format:
            selection += ["--format-sort", quality_sort(args.max_height)]
        try:
            metadata = json.loads(run(base + selection + ["--dump-single-json", "--skip-download", url],
                                      "metadata", work, min(args.timeout, 120)))
            if not isinstance(metadata, dict) or not metadata_items(metadata) or any(
                    not item.get("id") for item in metadata_items(metadata)):
                raise WorkflowError("metadata", "Missing media ID")
            attempts.append({"strategy": label, "stage": "metadata", "ok": True})
        except (WorkflowError, json.JSONDecodeError) as exc:
            last_error = safe_error(str(exc))
            attempts.append({"strategy": label, "stage": "metadata", "ok": False, "error": last_error})
            continue
        expected = [Path(args.output_dir).resolve() / f"{safe_stem(prefix + '_' + str(item['id']))}.mp4"
                    for item in metadata_items(metadata)]
        cached = all(p.is_file() for p in expected)
        if cached and not args.extras:
            attempts.append({"strategy": "existing-output", "stage": "download", "ok": True})
            return expected, metadata
        paths = work / "final_paths.jsonl"
        paths.unlink(missing_ok=True)
        command = base + selection + ["--merge-output-format", "mp4",
            "--paths", str(work), "-o", f"{prefix}_%(id)s.%(ext)s",
            "--print-to-file", "after_move:%(filepath)j", str(paths)]
        if cached:
            command += ["--skip-download"]
        if args.extras:
            command += ["--write-thumbnail", "--convert-thumbnails", "jpg", "--write-subs", "--write-auto-subs",
                        "--sub-langs", "all,-live_chat", "--embed-metadata"]
        try:
            run(command + [url], "attachments" if cached else "download", work, args.timeout)
            if cached:
                files = expected
            elif paths.exists():
                files = list(dict.fromkeys(Path(json.loads(line)).resolve() for line in
                    paths.read_text(encoding="utf-8").splitlines() if line.strip()))
            else:
                files = []
            if not files or any(not p.is_file() or (not cached and not p.is_relative_to(work.resolve())) for p in files):
                raise WorkflowError("download", "Downloader returned no final video file")
            attempts.append({"strategy": label, "stage": "attachments" if cached else "download", "ok": True})
            return files, metadata
        except (WorkflowError, json.JSONDecodeError) as exc:
            last_error = safe_error(str(exc))
            attempts.append({"strategy": label, "stage": "download", "ok": False, "error": last_error})
    raise WorkflowError(attempts[-1]["stage"] if attempts else "metadata", last_error)


def short_video_worker(url: str, output: Path, backend: str,
                       cookies: str | None = None, browser: str | None = None) -> None:
    backend_path = Path(backend).expanduser().resolve()
    if not backend_path.is_file():
        raise WorkflowError("backend", "Configured short-video backend does not exist")
    sys.path.insert(0, str(backend_path.parent))
    spec = importlib.util.spec_from_file_location("mansur_short_video_backend", backend_path)
    if spec is None or spec.loader is None:
        raise WorkflowError("backend", "Cannot load the configured Python backend")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "download_url", None)):
        raise WorkflowError("backend", "Backend must implement download_url(url, options)")
    options = {"outputDir": str(output)}
    if cookies:
        options["cookiesFile"] = cookies
    if browser:
        options["cookiesFromBrowser"] = browser
    result = module.download_url(url, options)
    if not isinstance(result, dict):
        raise WorkflowError("backend", "Backend must return a result dictionary")
    downloaded = result.get("ok") and result.get("downloaded") is not False and result.get("status") != "queued"
    print(json.dumps({"ok": bool(downloaded)}))
    if not downloaded:
        raise WorkflowError("download", "Existing short-video backend did not download a video")


def download_short_video(url: str, args, work: Path, attempts: list) -> tuple[list[Path], dict]:
    _, cookies, browser = cookie_strategies(args, platform_prefix(url), work)[0]
    command = [sys.executable, str(Path(__file__).resolve()), "short-video-worker", url, str(work),
               "--backend", args.short_video_backend]
    if cookies:
        command += ["--cookies-file", str(cookies)]
    if browser:
        command += ["--cookies-from-browser", browser]
    run(command, "short-video", work, args.timeout)
    files = [p for p in work.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES
             and not re.search(r"\.f[\w-]+\.(mp4|webm|m4a)$", p.name)]
    if not files or any(not p.resolve().is_relative_to(work.resolve()) for p in files):
        raise WorkflowError("download", "Short-video backend returned no final video file")
    attempts.append({"strategy": "existing-short-video-backend", "stage": "download", "ok": True})
    return files, {}


def write_json_exclusive(path: Path, payload: dict) -> None:
    try:
        with path.open("x", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
    except FileExistsError:
        pass


def execute_job(args, tools: Tools) -> dict:
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    work = output / "._download_work" / job_id
    work.mkdir(parents=True)
    report = {"ok": False, "job_id": job_id, "command": args.command, "files": [], "attempts": [],
              "started_at": datetime.now(timezone.utc).isoformat()}
    reports = output / "._download_reports"
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / f"{job_id}.json"
    report["report_path"] = str(report_path)
    try:
        report["tools"] = tools.describe()
        if args.command == "prepare":
            source = Path(args.path).expanduser().resolve()
            report["files"] = [prepare_video(source, output, args.name or source.stem, tools, work, args.timeout)]
        else:
            normalized = canonical_url(args.url)
            prefix = platform_prefix(normalized)
            # Preserve playlist parameters only when the user requested a playlist.
            url = args.url.strip() if args.playlist else normalized
            report["source_url"] = public_url(normalized)
            with download_lock(output, "url:" + url):
                try:
                    files, metadata = download_ytdlp(url, prefix, args, tools, work, report["attempts"])
                except WorkflowError as primary_error:
                    backend = getattr(args, "short_video_backend", None)
                    if prefix not in {"DY", "TT"} or not backend:
                        raise
                    report["attempts"].append({"strategy": "yt-dlp", "stage": primary_error.stage,
                                               "ok": False, "error": safe_error(str(primary_error))})
                    files, metadata = download_short_video(url, args, work, report["attempts"])
                items = metadata_items(metadata) if metadata else []
                for source in files:
                    matching = next((item for item in items if source.stem == safe_stem(f"{prefix}_{item.get('id')}")), {})
                    if matching:
                        validate_metadata(probe(source, tools, work), matching)
                    stem = source.stem
                    if prefix in {"DY", "TT"}:
                        match = re.search(r"\d{8,}", stem)
                        stem = f"{prefix}_{match[0]}" if match else f"{prefix}_{stem}"
                    item = prepare_video(source, output, stem, tools, work, args.timeout)
                    report["files"].append(item)
                    summary = {"source_url": public_url(normalized), "id": matching.get("id"), "title": matching.get("title"),
                               "uploader": matching.get("uploader"), "verified_file": item}
                    write_json_exclusive(output / f"{stem}.info.json", summary)
                if args.extras:
                    for sidecar in work.iterdir():
                        if sidecar.suffix.lower() in {".jpg", ".png", ".srt", ".vtt", ".ass"}:
                            try:
                                os.link(sidecar, output / sidecar.name)
                            except FileExistsError:
                                pass
        report["ok"] = True
        report["stage"] = "complete"
        cleanup_job(work, output)
        report["temporary_files_cleaned"] = True
    except (WorkflowError, OSError, ValueError) as exc:
        report["ok"] = False
        report["stage"] = getattr(exc, "stage", "filesystem")
        report["error"] = safe_error(str(exc))
        report["retained_work_dir"] = str(work)
        try:
            (work / "private_cookies.txt").unlink(missing_ok=True)
        except OSError as cookie_error:
            report["credential_cleanup_error"] = safe_error(str(cookie_error))
        for log in work.iterdir():
            if log.is_file() and re.fullmatch(r"[A-Za-z-]+_[0-9a-f]{8}\.(out|err)", log.name):
                try:
                    log.write_text(safe_error(log.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
                except OSError as log_error:
                    report.setdefault("log_redaction_errors", []).append(safe_error(str(log_error)))
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json_exclusive(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check the exact tools that this workflow uses")
    download = commands.add_parser("download", help="Download and deliver a verified iPhone-compatible MP4")
    download.add_argument("url")
    download.add_argument("--cookies", help="Exported cookie file; copied privately, original kept unchanged")
    download.add_argument("--cookies-from-browser")
    download.add_argument("--cookie-source", choices=["auto", "file", "none"], default="auto")
    download.add_argument("--playlist", action="store_true")
    download.add_argument("--format", default="")
    download.add_argument("--max-height", type=int, default=1080,
                          help="Preferred resolution (smaller dimension); 0 means highest available")
    download.add_argument("--extras", action="store_true", help="Also save available subtitles and thumbnails")
    download.add_argument("--short-video-backend", default=os.environ.get("MANSUR_SHORT_VIDEO_BACKEND"),
                          help="Optional trusted Python backend implementing download_url(url, options)")
    prepare = commands.add_parser("prepare", help="Convert and verify a local source without deleting it")
    prepare.add_argument("path")
    prepare.add_argument("--name", help="Final filename without the .mp4 suffix")
    for command in (download, prepare):
        command.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
        command.add_argument("--timeout", type=int, default=7200)
    inspect = commands.add_parser("verify", help="Inspect and fully decode an existing video")
    inspect.add_argument("path")
    inspect.add_argument("--timeout", type=int, default=7200)
    inspect.add_argument("--source", action="store_true", help="Allow a source that still needs conversion")
    worker = commands.add_parser("short-video-worker", help=argparse.SUPPRESS)
    worker.add_argument("url")
    worker.add_argument("output_dir")
    worker.add_argument("--backend", required=True)
    worker.add_argument("--cookies-file")
    worker.add_argument("--cookies-from-browser")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        if getattr(args, "timeout", 1) <= 0 or getattr(args, "max_height", 0) < 0:
            raise WorkflowError("input", "Timeout must be positive and resolution cap cannot be negative")
        if args.command == "short-video-worker":
            short_video_worker(args.url, Path(args.output_dir), args.backend,
                               args.cookies_file, args.cookies_from_browser)
            return 0
        if getattr(args, "cookies", None) and getattr(args, "cookies_from_browser", None):
            raise WorkflowError("input", "Choose a cookie file or a browser, not both")
        tools = Tools.discover()
        if args.command == "doctor":
            result = {"ok": True, "tools": tools.describe(), "default_output": str(DEFAULT_OUTPUT),
                      "version": VERSION, "short_video_backend_configured": bool(os.environ.get("MANSUR_SHORT_VIDEO_BACKEND"))}
        elif args.command == "verify":
            with tempfile.TemporaryDirectory(prefix="yt_verify_") as temporary:
                result = {"ok": True, "file": verify(Path(args.path).resolve(), tools,
                          Path(temporary), args.timeout, iphone=not args.source)}
        else:
            result = execute_job(args, tools)
    except (WorkflowError, OSError, ValueError) as exc:
        result = {"ok": False, "stage": getattr(exc, "stage", "filesystem"), "error": safe_error(str(exc))}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
