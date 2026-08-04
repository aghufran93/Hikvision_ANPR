# Contributing to Hikvision ANPR Monitoring System

Thanks for your interest in improving this project. It's a small, focused
codebase (two listener entry points sharing one core module, plus a Flask
dashboard) — please keep contributions in that spirit.

## Before you start

For anything beyond a trivial fix, open an issue first describing what
you'd like to change and why. This project runs live production traffic
cameras, so behavioural changes to `anpr_common.py` in particular need
discussion before implementation — a subtle regression there can mean
silently lost or misattributed vehicle events.

## Development setup

```bash
git clone https://github.com/aghufran93/Hikvision_ANPR.git
cd Hikvision_ANPR
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in test camera credentials
```

See the main [README.md](README.md) for full installation and
configuration details.

## Coding conventions observed in this repo

- **Blank line between logical statements** — the existing code favors
  one statement per "paragraph" with a blank line separator (see any
  function in `anpr_common.py` or `dashboard.py`). Match this style in
  new code rather than mixing in a denser style.
- **No comments explaining *what* code does** — identifiers are named to
  be self-explanatory. Comments in this codebase explain *why* something
  non-obvious is done (see the module docstring at the top of
  `anpr_common.py` for the standard this is held to) — keep new comments
  to that same bar, not restating the code.
- **Backward compatibility with the on-disk JSON schema is load-bearing.**
  `dashboard.py` reads whatever `anpr_common.py` writes; deployed
  historical events already exist in the old schema shape. Only add keys
  additively — never rename or remove existing top-level keys in the
  saved event JSON (`anpr`, `camera`, `images`, `capture`, `server`,
  `uuid`, `trigger`, `event_type`, `event_time`) without a migration plan.
- **No test suite currently exists.** If you add one, `pytest` is the
  natural choice given the existing dependency style, but this hasn't
  been decided — raise it in an issue first if you want to introduce a
  testing framework/CI pipeline.

## Making changes

1. Fork the repo and create a branch: `git checkout -b fix/short-description`.
2. Make your change. Run `python3 -m py_compile <file>.py` on anything you
   touch — there's no linter/CI configured yet, so this is the current bar.
3. If you touched `anpr_common.py`'s event-matching or timeout logic,
   test it against real captured data if you have any available (an XML
   sample + its JPEGs from `xml_temp/`), not just synthetic input — the
   whole design exists because synthetic/assumed camera behaviour turned
   out to be wrong in practice.
4. Commit with a clear message describing *why*, not just *what*.
5. Open a pull request against `main` describing what changed and how you
   verified it.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`. Include log
excerpts (`logs/entry_listener.log` / `logs/exit_listener.log`) where
relevant — timestamps and the `EVENT CREATED` / `EVENT PARTIAL` /
`EVENT COMPLETE` / `ORPHAN IMAGE` markers are usually the fastest way to
pin down what actually happened.

## License

This project is all-rights-reserved (see the main README) — there is no
open-source license granting reuse rights. By submitting a contribution,
you agree it may be incorporated under the same terms at the repository
owner's discretion.
