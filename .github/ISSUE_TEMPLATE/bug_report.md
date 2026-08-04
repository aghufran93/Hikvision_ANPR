---
name: Bug report
about: Report something that isn't working correctly
title: "[BUG] "
labels: bug
assignees: ''
---

## Describe the bug

A clear description of what's wrong.

## Which component is affected?

- [ ] Entry listener (`entry_listener.py`)
- [ ] Exit listener (`exit_listener.py`)
- [ ] Shared listener core (`anpr_common.py`)
- [ ] Dashboard (`dashboard.py` / `templates/` / `static/`)
- [ ] Documentation

## Steps to reproduce

1.
2.
3.

## Expected behavior

## Actual behavior

## Relevant log output

Paste the relevant excerpt from `logs/entry_listener.log` or
`logs/exit_listener.log`. Look for the event's `EVENT CREATED` /
`EVENT PARTIAL` / `EVENT COMPLETE` / `EVENT UPDATED` / `ORPHAN IMAGE`
lines and include timestamps.

```
paste log excerpt here
```

## Environment

- OS:
- Python version (`python3 --version`):
- Camera model/firmware (if listener-related):
- Relevant `.env` tuning values, if changed from defaults (do NOT paste
  credentials): `ANPR_EVENT_IDLE_TIMEOUT`, `ANPR_LATE_ATTACH_WINDOW`, etc.

## Additional context

Anything else relevant (screenshots of the dashboard, sample JSON from
`output/`, etc. — redact plate numbers/images if this is a public issue).
