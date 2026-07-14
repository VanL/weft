# Skills

This directory contains reusable task-scoped instructions for recurring work in
this repository.

## When To Add a Skill

Add a skill when:

- the same workflow keeps recurring
- work in one area needs stable commands, checks, and failure-mode guidance
- lessons are starting to read like a repeatable operating procedure

Common candidates:

- running the project
- adding new code in a recurring subsystem
- testing
- debugging
- release or deployment

## Layout

Use one directory per skill:

- `skills/<skill-name>/SKILL.md`

Keep each skill:

- short
- operational
- task-scoped
- linked to the relevant specs or implementation docs when needed

## Maintenance

After using a skill, ask whether it should be updated while the friction points
are fresh.

If a skill becomes stale or duplicated, merge or retire it.
