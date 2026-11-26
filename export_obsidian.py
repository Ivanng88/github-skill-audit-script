"""Render a standalone GitHub Skill Audit from the published Obsidian notes."""

import argparse
from collections import Counter
from datetime import date, datetime
from hashlib import sha256
import html
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
DETAIL_LINK = re.compile(r"\[\[IT ([a-f0-9]{24})\|Phân tích\]\]")
REPO_LINK = re.compile(r"\[([\w./-]+)( \(mới\))?\]\((https://github\.com/[\w./-]+)\)")
CATEGORY_LINK = re.compile(r"\[\[IT Project Atlas - (\w+) - (\d{4}-\d{2}-\d{2})\|([^\]]+)\]\]")
COUNTS = re.compile(r"(\d+) hạng mục · (\d+) project · (\d+) repo\. (\d+) repo")
URL = re.compile(r"https?://[^\s<>\"'\])]+")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HOST_PATH = re.compile(r"(?i)(?:/Users/|/Volumes/|/home/|file://|obsidian://|~[/\\])\S+")
SOURCE_NUMBER = re.compile(r"\b(?:[0-9]{13,20}|(?:post|reel|video)[_-]?[0-9]{7,20})\b", re.I)
SECRET = re.compile(r"(?i)\b(?:gh[pousr]_[a-z0-9]{20,}|github_pat_[\w_]{20,}|sk-[a-z0-9_-]{20,})\b")
TIER = {"adopt", "pilot", "watch", "reject", "unknown"}


def clean(value):
    value = html.unescape(value.replace("<br>", "\n"))
    value = URL.sub("", value)
    value = HOST_PATH.sub("", value)
    value = EMAIL.sub("", value)
    value = SOURCE_NUMBER.sub("", value)
    value = SECRET.sub("", value)
    value = re.sub(r"(?m)^\s*(?:supported|analyst_inference|vendor_claim|insufficient_evidence):\s*", "", value)
    return "\n".join(" ".join(line.split()).strip(" ·") for line in value.splitlines() if line.strip()).strip()


def fields(section):
    return {
        match[1]: match[2] for line in section.splitlines()
        if (match := re.match(r"^\*\*([^*]+):\*\*\s*(.*)$", line))
    }


def sections(text):
    parts = re.split(r"(?m)^## ([^\n]+)\n", text)
    return {title.casefold().strip(): body for title, body in zip(parts[1::2], parts[2::2])}


def note_version(text):
    return sha256(text.encode("utf-8")).hexdigest()


def safe_evidence(text, repo):
    parts = repo.casefold().split("/")
    seen = {}
    for match in URL.finditer(text):
        u = match[0].rstrip(".,;:")
        parsed = urlsplit(u)
        path = parsed.path.strip("/").casefold().split("/")
        if (parsed.scheme == "https" and parsed.netloc == "github.com" and path[:2] == parts
                and len(path) > 4 and path[2] == "blob" and re.fullmatch(r"[a-f0-9]{40}", path[3])
                and not parsed.query and re.fullmatch(r"(?:L[0-9]+(?:-L[0-9]+)?)?", parsed.fragment)):
            seen[u] = None
    return list(seen)[:10]


def native_detail(section, repo):
    f = fields(section)
    pros, cons = [], []
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith(("| Pros |", "| ---")):
            continue
        pair = re.split(r"(?<!\\)\|", line)[1:-1]
        if len(pair) == 2:
            for cell, items in zip(pair, (pros, cons)):
                if cell.strip():
                    items.append(clean(cell.replace("\\|", "|")))
    intro = section.split("| Pros | Cons |", 1)[0]
    intro = "\n".join(line for line in intro.splitlines() if not line.lstrip().startswith("**"))
    return {
        "description": clean(intro),
        "architecture": clean(f.get("architecture", "")),
        "conditions": clean(f.get("requirements", "")),
        "application": clean(f.get("applicability", "")),
        "uncertainty": clean(f.get("uncertainty", "")),
        "pros": pros,
        "cons": cons,
        "references": safe_evidence(f.get("evidence", ""), repo),
    }


