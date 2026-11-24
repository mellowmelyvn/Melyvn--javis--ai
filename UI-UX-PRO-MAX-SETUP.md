# UI UX Pro Max — project setup

This repository is prepared for the UI UX Pro Max design skill.

## Install the full skill for Codex on Windows

Open PowerShell in this repository's folder and run:

```powershell
npm install -g ui-ux-pro-max-cli
uipro init --ai codex
```

The installer downloads the complete skill, its searchable design data, and helper scripts into the project-specific location used by Codex. Restart Codex or reopen the project after installation.

## Use it

Ask Codex to design or improve an interface and explicitly say:

> Use the UI UX Pro Max skill. First generate a design system for this product, then build the interface using its recommendations. Check responsive layouts and accessibility before finishing.

## Source

- Skill repository: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
- The commands above follow the upstream project's recommended CLI installation method.

Note: Adding this guide to GitHub does not itself install the skill on your computer. The PowerShell commands above install the complete skill into your local clone so Codex can load it.
