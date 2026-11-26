"""Create and verify a Vercel release containing only the reviewed catalog."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from export_obsidian import build_html


CATALOG_FIELDS = {"title", "source", "source_hashes", "published", "categories", "repos"}
REPO_FIELDS = {
    "repo", "url", "project", "category", "category_label", "rank", "tier",
    "depth", "new", "details",
}
DETAIL_FIELDS = {
    "application", "architecture", "conditions", "cons", "description",
    "effectiveness", "license", "performance", "privacy", "pros",
    "references", "tests", "uncertainty",
}
INTERNAL_URL = re.compile(r"(?:docs\.google\.com|drive\.google\.com|facebook\.com|file://|obsidian://)", re.I)


def stage(output):
    raw = (ROOT / "data" / "audit.json").read_text(encoding="utf-8")
    packet = json.loads(raw)
    if not isinstance(packet, dict) or set(packet) != CATALOG_FIELDS or INTERNAL_URL.search(raw):
        raise ValueError("Report contains unexpected catalog fields or internal source URLs")
    if not packet["repos"] or any(
        set(repo) != REPO_FIELDS or set(repo["details"]) - DETAIL_FIELDS
        for repo in packet["repos"]
    ):
        raise ValueError("Report contains unexpected repository data")
    build_html(packet)
    static = output / ".vercel" / "output" / "static"
    static.mkdir(parents=True, exist_ok=False)
    (static / "index.html").write_bytes((ROOT / "index.html").read_bytes())
    (static.parent / "config.json").write_text('{"version":3}\n', encoding="utf-8")


def required_env(name, pattern):
    value = os.environ.get(name, "")
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Missing or invalid {name}")
    return value


def assert_protected_project():
    project_id = required_env("VERCEL_PROJECT_ID", r"prj_[A-Za-z0-9]+")
    team_id = required_env("VERCEL_ORG_ID", r"(?:team_)?[A-Za-z0-9]+")
    host = required_env("VERCEL_PRODUCTION_HOST", r"[a-z0-9-]+\.vercel\.app")
    token = required_env("VERCEL_TOKEN", r"\S+")
    query = "?" + urlencode({"teamId": team_id}) if team_id.startswith("team_") else ""
    endpoint = f"https://api.vercel.com/v9/projects/{project_id}"
    headers = {"Authorization": f"Bearer {token}"}
    with urlopen(Request(endpoint + query, headers=headers), timeout=20) as response:
        project = json.load(response)
    if (
        project.get("id") != project_id
        or project.get("accountId") != team_id
        or (project.get("ssoProtection") or {}).get("deploymentType") != "all"
        or project.get("link")
        or project.get("protectionBypass")
        or project.get("deploymentProtectionExceptions")
    ):
        raise ValueError("Project must use Vercel login for all deployments without Git auto-deploy or bypass")
    with urlopen(Request(endpoint + "/domains" + query, headers=headers), timeout=20) as response:
        domains = json.load(response)["domains"]
    if not any(row.get("name") == host and row.get("verified") for row in domains):
        raise ValueError("Production hostname is not assigned and verified for this project")
    return project_id, team_id, host


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def requires_login(url):
    try:
        with build_opener(NoRedirect).open(Request(url), timeout=15):
            return False
    except HTTPError as error:
        location = urlsplit(error.headers.get("Location", ""))
        if error.code in {302, 303, 307, 308}:
            return location.scheme == "https" and location.hostname == "vercel.com" and (
                "login" in location.path or "sso" in location.path
            )
        return error.code == 401 and b"Authentication Required" in error.read(8192)


def preflight(output):
    expected = {".vercel/output/config.json", ".vercel/output/static/index.html"}
    if {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()} != expected:
        raise ValueError("Deployment folder must contain only the report and build metadata")
    project_id, team_id, _ = assert_protected_project()
    (output / ".vercel" / "project.json").write_text(
        json.dumps({"projectId": project_id, "orgId": team_id}), encoding="utf-8"
    )


def verify(url):
    if not re.fullmatch(r"https://[a-z0-9-]+\.vercel\.app/?", url):
        raise ValueError("Vercel CLI returned an unexpected deployment URL")
    _, _, production = assert_protected_project()
    for target in (url, f"https://{production}"):
        for attempt in range(5):
            if requires_login(target):
                break
            if attempt == 4:
                raise ValueError("Deployment did not return the expected anonymous login challenge")
            time.sleep(3)
    print("Vercel login verified on the deployment and production URLs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "preflight", "verify"))
    parser.add_argument("target")
    args = parser.parse_args()
    try:
        if args.action == "verify":
            verify(args.target)
        else:
            {"stage": stage, "preflight": preflight}[args.action](Path(args.target))
    except (OSError, ValueError, KeyError, URLError) as error:
        parser.exit(1, f"Release rejected: {error}\n")
