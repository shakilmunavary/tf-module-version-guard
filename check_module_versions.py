#!/usr/bin/env python3
"""
Terraform Module Version Guard
==============================

Scans Terraform files for module blocks that reference the **public**
Terraform Registry (source = "<namespace>/<name>/<provider>"), resolves the
latest published version from registry.terraform.io, and compares it against
the version constraint used in the code.

It is intentionally DETERMINISTIC: given the same code and registry state it
always produces the same verdict. That makes it safe to use as a *required*
status check that hard-blocks a PR. The GitHub Copilot layer (see
.github/copilot-instructions.md) sits on top of this for natural-language
explanations and one-click fixes.

Exit code 0  -> all references are on the latest version (check passes)
Exit code 1  -> at least one outdated reference found (check FAILS / blocks PR)
Exit code 2  -> a hard error occurred (registry unreachable, parse error, ...)

Outputs:
  - A Markdown report written to the path in $REPORT_FILE (default: report.md)
  - The same report appended to $GITHUB_STEP_SUMMARY when present
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

REGISTRY_HOST = "https://registry.terraform.io"
USER_AGENT = "tf-module-version-guard/1.0"
HTTP_TIMEOUT = 20


# --------------------------------------------------------------------------- #
# Semantic version helpers (SemVer 2.0, pre-releases excluded from "latest")
# --------------------------------------------------------------------------- #
def parse_semver(v):
    """Return (major, minor, patch, is_prerelease) or None if unparseable."""
    v = v.strip().lstrip("v")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$", v)
    if not m:
        # tolerate 2-component versions like "5.1"
        m2 = re.match(r"^(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$", v)
        if not m2:
            return None
        return (int(m2.group(1)), int(m2.group(2)), 0, bool(m2.group(3)))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), bool(m.group(4)))


def release_tuple(parsed):
    return (parsed[0], parsed[1], parsed[2])


# --------------------------------------------------------------------------- #
# Terraform version-constraint evaluation
# --------------------------------------------------------------------------- #
def _satisfies_single(version_tuple, op, target_str):
    """Evaluate one clause like '>= 1.2.0' against a version tuple."""
    target = parse_semver(target_str)
    if target is None:
        return False
    vt = version_tuple
    tt = release_tuple(target)

    if op in ("=", "==", ""):
        return vt == tt
    if op == "!=":
        return vt != tt
    if op == ">":
        return vt > tt
    if op == ">=":
        return vt >= tt
    if op == "<":
        return vt < tt
    if op == "<=":
        return vt <= tt
    if op == "~>":
        # Pessimistic operator. The number of components specified in the
        # constraint determines the ceiling.
        #   ~> 1.0.4  ->  >= 1.0.4, < 1.1.0
        #   ~> 1.0    ->  >= 1.0.0, < 2.0.0
        comps = target_str.strip().lstrip("v").split(".")
        if vt < tt:
            return False
        if len(comps) >= 3:
            # lock major+minor, allow patch
            return vt[0] == tt[0] and vt[1] == tt[1]
        # len == 2: lock major, allow minor
        return vt[0] == tt[0]
    return False


def constraint_allows(version_tuple, constraint):
    """A constraint is a comma-separated list of clauses, all of which must hold."""
    if not constraint:
        return True  # unpinned -> Terraform takes latest, so everything "allowed"
    clauses = [c.strip() for c in constraint.split(",") if c.strip()]
    for clause in clauses:
        m = re.match(r"^(~>|>=|<=|!=|==|=|>|<)?\s*v?(.+)$", clause)
        if not m:
            return False
        op = m.group(1) or "="
        target = m.group(2).strip()
        if not _satisfies_single(version_tuple, op, target):
            return False
    return True


# --------------------------------------------------------------------------- #
# Terraform parsing (dependency-free, brace-balanced module block extraction)
# --------------------------------------------------------------------------- #
MODULE_HEADER = re.compile(r'module\s+"([^"]+)"\s*\{')


def extract_module_blocks(text):
    """Yield (module_name, block_body) for each module block via brace matching."""
    for hdr in MODULE_HEADER.finditer(text):
        name = hdr.group(1)
        i = hdr.end() - 1  # position of the opening '{'
        depth = 0
        body_start = hdr.end()
        for j in range(i, len(text)):
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield name, text[body_start:j]
                    break


def find_attr(body, attr):
    m = re.search(r'(?m)^\s*%s\s*=\s*"([^"]*)"' % re.escape(attr), body)
    return m.group(1) if m else None


def is_public_registry_source(source):
    """Public registry sources are exactly <namespace>/<name>/<provider>."""
    if not source:
        return False
    if "://" in source or source.startswith((".", "/")) or source.startswith("git"):
        return False
    parts = source.split("/")
    # 3 parts == public registry; 4 parts == private registry (host/ns/name/provider)
    return len(parts) == 3 and all(parts)


# --------------------------------------------------------------------------- #
# Registry lookups
# --------------------------------------------------------------------------- #
def fetch_versions(namespace, name, provider):
    """Return sorted list of release version tuples (newest first). Raises on hard error."""
    url = f"{REGISTRY_HOST}/v1/modules/{namespace}/{name}/{provider}/versions"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    raw = data.get("modules", [{}])[0].get("versions", [])
    releases = []
    for entry in raw:
        p = parse_semver(entry.get("version", ""))
        if p and not p[3]:  # exclude pre-releases from "latest"
            releases.append(release_tuple(p))
    releases.sort(reverse=True)
    return releases


# --------------------------------------------------------------------------- #
# Core evaluation
# --------------------------------------------------------------------------- #
def fmt(t):
    return f"{t[0]}.{t[1]}.{t[2]}"


def evaluate(files, fetcher=fetch_versions):
    """Return list of finding dicts."""
    findings = []
    for path in files:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for mod_name, body in extract_module_blocks(text):
            source = find_attr(body, "source")
            version = find_attr(body, "version")  # the constraint string, may be None
            if not is_public_registry_source(source):
                continue
            namespace, name, provider = source.split("/")
            finding = {
                "file": path,
                "module": mod_name,
                "source": source,
                "constraint": version or "",
            }
            try:
                releases = fetcher(namespace, name, provider)
            except urllib.error.HTTPError as e:
                finding.update(status="ERROR", detail=f"registry HTTP {e.code}")
                findings.append(finding)
                continue
            except Exception as e:  # noqa: BLE001
                finding.update(status="ERROR", detail=f"lookup failed: {e}")
                findings.append(finding)
                continue

            if not releases:
                finding.update(status="ERROR", detail="no published versions found")
                findings.append(finding)
                continue

            latest = releases[0]
            finding["latest"] = fmt(latest)

            if not version:
                finding.update(status="UNPINNED", effective=fmt(latest),
                               detail="no version constraint; not reproducible")
                findings.append(finding)
                continue

            allowed = [r for r in releases if constraint_allows(r, version)]
            if not allowed:
                finding.update(status="ERROR", effective="-",
                               detail="constraint matches no published version")
                findings.append(finding)
                continue

            effective = allowed[0]  # highest version the constraint permits
            finding["effective"] = fmt(effective)
            if effective == latest:
                finding["status"] = "OK"
            else:
                finding["status"] = "OUTDATED"
                gap = "major" if effective[0] < latest[0] else \
                      "minor" if effective[1] < latest[1] else "patch"
                finding["detail"] = f"{gap} behind latest"
            findings.append(finding)
    return findings


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def build_report(findings):
    outdated = [f for f in findings if f["status"] == "OUTDATED"]
    errors = [f for f in findings if f["status"] == "ERROR"]
    unpinned = [f for f in findings if f["status"] == "UNPINNED"]
    ok = [f for f in findings if f["status"] == "OK"]

    icon = {"OK": "✅", "OUTDATED": "❌", "UNPINNED": "⚠️", "ERROR": "🚫"}
    lines = ["## 🛡️ Terraform Module Version Guard", ""]
    if not findings:
        lines.append("No public Terraform Registry modules found in the scanned files.")
        return "\n".join(lines)

    if outdated:
        lines.append(f"**{len(outdated)} module reference(s) are outdated.** "
                     "This check is required and will block the merge until they are bumped.")
    else:
        lines.append("All public registry module references are on the latest version. 🎉")
    lines += ["", "| Status | Module | Source | In&nbsp;code | Resolves&nbsp;to | Latest | Note |",
              "|---|---|---|---|---|---|---|"]
    order = {"OUTDATED": 0, "ERROR": 1, "UNPINNED": 2, "OK": 3}
    for f in sorted(findings, key=lambda x: order.get(x["status"], 9)):
        lines.append("| {ic} {st} | `{mod}` | `{src}` | `{con}` | `{eff}` | `{lat}` | {note} |".format(
            ic=icon.get(f["status"], ""),
            st=f["status"],
            mod=f["module"],
            src=f["source"],
            con=f["constraint"] or "—",
            eff=f.get("effective", "—"),
            lat=f.get("latest", "—"),
            note=f.get("detail", ""),
        ))
    lines += ["", f"<sub>Scanned files for {len(findings)} registry module reference(s): "
              f"{len(ok)} OK · {len(outdated)} outdated · {len(unpinned)} unpinned · "
              f"{len(errors)} error.</sub>",
              "<!-- tf-module-version-guard -->"]
    return "\n".join(lines)


def discover_tf_files(root):
    return [str(p) for p in Path(root).rglob("*.tf")]


def main():
    root = os.environ.get("SCAN_ROOT", ".")
    # Allow an explicit, newline/space separated file list (e.g. only changed files)
    explicit = os.environ.get("TF_FILES", "").split()
    files = explicit if explicit else discover_tf_files(root)
    files = [f for f in files if f.endswith(".tf") and Path(f).is_file()]

    fail_on_unpinned = os.environ.get("FAIL_ON_UNPINNED", "false").lower() == "true"

    findings = evaluate(files)
    report = build_report(findings)

    report_file = os.environ.get("REPORT_FILE", "report.md")
    Path(report_file).write_text(report, encoding="utf-8")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")

    print(report)

    has_outdated = any(f["status"] == "OUTDATED" for f in findings)
    has_error = any(f["status"] == "ERROR" for f in findings)
    has_unpinned = any(f["status"] == "UNPINNED" for f in findings)

    if has_error:
        sys.exit(2)
    if has_outdated or (fail_on_unpinned and has_unpinned):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
