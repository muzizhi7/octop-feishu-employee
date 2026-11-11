# Octop Feishu Digital Employee

A Feishu/Lark interaction layer for [Octop](https://github.com/TencentCloud/Octop): native quotes and topics, task controls, confirmations, attachments, and installation that survives Octop upgrades — delivered as an installable Agent Skill.

[中文说明](README.md)

Octop's built-in Feishu channel sends and receives messages. Using it as a shared digital coworker quickly exposes the rest: answers with no topic isolation, an `@` requirement on every message, half-collapsed progress, a pause button that does not pause, attachments that will not open, and a patch that disappears after the next Octop update.

This repository adds that layer, and makes recovery part of the install.

## What changes

| Scenario | Stock Octop Feishu channel | After this Skill |
| --- | --- | --- |
| Group answers | Flat messages, mixed context | Quoted reply by default; topics only when asked |
| Topic follow-ups | Re-`@` every time | Optional no-`@` continuation inside topics the bot already joined |
| Progress | Status and body interleaved | A compact "thinking / executing" row; expand for tool activity |
| Pause | Looks paused, tools keep running | Real admission gate for later model and tool calls |
| Stop / retry | Unreliable entry points | Stop inside the progress card; retry only after failure or stop |
| Unhappy answer | "Regenerate" appears | Keep talking in the same conversation; no regenerate after success |
| Short replies | Oversized card | Native bubble that sizes to content |
| Media | Partial or silent failure | Images, files, voice, video both ways, with explicit failure messages |
| Octop upgrade | Patch overwritten | Contract check, then restore if compatible, otherwise block and report |

## Quick start

```sh
git clone https://github.com/muzizhi7/octop-feishu-employee.git ~/.codex/skills/octop-feishu-employee
cd ~/.codex/skills/octop-feishu-employee
python3 scripts/employee_patch.py doctor
```

`doctor` is read-only: it inspects real source files, interface contracts, the running process, task state and media dependencies. It never reads credentials or prints chat text. Check `core_compatible`, `install_ready` and `blockers`, then:

```sh
python3 scripts/employee_patch.py verify
# finish running tasks, then quit Octop
python3 scripts/employee_patch.py install --dry-run
python3 scripts/employee_patch.py install
python3 scripts/employee_patch.py guard-enable
# start Octop and accept it against your own bot
```

Rollback returns to the state before the most recent install:

```sh
python3 scripts/employee_patch.py rollback --dry-run
python3 scripts/employee_patch.py rollback
```

Local settings page: `python3 scripts/employee_patch.py configure` (binds `127.0.0.1:8766` only).

## Support

| Environment | Status |
| --- | --- |
| macOS Apple Silicon, Octop 1.0.2, portable layout, Python 3.12 | Full acceptance, including two-way media |
| macOS Intel | Source-level compatibility checks only |
| Other Octop / Python versions | Run `doctor`; contract changes block installation |
| Linux, Windows, containers | Detected, automatic install blocked pending adaptation |

Details: [references/environments.md](references/environments.md).

## Scope and honesty

Matched against Feishu Aily: quotes, topic workspaces, collapsed progress, confirmations, pause/continue/stop, attachment round trips, compact short messages.

Not implemented: Aily's dedicated bottom task drawer (native topics plus collapsed cards are used instead), hosted enterprise administration, and vendor model channels. No Aily internals are copied and there is no endorsement from Octop, Feishu, Lark or Aily.

Tests prove code paths, not your account. Run the acceptance checklist in [references/acceptance.md](references/acceptance.md) against your own bot before demoing to anyone.

## License

MIT, see [LICENSE](LICENSE). Several runtime patch files derive from [TencentCloud/Octop](https://github.com/TencentCloud/Octop) (MIT); full attribution in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
