"""Query an exported audit and run CodeGraph against one explicitly named checkout."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata

from export_obsidian import build_html, build_knowledge


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "audit.json"
TIER_ORDER = {tier: index for index, tier in enumerate(("adopt", "pilot", "watch", "reject", "unknown"))}


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def normalized(text):
    text = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    return "".join(char for char in text if not unicodedata.combining(char))


def find(packet, args):
    needle = normalized(args.text)
    rows = [
        row for row in packet["repos"]
        if needle in normalized(" ".join((row["repo"], row["project"], row["category_label"])))
        and (args.category is None or row["category"] == args.category)
        and (args.tier is None or row["tier"] == args.tier)
        and (not args.new or row["new"])
    ]
    rows.sort(key=lambda row: (TIER_ORDER[row["tier"]], row["category"], row["rank"], row["repo"].casefold()))
    results = rows[:args.limit]
    if args.json:
        print(json.dumps({"matches": len(rows), "repos": results}, ensure_ascii=False, indent=2))
        return
    for row in results:
        print(f'{row["repo"]}  {row["tier"]}  #{row["rank"]} / {row["category"]}')
        if row["project"] != row["repo"]:
            print(f'  Project: {row["project"]}')
        print(f'  Bằng chứng: {row["depth"]}')
        if row["details"].get("conditions"):
            print("  Điều kiện: " + row["details"]["conditions"].replace("\n", " "))
    print(f"{len(results)}/{len(rows)} repo")


def check(packet):
    rows = packet["repos"]
    expected = {row["repo"].casefold(): row for row in rows}
    if len(expected) != len(rows):
        raise ValueError("Duplicate repository identities")
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    block = re.search(r'<script type="application/json" id="audit-data">(.*?)</script>', html, re.S)
    if block is None or json.loads(block[1]) != packet:
        raise ValueError("HTML and JSON must contain the same snapshot")
    markdown = (ROOT / "knowledge.md").read_text(encoding="utf-8")
    links = re.findall(r"^\| \d+ \| [^|]+ \| \[([^\]]+)\]\(https://github\.com/[^)]+\)", markdown, re.M)
    if len(links) != len(rows) or {repo.casefold() for repo in links} != expected.keys():
        raise ValueError("Markdown and JSON must list the same repositories")
    known_categories = set(packet["categories"])
    if any(row["category"] not in known_categories or row["tier"] not in TIER_ORDER or row["rank"] < 1
           or not row["details"] for row in rows):
        raise ValueError("Invalid category, recommendation, rank or detail")
    print(json.dumps({"status": "ok", "atlas": packet["published"], "repos": len(rows),
                      "projects": len({row["project"] for row in rows}),
                      "categories": len(known_categories), "new": sum(row["new"] for row in rows)}))


def codegraph(args):
    binary = args.binary or shutil.which("codegraph")
    if not binary:
        raise ValueError("CodeGraph is not on PATH. Supply --binary with its installed executable.")
    binary = str(Path(binary).expanduser().resolve())
    project = args.project.expanduser().resolve(strict=True)
    if not project.is_dir() or project == project.parent or project == Path.home().resolve():
        raise ValueError("Specify a concrete source checkout for --project.")
    if (args.action == "query") != bool(args.symbol):
        raise ValueError("Supply a symbol only for CodeGraph query.")
    index = project / ".codegraph" / "codegraph.db"
    if args.action == "init" and index.exists():
        raise ValueError("This checkout already has an index. Run codegraph sync to refresh it.")
    if args.action != "init" and not index.is_file():
        raise ValueError("This checkout has no index. Run codegraph init on the named checkout first.")
    command = [binary, args.action]
    command += [args.symbol, "--path", str(project), "--json"] if args.symbol else [str(project)]
    env = {**os.environ, "CODEGRAPH_NO_DAEMON": "1", "CODEGRAPH_NO_WATCH": "1",
           "CODEGRAPH_TELEMETRY": "0", "DO_NOT_TRACK": "1"}
    return subprocess.run(command, cwd=project, env=env, check=False).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    lookup = commands.add_parser("find", help="Find and shortlist repositories in the audit")
    lookup.add_argument("text", nargs="?", default="")
    lookup.add_argument("--category")
    lookup.add_argument("--tier", choices=TIER_ORDER)
    lookup.add_argument("--new", action="store_true")
    lookup.add_argument("--json", action="store_true")
    lookup.add_argument("--limit", type=int, default=10)
    commands.add_parser("check", help="Verify matching HTML, JSON, and Markdown")
    commands.add_parser("render", help="Rebuild HTML and Markdown from the exported JSON")
    graph = commands.add_parser("codegraph", help="Operate an installed local CodeGraph index")
    graph.add_argument("action", choices=("init", "sync", "status", "query"))
    graph.add_argument("symbol", nargs="?")
    graph.add_argument("--project", type=Path, required=True)
    graph.add_argument("--binary", help="Path to an installed CodeGraph executable")
    args = parser.parse_args()
    try:
        if args.command == "codegraph":
            return codegraph(args)
        packet = load()
        if args.command == "check":
            check(packet)
        elif args.command == "render":
            build_html(packet)
            build_knowledge(packet)
            check(packet)
        elif args.command == "find":
            if args.limit < 1:
                parser.error("--limit must be positive")
            if args.category is not None and args.category not in packet["categories"]:
                parser.error("Unknown category. See the category labels in data/audit.json.")
            find(packet, args)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
