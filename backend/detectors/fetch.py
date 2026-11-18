"""fetch: download the image / video / audio behind a link into a temp file.

This is the one place the worker uses the network on purpose (models stay offline). Order:
  1. Known video sites (YouTube, TikTok, X, ...) -> backend/video_sites.py (yt-dlp), only if the
     user turned on "Video sites"; image posts fall back to the page's preview image.
  2. Anything else: GET it. Media -> save it. HTML -> download the video or image the page
     declares (og:video / og:image / twitter:* / <video>), video first.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser

from . import Cancelled, log

MAX_BYTES = 500 * 1024 * 1024
MAX_HTML = 3 * 1024 * 1024
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# Sites that need yt-dlp to get at the video. They (and their CDNs) also re-compress every upload.
VIDEO_SITES = {
    "youtube.com": "YouTube", "youtu.be": "YouTube", "tiktok.com": "TikTok", "x.com": "X",
    "twitter.com": "X", "instagram.com": "Instagram", "facebook.com": "Facebook", "fb.watch": "Facebook",
    "vimeo.com": "Vimeo", "twitch.tv": "Twitch", "reddit.com": "Reddit", "v.redd.it": "Reddit",
    "dailymotion.com": "Dailymotion", "threads.net": "Threads", "threads.com": "Threads",
    "bsky.app": "Bluesky", "linkedin.com": "LinkedIn", "streamable.com": "Streamable",
    "soundcloud.com": "SoundCloud", "bilibili.com": "Bilibili", "rumble.com": "Rumble", "kick.com": "Kick",
}
PLATFORM_CDNS = ("twimg.com", "cdninstagram.com", "fbcdn.net", "tiktokcdn.com", "googlevideo.com",
                 "ytimg.com", "redd.it", "vimeocdn.com", "bsky.network")


def _host(url: str) -> str:
    h = (urllib.parse.urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _match(host: str, domains) -> str | None:
    return next((d for d in domains if host == d or host.endswith("." + d)), None)


def _tempdir() -> str:
    d = os.path.join(tempfile.gettempdir(), "traels", "url", uuid.uuid4().hex)
    os.makedirs(d, exist_ok=True)
    return d


def _kind(ctype: str, name: str = "") -> str | None:
    ct = (ctype or "").split(";")[0].strip().lower()
    if not ct or ct in ("application/octet-stream", "binary/octet-stream"):
        ct = mimetypes.guess_type(name)[0] or ""
    if ct == "image/svg+xml":
        return None
    if ct == "application/ogg":
        return "audio"
    top = ct.split("/")[0]
    return top if top in ("image", "video", "audio") else None


class _Meta(HTMLParser):
    VIDEO_KEYS = ("og:video:secure_url", "og:video:url", "og:video", "twitter:player:stream")
    IMAGE_KEYS = ("og:image:secure_url", "og:image:url", "og:image", "twitter:image", "twitter:image:src")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.videos: list[str] = []  # src of <video> and <source type="video/...">
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"]
        elif a.get("src") and (tag == "video" or (tag == "source" and a.get("type", "").startswith("video/"))):
            self.videos.append(a["src"])
        elif tag == "link" and a.get("rel", "").lower() == "image_src" and a.get("href"):
            self.meta.setdefault("image_src", a["href"])
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def candidates(self, base: str) -> list[str]:
        # What the page declares as its own media comes first; inline players are often unrelated clips.
        urls = [self.meta[k] for k in self.VIDEO_KEYS if k in self.meta]
        urls += [self.meta[k] for k in self.IMAGE_KEYS + ("image_src",) if k in self.meta]
        urls += self.videos
        seen, out = set(), []
        for u in urls:
            u = urllib.parse.urljoin(base, u.strip())
            if u.startswith(("http://", "https://")) and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    @property
    def page_title(self) -> str:
        return (self.meta.get("og:title") or self.title).strip()


def _open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=30)  # noqa: S310 - http(s) only, checked by caller


def _filename(resp, url: str, kind: str, ctype: str) -> str:
    cd = resp.headers.get("Content-Disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    name = urllib.parse.unquote(m.group(1)) if m else os.path.basename(urllib.parse.urlparse(resp.url or url).path)
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .") or kind
    if not os.path.splitext(name)[1]:
        name += mimetypes.guess_extension((ctype or "").split(";")[0].strip()) or ""
    return name[:120]


def _save(resp, dest: str, ctx, label: str):
    total = int(resp.headers.get("Content-Length") or 0)
    done = 0
    with open(dest, "wb") as f:
        while True:
            ctx.check()
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            done += len(chunk)
            if done > MAX_BYTES:
                raise RuntimeError(f"That file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
            f.write(chunk)
            if total:
                ctx.progress(min(0.95, done / total * 0.95), f"Downloading from {label}")


def _thumb(path: str, d: str) -> str | None:
    """First video frame as a small JPEG, so the app can preview formats AVFoundation can't open."""
    try:
        import av
        with av.open(path) as c:
            for frame in c.decode(video=0):
                im = frame.to_image()
                im.thumbnail((480, 480))
                out = os.path.join(d, "thumb.jpg")
                im.convert("RGB").save(out, quality=85)
                return out
    except Exception as e:  # noqa: BLE001
        log(f"thumbnail failed: {e}")
    return None


