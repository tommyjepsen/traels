# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = [
#   "yt-dlp[default]",
#   "deno",
#   "av",
# ]
# ///
"""Download one public video from a video site (YouTube, TikTok, X, ...) with yt-dlp.

Kept out of worker.py so yt-dlp and Deno (~40 MB, needed for YouTube's JS challenges) are only
installed once the user turns on "Video sites" in Settings.

Usage: video_sites.py URL OUT_DIR [MAX_MB]
Prints JSON lines on stdout: {"progress": 0.4, "message": "..."} ... then exactly one of
{"done": {"path", "title", "site", "duration"}} or {"error": "...", "no_video": bool}.
"""
import heapq
import json
import os
import sys


def emit(**kw):
    print(json.dumps(kw), flush=True)


class _Quiet:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): print(m, file=sys.stderr, flush=True)
    def error(self, m): print(m, file=sys.stderr, flush=True)


def _has_stream(path, kind):
    import av
    try:
        with av.open(path) as c:
            return bool(c.streams.video if kind == "video" else c.streams.audio)
    except Exception:  # noqa: BLE001
        return False


def _mux(video, audio, out):
    """Stream-copy the video track of one file and the audio track of another into Matroska,
    interleaved by time (no ffmpeg binary needed)."""
    import av
    with av.open(video) as iv, av.open(audio) as ia, av.open(out, "w", format="matroska") as o:
        vin, ain = iv.streams.video[0], ia.streams.audio[0]
        vout, aout = o.add_stream_from_template(vin), o.add_stream_from_template(ain)

        def packets(src, sin, sout, order):
            for pkt in src.demux(sin):
                if pkt.dts is None:
                    continue
                pkt.stream = sout
                yield float(pkt.dts * sin.time_base), order, pkt

        for _t, _o, pkt in heapq.merge(packets(iv, vin, vout, 0), packets(ia, ain, aout, 1),
                                       key=lambda x: (x[0], x[1])):
            o.mux(pkt)


def main():
    url, out = sys.argv[1], sys.argv[2]
    max_mb = int(sys.argv[3]) if len(sys.argv) > 3 else 500
    import deno
    import yt_dlp

    stage = {"name": "video", "lo": 0.0, "hi": 0.85}

    def hook(d):
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if total:
            f = min(1.0, (d.get("downloaded_bytes") or 0) / total)
            emit(progress=stage["lo"] + (stage["hi"] - stage["lo"]) * f, message=f"Downloading {stage['name']}")

    base = {
        "logger": _Quiet(), "noprogress": True, "noplaylist": True, "playlist_items": "1",
        "js_runtimes": {"deno": {"path": deno.find_deno_bin()}},
        "outtmpl": os.path.join(out, "%(id)s.f%(format_id)s.%(ext)s"),
        "max_filesize": max_mb * 1024 * 1024, "progress_hooks": [hook],
    }

    def download(info, fmt, sort):
        # Re-extract in the downloading session: YouTube's media URLs are tied to the session that
        # resolved them, and reusing `info` from another YoutubeDL instance gets HTTP 403.
        with yt_dlp.YoutubeDL({**base, "format": fmt, "format_sort": sort}) as y:
            res = y.extract_info(info.get("webpage_url") or url, download=True)
        if res.get("_type") == "playlist":
            res = next(iter(res.get("entries") or []), None) or {}
        files = [r.get("filepath") for r in res.get("requested_downloads") or []]
        if not files or not files[0] or not os.path.exists(files[0]):
            raise RuntimeError("The download did not produce a file (it may be larger than the size limit).")
        return files[0]

    try:
        emit(progress=0.0, message="Looking up the video")
        with yt_dlp.YoutubeDL(base) as y:
            info = y.extract_info(url, download=False)
        if info.get("_type") == "playlist":
            info = next(iter(info.get("entries") or []), None)
            if not info:
                raise RuntimeError("That link is an empty playlist.")
        duration = info.get("duration") or 0
        title = info.get("title") or info.get("id") or "Video"
        formats = info.get("formats") or [info]
        has_video = any((f.get("vcodec") or "none") != "none" for f in formats)
        has_audio = any((f.get("acodec") or "none") != "none" for f in formats)

        # We only sample 12 frames, so long videos don't need full resolution.
        height = 1080 if duration <= 600 else 480
        if has_video:
            path = download(info, "bv*/b", [f"res:{height}", "vcodec:h264", "proto:https"])
            if has_audio and not _has_stream(path, "audio"):
                stage.update(name="audio", lo=0.85, hi=0.97)
                audio = download(info, "ba/b", ["acodec:aac", "proto:https"])
                emit(progress=0.97, message="Joining video and audio")
                merged = os.path.join(out, "video.mkv")
                try:
                    _mux(path, audio, merged)
                    for part in (path, audio):
                        os.remove(part)
                    path = merged
                except Exception as e:  # noqa: BLE001  - frames alone still work
                    print(f"mux failed, using video only: {e}", file=sys.stderr, flush=True)
        else:
            stage.update(name="audio", lo=0.0, hi=0.97)
            path = download(info, "ba/b", ["acodec:aac", "proto:https"])
        emit(done={"path": path, "title": title, "site": info.get("extractor_key") or "",
                   "duration": duration})
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace("ERROR: ", "").strip() or e.__class__.__name__
        emit(error=msg, no_video="no video formats" in msg.lower() or "no video could be found" in msg.lower())


if __name__ == "__main__":
    main()
