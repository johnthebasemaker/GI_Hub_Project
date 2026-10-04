# tools/github — repository settings kept as code

`branch_protection_main.json` is the protection on `main` (ruling Q18-1,
2026-10-04): the three jobs of `postgres-dual-ci.yml` are required and strict
(the branch must be up to date with `main`), admins included, and force-push and
deletion are blocked. Re-apply it, or apply an edited copy, with:

```bash
gh api -X PUT repos/johnthebasemaker/GI_Hub_Project/branches/main/protection --input tools/github/branch_protection_main.json
```

⚠️ Use `--input` with this file. `gh api -f strict=true` sends the STRING
`"true"`, and `-f restrictions=null` sends the string `"null"`; GitHub answers
422 *"No subschema in anyOf matched"*. That is the error the operator hit.

⚠️ A required check must run on EVERY pull request. This is why
`postgres-dual-ci.yml` has no path filter on `pull_request` (RULES.md
P18-pr-ci): a check that never starts leaves a PR "Expected — waiting" for ever.

In an emergency, Settings → Branches → `main` → untick *Do not allow bypassing
the above settings* lets an admin merge past a red check. Tick it again
afterwards.
