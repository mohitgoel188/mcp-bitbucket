## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- The problem this solves. -->

## Checklist

- [ ] Offline tests pass: `python -m unittest tests.test_bb_api tests.test_auth_modes tests.test_stdio_handshake`
- [ ] `ruff check .` and `ruff format .` are clean
- [ ] Tests added for new behaviour
- [ ] README updated if a tool's surface or an environment variable changed
- [ ] No credentials, real workspace names, or internal paths in the diff

## If this adds or changes a tool

- [ ] It carries an annotation from `annotations.py` (read-only vs destructive)
- [ ] Its `description` reads as instructions to a model, not as documentation
- [ ] The response is trimmed — no raw API payload dumps
