# Scripts

Automation scripts live here **alongside the prompt that generates or maintains them**.

The pattern: one folder per script, containing the script itself plus a `prompt.md` (same frontmatter format as skills) that describes how to regenerate or extend it. When the script needs a change, you don't hand-edit from memory — you feed the prompt plus the current script back to the AI, so the "source of truth" for *how the script should behave* is versioned right next to the code.

```
scripts/
  flaky-test-triage/
    triage.ts        # the script
    prompt.md        # the skill that maintains it
```

This folder starts empty on purpose — add your first real script when you migrate one from your existing QA work.
