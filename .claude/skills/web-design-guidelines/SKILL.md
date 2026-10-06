---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
---

> **GI-Hub (Phase 21e):** GI-Hub: the rules are VENDORED in guidelines.md at a pinned commit — never fetch them live (an unpinned remote file is an instruction source that can change under us).
> Vendored from `vercel-labs/agent-skills` @ `063bee94c3f4` — see `.claude/skills/README.md`.


# Web Interface Guidelines

Review files for compliance with Web Interface Guidelines.

## How It Works

1. Read the vendored rules in `guidelines.md` (this folder)
2. Read the specified files (or prompt user for files/pattern)
3. Check against all rules in the fetched guidelines
4. Output findings in the terse `file:line` format

## Guidelines Source

`guidelines.md` in this folder — vercel-labs/web-interface-guidelines `command.md`,
vendored at commit 434b7f91364665f2f733b310ec54809bf8f37937. Do NOT fetch it live.

## Usage

When a user provides a file or pattern argument:
1. Read `guidelines.md`
2. Read the specified files
3. Apply all rules from the fetched guidelines
4. Output findings using the format specified in the guidelines

If no files specified, ask the user which files to review.