def _media_kind(path: str, guess: str) -> str:
    """Trust the container over the Content-Type: 'video/mp4' can be audio-only, etc."""
    if guess == "image":
        return guess
    try:
        import av
        with av.open(path) as c:
            if c.streams.video and any((s.frames or 0) != 1 for s in c.streams.video):
                return "video"
            if c.streams.audio:
                return "audio"
    except Exception:  # noqa: BLE001
        pass
    return guess


def _result(path, kind, title, host, d, platform):
    kind = _media_kind(path, kind)
    return {"path": path, "kind": kind, "title": title or os.path.basename(path), "source": host,
            "platform": platform, "thumb": _thumb(path, d) if kind == "video" else None}


def _direct(url: str, ctx, label: str, d: str, allow_html: bool):
    """-> ("media", result) | ("html", parser) | None (not media, html not wanted)."""
    resp = _open(url)
    with resp:
        ctype = resp.headers.get("Content-Type") or ""
        kind = _kind(ctype, urllib.parse.urlparse(resp.url or url).path)
        if kind:
            name = _filename(resp, url, kind, ctype)
            dest = os.path.join(d, name)
            _save(resp, dest, ctx, label)
            return "media", (dest, kind, name)
        if allow_html and "html" in ctype.lower():
            p = _Meta()
            p.feed(resp.read(MAX_HTML).decode(resp.headers.get_content_charset() or "utf-8", "replace"))
            return "html", p
    return None


def _from_page(p: _Meta, url: str, ctx, host: str, d: str, platform: bool):
    for cand in p.candidates(url):
        ctx.check()
        try:
            got = _direct(cand, ctx, host, d, allow_html=False)
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"candidate {cand} failed: {e}")
            continue
        if got:
            path, kind, _name = got[1]
            return _result(path, kind, p.page_title, host, d, platform or bool(_match(_host(cand), PLATFORM_CDNS)))
    return None


def _video_site(url: str, ctx, d: str, site: str, host: str):
    """-> result dict, or None if the post has no video (e.g. a photo post)."""
    uv = os.environ.get("TRAELS_UV") or shutil.which("uv")
    if not uv:
        raise RuntimeError("uv was not found, so video sites can't be downloaded.")
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "video_sites.py")
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIRTUAL_ENV")}
    ctx.progress(0.0, f"Getting the video from {site} (the first time also sets up the downloader)")
    proc = subprocess.Popen([uv, "run", "--quiet", "--script", script, url, d, str(MAX_BYTES // (1024 * 1024))],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    final = None
    try:
        for line in proc.stdout:
            if ctx.cancel_event.is_set():
                proc.kill()
                raise Cancelled()
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "progress" in msg:
                ctx.progress(min(0.98, msg["progress"]), f"{msg.get('message', 'Downloading')} from {site}")
            elif "done" in msg or "error" in msg:
                final = msg
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if final is None:
        err = (proc.stderr.read() or "").strip().splitlines()
        raise RuntimeError(f"The {site} download stopped unexpectedly. {err[-1] if err else ''}".strip())
    if "error" in final:
        if final.get("no_video"):
            return None
        raise RuntimeError(f"Couldn't get the video from {site}: {final['error']}")
    done = final["done"]
    return _result(done["path"], "video", done.get("title"), host, d, True)


def fetch(req: dict, ctx) -> dict:
    url = (req.get("url") or "").strip()
    if urllib.parse.urlparse(url).scheme not in ("http", "https") or not _host(url):
        raise ValueError("Only http:// and https:// links can be checked.")
    host = _host(url)
    d = _tempdir()
    site_domain = _match(host, VIDEO_SITES)
    if site_domain:
        site = VIDEO_SITES[site_domain]
        if not req.get("video_sites"):
            raise RuntimeError(f"To check videos from {site}, turn on Video sites in Settings → General.")
        res = _video_site(url, ctx, d, site, host)
        if res:
            return res
        # A photo post: use the image the page shares for link previews.
        got = _direct(url, ctx, host, d, allow_html=True)
        res = got and got[0] == "html" and _from_page(got[1], url, ctx, host, d, True)
        if res:
            return res
        raise RuntimeError(f"Couldn't find a video or image in that {site} post.")

    ctx.progress(0.0, f"Downloading from {host}")
    got = _direct(url, ctx, host, d, allow_html=True)
    if got is None:
        raise RuntimeError("That link isn't an image, video, audio file or web page.")
    platform = bool(_match(host, PLATFORM_CDNS))
    if got[0] == "media":
        path, kind, name = got[1]
        return _result(path, kind, name, host, d, platform)
    res = _from_page(got[1], url, ctx, host, d, platform)
    if res:
        return res
    raise RuntimeError("Couldn't find an image or video on that page.")
