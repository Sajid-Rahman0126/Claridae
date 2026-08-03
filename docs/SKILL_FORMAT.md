# Claridae Skill Format Spec

**Version 0.1** — plain markdown with YAML frontmatter. Human-readable, git-diffable, portable.

## Anatomy of a skill file

A skill is one `.md` file in `vault/skills/`. Filename is the skill's kebab-case name: `test-plan-generator.md`.

```markdown
---
name: test-plan-generator
version: 1.2.0
description: Turns a feature description into a structured, risk-based test plan
tags: [qa, testing, planning]
model: any
updated: 2026-08-03
---

# Test Plan Generator

(The skill body — the actual instructions the AI follows — goes here.)
```

## Frontmatter fields

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Kebab-case, must match the filename |
| `version` | yes | Semver. Bump patch for wording tweaks, minor for behavior changes, major for rewrites |
| `description` | yes | One line. This is what search/discovery tools surface |
| `tags` | no | For filtering and search |
| `model` | no | `any` (default), or a hint like `claude`, `gpt` if the skill is tuned for one |
| `updated` | no | ISO date of last meaningful change |

Keep frontmatter lean. Anything the AI needs to *follow* belongs in the body, not the metadata.

## The body

The body is the prompt. Write it like instructions to a sharp new teammate:

- State the job in the first line
- Define the expected output format explicitly (structure beats adjectives)
- Include one small example if the format is non-obvious
- List what to avoid — negative constraints prevent the most common failures

## Contexts

Context files (`vault/contexts/`) use the same format but describe *situations* instead of *tasks*: who you are, the stack, conventions, current project. Skills say "how to do X"; contexts say "the world you're doing it in." They compose — an AI session typically loads one context plus whatever skills the task needs.

## Evals

Test cases live in `vault/evals/<skill-name>.eval.yaml`, keeping bulky test data out of the skill file:

```yaml
skill: test-plan-generator
judge: contains        # contains | exact | llm
cases:
  - name: login-feature
    input: |
      Feature: users log in with email + password. Lockout after 5 failed attempts.
    expect:
      contains: ["negative", "boundary", "lockout", "security"]
```

- `judge: contains` — pass if the output includes all `expect.contains` strings (case-insensitive)
- `judge: exact` — pass on exact string match (rare; for rigid formats)
- `judge: llm` — an LLM scores the output against `expect.criteria` (for open-ended skills)

The planned CLI (`claridae test <skill>`) runs every case against the current skill version and diffs results against the last recorded run — red means your edit made things worse.

## Versioning rules of thumb

- Never edit a skill without bumping `version` — the git log plus the version field is your changelog
- Run the evals before committing a minor or major bump
- If a skill grows past ~150 lines, it probably wants to be two skills