def legacy_list(current, historical):
    if current:
        return clean(current).splitlines()
    try:
        prior = json.loads(html.unescape(historical.replace("<br>", "\n")))
        if isinstance(prior, list):
            return [clean(line) for line in prior if isinstance(line, str)]
    except json.JSONDecodeError:
        pass
    return []


def legacy_detail(section, repo):
    f = fields(section)
    return {
        "architecture": clean(f.get("Current architecture", "")),
        "conditions": clean(f.get("Current requirements", "")),
        "license": clean(f.get("Current license", "")),
        "application": clean(f.get("Current applicability", "")),
        "uncertainty": clean(f.get("Current uncertainty", "")),
        "performance": clean(f.get("Current performance", "")),
        "tests": clean(f.get("Current test_evidence", "")),
        "effectiveness": clean(f.get("Current effectiveness", "")),
        "privacy": clean(f.get("Current privacy", "")),
        "pros": legacy_list(f.get("Current pros", ""), f.get("Pros", "")),
        "cons": legacy_list(f.get("Current cons", ""), f.get("Cons", "")),
        "references": safe_evidence(f.get("Commit/source links", ""), repo),
    }


def read(path):
    return path.read_bytes().decode("utf-8")


def assemble(knowledge, atlas_date, legacy_tag=None):
    if legacy_tag and not re.fullmatch(r"[A-Za-z0-9 _.-]+", legacy_tag):
        raise ValueError("Invalid legacy tag")
    folder = knowledge / "Repository Atlas"
    iso_date = atlas_date.isoformat()
    overview = read(folder / f"IT Project Atlas {iso_date}.md")
    categories = {category: label for category, version, label in CATEGORY_LINK.findall(overview)
                  if version == iso_date}
    if not categories:
        raise ValueError("The Obsidian Atlas contains no category links")
    hashes = {"atlas": note_version(overview)}
    all_rows = []
    for category, label in categories.items():
        note = read(folder / f"IT Project Atlas - {category} - {iso_date}.md")
        hashes[category] = note_version(note)
        old = {}
        if legacy_tag:
            old_note = folder / f"IT Atlas {category} {legacy_tag}.md"
            if old_note.is_file():
                old = sections(read(old_note))
        for project, block in sections_with_titles(note):
            for line in block.splitlines():
                if not re.match(r"^\| \d+ \|", line):
                    continue
                all_rows.append(make_row(knowledge, category, label, project, line, old))
    rows = {r["repo"].casefold(): r for r in all_rows}
    if len(rows) != len(all_rows):
        raise ValueError("Obsidian category notes contain duplicate repos")
    counts = (len(categories), len({r["project"] for r in all_rows}), len(all_rows),
              sum(r["new"] for r in all_rows))
    expected = COUNTS.search(overview)
    if expected is None or counts != tuple(map(int, expected.groups())):
        raise ValueError("Obsidian overview and category notes have different totals")
    return {
        "title": "GitHub Skill Audit",
        "source": "Obsidian · IT Project Atlas",
        "published": atlas_date.strftime("%d/%m/%Y"),
        "categories": categories,
        "source_hashes": hashes,
        "repos": all_rows,
    }


def sections_with_titles(text):
    parts = re.split(r"(?m)^## ([^\n]+)\n", text)
    return zip(parts[1::2], parts[2::2])


