# Project skills — UI (Phase 21e, ruling Q21-22)

Installed by request of the operator from the cheat sheet *9 Best UI Skills for
Claude Code*. **Vendored and pinned**, each file read before it was added: a
skill is a set of instructions an agent follows, so it is reviewed like code
and never updated by fetching.

| Skill | From | Commit | Changed here |
|---|---|---|---|
| frontend-design | anthropics/skills | `683bc88e56f3` | GI-Hub note (direction already chosen) |
| webapp-testing | anthropics/skills | `683bc88e56f3` | `scripts/with_server.py` REMOVED (servers only via the preview tools / E2E harness) |
| animate · review-animations · find-animation-opportunities · emil-design-eng | emilkowalski/skills | `e8a175de22ae` | GI-Hub note (CSS-only on the critical path) |
| web-design-guidelines | vercel-labs/agent-skills | `063bee94c3f4` | rules VENDORED as `guidelines.md` (web-interface-guidelines `434b7f913646`); no live fetch |

The contract they serve is `docs/DESIGN_SYSTEM.md`; where a skill and the
contract differ, the contract wins. The other skills in this folder (video /
HyperFrames) are local installs and stay git-ignored.

To update one: fetch the new commit into a scratch folder, READ the diff, then
copy it here and change the commit in this table — in a PR.
