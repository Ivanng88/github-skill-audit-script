"""Render approved public video transcripts as a standalone static page."""

from html import escape
import json
import re
from urllib.parse import urlsplit

from export_obsidian import EMAIL, HOST_PATH, SECRET


SCHEMA = "it-public-transcripts/v1"
FIELDS = {"id", "source_url", "provenance", "partial", "text"}
FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com"}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
PRIVATE_TEXT = re.compile(r"docs\.google\.com|drive\.google\.com|\bmaster\.md\b|[/\\]transcripts[/\\]", re.I)


def is_public_video_url(url):
    if not isinstance(url, str) or not re.fullmatch(r"https://[\x21-\x7e]+", url):
        return False
    parsed = urlsplit(url)
    if parsed.fragment or parsed.netloc != parsed.hostname:
        return False
    if parsed.hostname in FACEBOOK_HOSTS:
        if parsed.path in {"/watch", "/watch/", "/video.php"}:
            return bool(re.fullmatch(r"v=[0-9]+", parsed.query))
        return not parsed.query and bool(
            re.fullmatch(r"/(?:[0-9]+/(?:posts|videos)|reel)/[0-9]+/?", parsed.path)
        )
    if parsed.hostname == "fb.watch":
        return not parsed.query and bool(re.fullmatch(r"/[A-Za-z0-9_-]+/?", parsed.path))
    if parsed.hostname in YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            return bool(re.fullmatch(r"v=[A-Za-z0-9_-]{11}", parsed.query))
        return not parsed.query and bool(re.fullmatch(r"/shorts/[A-Za-z0-9_-]{11}/?", parsed.path))
    return parsed.hostname == "youtu.be" and not parsed.query and bool(
        re.fullmatch(r"/[A-Za-z0-9_-]{11}/?", parsed.path)
    )


def render_transcript_page(path):
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(bundle, dict)
        or set(bundle) != {"schema", "transcripts"}
        or bundle["schema"] != SCHEMA
        or not isinstance(bundle["transcripts"], list)
        or not bundle["transcripts"]
    ):
        raise ValueError("Invalid or empty public transcript bundle")

    sections = []
    ids = set()
    for row in bundle["transcripts"]:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError("Unexpected fields in public transcript record")
        transcript_id, url, provenance, partial, text = (row[key] for key in (
            "id", "source_url", "provenance", "partial", "text"
        ))
        if (
            not isinstance(transcript_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", transcript_id)
            or transcript_id in ids
            or not is_public_video_url(url)
            or provenance not in ("collector", "legacy")
            or type(partial) is not bool
            or not isinstance(text, str)
            or not text.strip()
            or any(pattern.search(text) for pattern in (HOST_PATH, EMAIL, SECRET, PRIVATE_TEXT))
        ):
            raise ValueError("Invalid public transcript record or private text")
        ids.add(transcript_id)
        origin = "Collector" if provenance == "collector" else "Bản lưu trước đây"
        completeness = "Một phần" if partial else "Bản chép lời"
        sections.append(
            f'<article id="{transcript_id}"><h2><a href="#{transcript_id}">{transcript_id}</a></h2>'
            f'<p>{origin} · {completeness} · '
            f'<a href="{escape(url)}" rel="noreferrer">Video nguồn</a></p>'
            f'<pre dir="auto">{escape(text)}</pre></article>'
        )

    return (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Bản chép lời video công khai</title><style>'
        'body{max-width:76ch;margin:2rem auto;padding:0 1rem;font:1rem/1.6 system-ui,sans-serif;color:#182536}'
        'a{color:#1453a3}article{border-top:1px solid #bac5d3;padding:1rem 0}'
        'h1,h2{line-height:1.2}h2{overflow-wrap:anywhere}'
        'pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fa;padding:1rem}'
        '</style></head><body><main><p><a href="/">GitHub Skill Audit</a></p>'
        '<h1>Bản chép lời video công khai</h1>'
        f'<p>{len(sections)} video</p>{"".join(sections)}</main></body></html>\n'
    )
