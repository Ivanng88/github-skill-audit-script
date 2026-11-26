"""Create and verify a Vercel release containing only the reviewed catalog."""

import argparse
from hashlib import sha256
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
from export_obsidian import build_html, safe_evidence


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
PRIVATE_FILE = re.compile(r"\bmaster\.md\b|[/\\]transcripts[/\\]", re.I)
STATIC_REPORT = Path(".vercel/output/static/index.html")
BUILD_METADATA = Path(".vercel/output/config.json")
PRIVATE_ROUTES = ("/master.md", "/transcripts/", "/knowledge.md", "/data/audit.json")


def stage(output, access="protected"):
    raw = (ROOT / "data" / "audit.json").read_text(encoding="utf-8")
    packet = json.loads(raw)
    if not isinstance(packet, dict) or set(packet) != CATALOG_FIELDS or INTERNAL_URL.search(raw):
        raise ValueError("Report contains unexpected catalog fields or internal source URLs")
    if not packet["repos"] or any(
        set(repo) != REPO_FIELDS or set(repo["details"]) - DETAIL_FIELDS
        for repo in packet["repos"]
    ):
        raise ValueError("Report contains unexpected repository data")
    if access == "public":
        if PRIVATE_FILE.search(raw):
            raise ValueError("Public report contains a private file path")
        for repo in packet["repos"]:
            name = repo["repo"]
            repo_url = repo["url"]
            references = repo["details"].get("references", [])
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name)
                or not isinstance(repo_url, str)
                or repo_url.casefold() != f"https://github.com/{name.casefold()}"
                or not isinstance(references, list)
                or not all(isinstance(url, str) for url in references)
                or safe_evidence(" ".join(references), name) != references
            ):
                raise ValueError("Public report contains an unexpected repository or evidence link")
    build_html(packet)
    (output / STATIC_REPORT).parent.mkdir(parents=True, exist_ok=False)
    (output / STATIC_REPORT).write_bytes((ROOT / "index.html").read_bytes())
    (output / BUILD_METADATA).write_text('{"version":3}\n', encoding="utf-8")


def required_env(name, pattern):
    value = os.environ.get(name, "")
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Missing or invalid {name}")
    return value


def assert_project(access):
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
        or project.get("link")
        or project.get("protectionBypass")
        or project.get("deploymentProtectionExceptions")
    ):
        raise ValueError("Project identity must match without Git auto-deploy or bypass")
    if access == "protected" and (project.get("ssoProtection") or {}).get("deploymentType") != "all":
        raise ValueError("Project must use Vercel login for all deployments")
    if access == "public" and any(
        project.get(setting) for setting in ("ssoProtection", "passwordProtection", "trustedIps")
    ):
        raise ValueError("Project must allow anonymous access for public publishing")
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


def preflight(output, access="protected"):
    entries = list(output.rglob("*"))
    if any(path.is_symlink() for path in entries) or {
        path.relative_to(output) for path in entries if path.is_file()
    } != {BUILD_METADATA, STATIC_REPORT}:
        raise ValueError("Deployment folder must contain only the report and build metadata")
    if json.loads((output / BUILD_METADATA).read_text(encoding="utf-8")) != {"version": 3}:
        raise ValueError("Unexpected deployment metadata")
    project_id, team_id, _ = assert_project(access)
    (output / ".vercel" / "project.json").write_text(
        json.dumps({"projectId": project_id, "orgId": team_id}), encoding="utf-8"
    )


def public_html_matches(opener, url, expected_size, expected_digest):
    try:
        with opener.open(Request(url), timeout=15) as response:
            return (
                response.status == 200
                and response.headers.get_content_type() == "text/html"
                and sha256(response.read(expected_size + 1)).digest() == expected_digest
            )
    except URLError:
        return False


def verify_public_routes(opener, url):
    for route in PRIVATE_ROUTES:
        try:
            with opener.open(Request(url + route), timeout=15):
                pass
        except HTTPError as error:
            if error.code == 404:
                continue
        raise ValueError(f"Public deployment exposes or redirects a private file route: {route}")


def verify(url, access="protected", output=None):
    if not re.fullmatch(r"https://[a-z0-9-]+\.vercel\.app/?", url):
        raise ValueError("Vercel CLI returned an unexpected deployment URL")
    _, _, production = assert_project(access)
    if access == "public":
        if output is None:
            raise ValueError("Public verification requires the staged release path")
        report = (output / STATIC_REPORT).read_bytes()
        expected_digest = sha256(report).digest()
        opener = build_opener(NoRedirect)
    for target in (url.rstrip("/"), f"https://{production}"):
        for attempt in range(5):
            if access == "protected" and requires_login(target):
                break
            if access == "public" and public_html_matches(opener, target, len(report), expected_digest):
                verify_public_routes(opener, target)
                break
            if attempt == 4:
                raise ValueError(f"Deployment does not match the expected {access} report")
            time.sleep(3)
    print(f"Verified the {access} report on the deployment and production URLs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "preflight", "verify"))
    parser.add_argument("target")
    parser.add_argument("--access", choices=("protected", "public"), default="protected")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "verify":
            verify(args.target, args.access, args.output)
        else:
            {"stage": stage, "preflight": preflight}[args.action](Path(args.target), args.access)
    except (OSError, ValueError, KeyError, URLError) as error:
        parser.exit(1, f"Release rejected: {error}\n")
