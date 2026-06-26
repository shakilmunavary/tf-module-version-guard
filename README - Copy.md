# Terraform Module Version Guard

An AI-assisted PR check that verifies every **public Terraform Registry**
module reference is pinned to the **latest published version**, posts a report
on the pull request, and **hard-blocks the merge** when something is outdated.

It is built in two layers:

| Layer | Tool | Role |
|---|---|---|
| 1. Gate (deterministic) | GitHub Action + Python | Parses `.tf`, resolves latest version from `registry.terraform.io`, fails the check on a mismatch. This is the *required* check that blocks the PR. |
| 2. Assist (AI) | GitHub Copilot code review | Explains findings in plain language and proposes one-click version bumps. |

The reason for the split: GitHub Copilot code review is non-deterministic and
[cannot itself block a merge](https://docs.github.com/copilot), so it can't be
the enforcement mechanism. The deterministic Action is the gate; Copilot is the
helpful reviewer on top.

```
.
├── .github
│   ├── workflows/terraform-module-version-check.yml   # the required check
│   ├── scripts/check_module_versions.py               # deterministic logic
│   ├── copilot-instructions.md                        # Copilot review guidance
│   └── skills/code-review/SKILL.md                     # Copilot review skill (preview)
└── environments
    ├── dev/main.tf      # one current, one outdated, one local module
    └── prod/main.tf     # a ~> constraint and an unpinned module
```

## Quick start

1. **Create the repo and push these files.**
   ```bash
   gh repo create my-org/tf-module-version-guard --private --source . --remote origin --push
   ```
   (or create the repo in the GitHub UI and `git push`).

2. **Make the check required.** Repo → **Settings → Rules → Rulesets → New branch ruleset**:
   - Target branch: `main`
   - Enable **Require status checks to pass**
   - Add the check named **`module-version-guard`**
   - Save with enforcement **Active**.

   Now no PR can merge into `main` while a module reference is outdated.

3. **Turn on the Copilot layer (optional but it's the AI part).**
   - Repo → **Settings → Copilot → Code review** → enable **Automatic code review**
     (or add "Automatically request Copilot code review" to the ruleset above).
   - `.github/copilot-instructions.md` is picked up automatically.
   - Agent skills + MCP for Copilot code review are in public preview; the skill
     under `.github/skills/code-review/` is used when relevant.
   - Requires a paid Copilot plan (Business/Enterprise for org-wide policy).

## Try the demo

```bash
git checkout -b demo
# edit environments/dev/main.tf and downgrade module "vpc" to version = "5.0.0"
git commit -am "demo: outdated vpc pin" && git push -u origin demo
gh pr create --fill
```

You'll see: the `module-version-guard` check go red, a PR comment with the
in-code vs. latest table, and (if enabled) Copilot inline comments proposing
the bump. The merge button stays blocked until you bump the version.

## How "outdated" is decided

- `source = "namespace/name/provider"` (3 segments, no scheme) → treated as a
  public registry module. Local (`./`, `../`) and git sources are ignored.
- The latest **release** version is read from
  `registry.terraform.io/v1/modules/<ns>/<name>/<provider>/versions`
  (pre-releases excluded).
- The `version` constraint is evaluated against all published versions to find
  the **highest version the constraint actually permits**. If that is below the
  latest release, it's **OUTDATED** (this is why `~> 20.0` is flagged when
  `21.x` exists). An exact pin below latest is **OUTDATED**. No `version` at all
  is **UNPINNED** (warning by default; set `FAIL_ON_UNPINNED: "true"` to block).

## Configuration (workflow env)

| Variable | Default | Meaning |
|---|---|---|
| `SCAN_ROOT` | `.` | Directory tree scanned for `*.tf` |
| `TF_FILES` | _(unset)_ | Space-separated explicit file list (e.g. only changed files); overrides scan |
| `FAIL_ON_UNPINNED` | `false` | Also block when a registry module has no `version` |
| `REPORT_FILE` | `report.md` | Where the Markdown report is written |

## Local run

```bash
SCAN_ROOT=. python3 .github/scripts/check_module_versions.py
echo "exit: $?"   # 0 = all latest, 1 = outdated found, 2 = error
```
