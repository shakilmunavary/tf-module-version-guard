# Copilot Code Review Instructions

These instructions guide GitHub Copilot when it reviews pull requests in this
repository. Keep them short and specific — Copilot code review is
non-deterministic, so the authoritative gate is the `module-version-guard`
status check, not Copilot. Copilot's job here is to explain findings in plain
language and suggest the fix.

## Terraform module versions

- Flag any `module` block whose `source` is a public Terraform Registry
  address (format `namespace/name/provider`, e.g. `terraform-aws-modules/vpc/aws`)
  that pins a `version` lower than the latest published version.
- When you flag an outdated module, state the version in the code, the latest
  available version, and propose a one-line suggested change that bumps the
  `version` to the latest.
- Flag `module` blocks that reference a public registry source but have no
  `version` argument. Recommend pinning to the latest published version.
- Treat a `~>` constraint as outdated if it cannot reach the latest published
  major/minor version (e.g. `~> 20.0` when `21.x` exists).
- Do not flag local module sources (`./`, `../`) or git sources.

## General

- Reference the "Terraform Module Version Guard" PR comment when it exists
  rather than repeating its full table.
- Be concise. One comment per affected module block.
