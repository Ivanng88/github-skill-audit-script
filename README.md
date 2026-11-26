# GitHub Skill Audit scripts

Build a browsable repository catalog from Markdown notes exported from Obsidian. The Python tools use only the standard library. The example notes are synthetic.

## Quick start

Run these commands from the checkout with Python 3.9 or later.

```sh
python3 export_obsidian.py --knowledge-root examples/atlas --atlas-date 2000-01-01
python3 audit.py check
python3 audit.py find example --json
```

Open the generated `index.html` in a browser. The exporter also writes `data/audit.json` and `knowledge.md`. `python3 audit.py render` rebuilds both views from the JSON.

## Use your own notes

Point `--knowledge-root` to a directory containing `Repository Atlas` and `Continuous Updates`. Pass the date from the Atlas filenames with `--atlas-date`. The [example notes](examples/atlas) show the required links, category table and review format. Use `--legacy-tag` to include an optional older note named `IT Atlas <category> <legacy-tag>.md`.

Review generated output before sharing it. The exporter filters some sensitive values, but your notes can still contain confidential information. Generated outputs are ignored by Git.

If CodeGraph is already installed, use `python3 audit.py codegraph --help` for the optional local index commands.

## GitHub Actions

Pull requests and pushes to `main` validate the synthetic catalog. A private
repository may call `.github/workflows/protected-report.yml` at a pinned commit
to validate its own `index.html`, `data/audit.json`, and `knowledge.md`.
Provide that same commit as `scripts_sha`. Leave `deploy` off to validate only.

Set `deploy` and supply the Vercel project ID, team ID, production hostname,
and an explicitly mapped Vercel secret from the private caller to release.
The default `access: protected` requires Vercel Authentication for **All
Deployments** and checks that both Vercel URLs challenge anonymous requests.
Explicit `access: public` allows a release from `codex/github-skill-audit` to a
Vercel project open to anonymous visitors. The workflow compares the deployed
HTML with the staged HTML and requires HTTP 404 on private file routes at both
Vercel URLs. Only the rendered HTML is staged for either release mode.
