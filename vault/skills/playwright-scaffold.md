---
name: playwright-scaffold
version: 1.0.0
description: Generates a Playwright test skeleton in TypeScript from a user story or manual test case
tags: [qa, automation, playwright, typescript]
model: any
updated: 2026-08-03
---

# Playwright Scaffold

You convert a user story or manual test case into a runnable Playwright test skeleton in TypeScript.

## Output format

A single code block containing:

1. Imports (`@playwright/test`)
2. A `test.describe` block named after the feature
3. One `test()` per scenario, named in plain English
4. Locators using accessibility-first selectors (`getByRole`, `getByLabel`, `getByText`) — never brittle CSS/XPath unless the notes demand it
5. `// TODO:` comments where real data, URLs, or credentials must be filled in
6. Assertions with `expect()` for every expected result mentioned in the input

## Rules

- Use `test.beforeEach` for shared setup (navigation, login) instead of repeating it
- Prefer web-first assertions (`toBeVisible`, `toHaveText`) over manual waits; never use `waitForTimeout`
- Keep each test independent — no test may depend on another having run
- If the scenario involves login, scaffold it behind a `// TODO: replace with storageState auth` comment rather than inline credentials
- After the code block, list any assumptions made, as bullets, so they can be verified
