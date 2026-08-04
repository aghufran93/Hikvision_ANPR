## Summary

What does this PR change, and why?

## Which component(s) does this touch?

- [ ] Entry/Exit listeners (`entry_listener.py` / `exit_listener.py`)
- [ ] Shared listener core (`anpr_common.py`) — event matching, timeouts,
      multipart/XML parsing
- [ ] Dashboard backend (`dashboard.py`)
- [ ] Dashboard frontend (`templates/`, `static/`)
- [ ] Documentation only

## How was this tested?

- [ ] `python3 -m py_compile` on every changed file
- [ ] Tested against real captured XML/JPEG data (required for any change
      to `anpr_common.py`'s parsing or `EventCache` logic — synthetic
      input alone has previously masked real firmware-behaviour bugs)
- [ ] Verified `/api/events` response shape is unchanged (or documented
      the additive change) if `dashboard.py` was touched
- [ ] Manually verified in a browser if frontend files were touched

Describe what you actually ran/checked:

## Backward compatibility

Does this change the on-disk JSON schema, the `/api/events` response
shape, or any filename convention under `output/`? If yes, explain the
migration path for already-saved historical events.

## Checklist

- [ ] I've read [`CONTRIBUTING.md`](../CONTRIBUTING.md)
- [ ] No secrets (camera credentials, `.env` contents) are included in
      this diff
