"""Server instructions sent to the client during initialize.

These reach the model automatically on every session, so they are deliberately
short: tool routing and the handful of gotchas that cause wrong calls. Anything
longer-form (workflow recipes, review conventions) belongs in a skill, not here.
"""

SERVER_INSTRUCTIONS = """\
Bitbucket Cloud API access for the configured workspace.

ROUTING - pick a tool in this order:
1. A purpose-built `bb_*` tool if one matches the task. These return trimmed,
   readable output and validate their arguments.
2. Otherwise `bb_list_endpoints` to find the endpoint, then `bb_request` to call
   it. Between them these cover all 294 Bitbucket Cloud endpoints, so a missing
   purpose-built tool is never a reason to give up or fall back to the web UI.

Never guess a path from memory - `bb_list_endpoints` returns the exact path,
path/query params and a docs link.

KEY FACTS
- `workspace` defaults to the configured workspace; usually pass only `repo_slug`.
- Paths for `bb_request` are relative to https://api.bitbucket.org/2.0 and must
  have real values substituted for {placeholders}.
- List endpoints paginate. Pass `paginate=true` to `bb_request` to follow the
  `next` links and merge every page; `pagelen` caps at 100.
- Diff, patch and pipeline-log endpoints answer in text/plain, not JSON - pass
  `raw=true` to get the body verbatim.
- Use the `fields` query param to trim large responses (e.g.
  `fields="values.id,values.title"`), which materially cuts token cost.
- An inline PR comment needs both a path and a line:
  {"content": {"raw": "..."}, "inline": {"path": "app/x.py", "to": 42}}
  `to` is a line in the new file; `from` is a line in the old one.

WRITES
Approving, commenting, merging, declining and deleting are visible to other
people and mostly irreversible. Confirm intent before calling one unless the
user has clearly already asked for that exact action.
"""
