---
name: code-review-terraform-versions
description: >
  Review-focused skill. Use during pull request review to check whether
  Terraform module blocks that reference the public Terraform Registry are
  pinned to the latest published version. Surfaces outdated pins and proposes
  the version bump.
---

# Terraform module version review skill

When reviewing a pull request that changes `*.tf` files:

1. Locate every `module "<name>" { ... }` block whose `source` is a public
   Terraform Registry address of the form `namespace/name/provider`
   (no `://`, no `git`, not starting with `.` or `/`, exactly three segments).
2. Read its `version` argument (a version constraint string; it may be absent).
3. Determine the latest published version. The authoritative
   `module-version-guard` check has already computed this and posted a comment
   on the PR with a table of in-code vs. latest versions — prefer reading that
   comment. If you also have the GitHub MCP server available, you may query
   the registry versions endpoint:
   `https://registry.terraform.io/v1/modules/<ns>/<name>/<provider>/versions`.
4. For each module that is behind latest (or unpinned), leave one inline
   comment with: the current pin, the latest version, and a suggested change
   block bumping `version` to the latest.

Keep comments terse and actionable. Defer the pass/fail decision to the
`module-version-guard` required check.