def make_row(knowledge, category, label, project, line, old):
    rank, link, tier, depth, pointer = line[2:].split(" | ", 4)
    repo, new, url = REPO_LINK.fullmatch(link).groups()
    assert tier in TIER and int(rank) > 0
    previous = legacy_detail(old[repo.casefold()], repo) if repo.casefold() in old else {}
    current = {}
    note = DETAIL_LINK.search(pointer)
    if note:
        name = "IT " + note[1] + ".md"
        path = knowledge / "Continuous Updates" / name
        all_sections = sections(read(path))
        if repo.casefold() not in all_sections:
            raise ValueError(f"The native Obsidian note misses {repo}")
        current = native_detail(all_sections[repo.casefold()], repo)
    if new and not current:
        raise ValueError(f"Missing source review for new repo {repo}")
    details = {**previous, **{key: value for key, value in current.items() if value}}
    if tier == "unknown" and depth == "Nguồn chưa truy cập được":
        details = {"uncertainty": "Chưa truy cập được nguồn ở lần rà soát. Chưa đủ bằng chứng để đánh giá."}
    elif category == "security":
        details = {
            "application": "Hồ sơ tham chiếu để đánh giá phòng thủ và kiểm tra phạm vi được phép.",
            "uncertainty": "Bản chia sẻ giữ mức khuyến nghị và độ sâu bằng chứng. Chi tiết kỹ thuật nằm trong hồ sơ Obsidian.",
        }
    return {
        "repo": repo, "url": url, "project": project, "category": category, "category_label": label,
        "rank": int(rank), "tier": tier, "depth": depth, "new": bool(new), "details": details,
    }


def build_html(packet):
    encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    if HOST_PATH.search(encoded) or EMAIL.search(encoded) or SECRET.search(encoded):
        raise ValueError("External artifact contains a local path, email or token-like value")
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = read(HERE / "template.html")
    assert template.count("__AUDIT_DATA__") == 1
    audit_date = datetime.strptime(packet["published"], "%d/%m/%Y").strftime("%d/%m/%Y")
    result = template.replace("__AUDIT_DATE__", audit_date).replace("__AUDIT_DATA__", encoded)
    (HERE / "index.html").write_text(result, encoding="utf-8")
    return len(result.encode("utf-8"))


def build_knowledge(packet):
    repos = packet["repos"]
    lines = [
        "# GitHub Skill Audit", "",
        f'Bản chụp Obsidian ngày {packet["published"]}. '
        f'{len(repos)} repo · {len({row["project"] for row in repos})} project · {len(packet["categories"])} hạng mục.',
        "",
        "Xếp hạng theo ưu tiên áp dụng và độ sâu bằng chứng trong từng hạng mục. Repo cùng mức và độ sâu được đồng hạng.",
        "Đánh giá, license, ưu nhược điểm và mã nguồn tham chiếu nằm trong [audit.json](data/audit.json) và [trang HTML](index.html).",
    ]
    for category, label in packet["categories"].items():
        lines.extend(("", f"## {label}", "", "| Hạng | Project | Repo | Khuyến nghị | Bằng chứng |",
                      "| ---: | --- | --- | --- | --- |"))
        category_repos = sorted((row for row in repos if row["category"] == category),
                                key=lambda row: (row["rank"], row["repo"].casefold()))
        for row in category_repos:
            lines.append(f'| {row["rank"]} | {row["project"]} | [{row["repo"]}]({row["url"]}) | {row["tier"]} | {row["depth"]} |')
    (HERE / "knowledge.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge-root", type=Path, required=True, help="Obsidian IT Knowledge Base folder")
    parser.add_argument("--atlas-date", type=date.fromisoformat, required=True)
    parser.add_argument("--legacy-tag", help="Optional suffix in IT Atlas <category> <legacy-tag>.md")
    args = parser.parse_args()
    packet = assemble(args.knowledge_root, args.atlas_date, args.legacy_tag)
    rows = packet["repos"]
    categories = Counter(row["category"] for row in rows)
    for row in rows:
        assert row["details"].get("architecture") or row["details"].get("application") or row["details"].get("uncertainty") or row["tier"] == "unknown"
    size = build_html(packet)
    (HERE / "data").mkdir(exist_ok=True)
    (HERE / "data" / "audit.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_knowledge(packet)
    print(json.dumps({"repos": len(rows), "categories": len(categories),
                      "projects": len({row["project"] for row in rows}),
                      "new": sum(row["new"] for row in rows), "bytes": size}))


if __name__ == "__main__":
    main()
