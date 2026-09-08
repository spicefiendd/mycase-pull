# mycase-pull

CLI for **Legal friend** (and any Dom agent): pull one Indiana **MyCase / Odyssey** public case by cause number into `cause.json` + `cause.md`.

**Banner on every run / file:** public MyCase data only. Not certified. Not a filing. Do not e-file from this.

## Path

`/workspace/mycase-pull/mycase_pull.py`

## How to run

```bash
python3 /workspace/mycase-pull/mycase_pull.py \
  --cause 49D05-2203-CM-000001 \
  --out /workspace/mycase-pull/out
```

Offline / fixture (saved CaseSummary JSON from a prior pull):

```bash
python3 /workspace/mycase-pull/mycase_pull.py \
  --cause 49D05-2203-CM-000001 \
  --fixture /path/to/casesum.json \
  --out /workspace/mycase-pull/out
```

## Outputs (in `--out`)

- `cause.json` — structured header, parties, charges (+ dispositions from DispEvents), ledger, events
- `cause.md` — same facts, human-readable
- Also writes `{CAUSE}.json` / `{CAUSE}.md` copies

Exit codes: `0` ok; `2` bad args / network / refused `--append-packet`; process exits with `NO_HIT:` / `CAPTCHA:` messages on empty or challenged searches.

## Constraints

- Public endpoints only (`Search/SearchCases`, `Case/CaseSummary`). No login bypass.
- Never appends to an Indiana XP petition (`--append-packet` is refused).
- CaseTokens are session-scoped; re-run by cause number.

## Note on this box

Direct Python/curl TLS to `public.courts.in.gov` sometimes fails (`UNEXPECTED_EOF`). The SPA fetch from a real browser session still works; use `--fixture` with a saved CaseSummary when live TLS is down.
