# Claridae

**A portable, testable home for everything you've taught your AI.**

Every time you use AI, you re-explain yourself. Who you are, what project you're on, how you like things done, the prompt that worked great last month that's now lost in some chat history. That knowledge lives nowhere.

Claridae is where it lives. One vault for your skills, contexts, and automation scripts — versioned like code, portable across AI tools, and testable so you know when a change makes things worse.

## Why

- **Your prompts are assets.** They should compound over time, not evaporate with every chat.
- **Your context shouldn't be trapped.** Switch from Claude to ChatGPT to Cursor tomorrow — the vault comes with you.
- **Prompts regress like code does.** So test them like code. Save expected outcomes, tweak a skill, and find out immediately if v1.3 got worse than v1.2.

## What's in the vault

```
vault/
  skills/      Reusable prompts and instructions, versioned with YAML frontmatter
  contexts/    Who you are and what you're working on — switchable per project
  scripts/     Automation scripts stored alongside the prompts that maintain them
  evals/       Test cases for skills, so changes can be verified before you rely on them
```

Everything is plain markdown and YAML. Git is the versioning. No database, no lock-in — clone it, grep it, diff it.

See [docs/SKILL_FORMAT.md](docs/SKILL_FORMAT.md) for the file format spec.

## Quick start

1. Clone this repo (or use it as a template for your own vault)
2. Browse `vault/skills/` for example skills — they're real QA-engineering skills, not filler
3. Copy the format and start writing your own
4. Commit as you go — your git history becomes the changelog of how your AI setup evolved

## Roadmap

- [x] Vault structure + skill file format spec
- [x] Example skills, contexts, and evals
- [ ] **MCP server** — `search_skills`, `get_skill`, `get_context`, so Claude, Cursor, and any MCP-compatible tool can pull from your vault directly
- [ ] **CLI test runner** — `claridae test <skill>` runs a skill against its saved cases and shows a pass/fail diff vs. the last version
- [ ] Web UI, cloud sync, team sharing

## Status

Early and building in public. Follow along — feedback and issues welcome.

## License

MIT — see [LICENSE](LICENSE).
