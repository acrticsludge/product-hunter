# Harden prompt: research run observation

**RUN_ID:** robs-20261021-ea2759ad
**Node:** HARDEN (stage 6)
**You are:** the security and observability pass. You report gaps. You do not edit.

## The surface under review

This run changed no product code. It made two read calls through the `product_hunter` tool and wrote documentation under `docs/reasonix/`. The read path it touched is:

1. `.opencode/plugins/product-hunter.ts`, the plugin that spawns the registry
2. `src/product_hunter/registry.py`, the state layer with `ACTIONS` and `execute()`
3. `src/product_hunter/db/connection.py`, how the SQLite file is opened
4. `db/product_hunter.db`, table `research_runs`, the durable state that was read

The two calls were `action=list` and `action=status` with `research_id=RH-20261021-0003`.

## What to review

Work the trust boundary, the data exposure, and the observability of exactly that path.

1. **Trust boundary.** The plugin spawns a subprocess. Check how the action name and the `research_id` reach that subprocess and whether either can be influenced into a command, a path, or an argument injection. The registry already has a `TargetRequest` pattern check; confirm it actually constrains what it claims to.
2. **Data exposure.** The `list` and `status` outputs were printed into a chat reply. Say what those outputs can contain: objective text, error text, timestamps, internal ids, file paths, anything that looks like a credential or a secret. Check whether an objective or `last_error` can carry attacker-influenced text into a rendered line, and whether rendering escapes it.
3. **Database access.** The registry opens the database without the `read_only` option. Assess what a read-only call can therefore still do, and whether the difference matters for these two actions. This is a hardening note, not a code change request.
4. **Error handling.** Confirm that a bad `research_id`, a missing database, or a corrupt row produces a structured error and a clean exit code, not a bare traceback.
5. **Observability.** Say what an operator can see about a read of this kind: are these calls logged anywhere, is there an event trail, would an operator know a run was queried. If nothing is logged, that is a finding with a severity.

## Rules

- You report. You do not edit, not even in the documentation.
- Stay on the path above. A whole-repo audit is stage 8's job, not yours. If you notice something elsewhere, put it in a clearly separated "outside this run" list.
- Another agent is editing `src/`, `tests/`, `config/`, `eval/`, and `src/product_hunter/research/` live. Judge the code as it is when you read it, and say if something changed under you.
- Severity must mean something. Reserve critical for a real exploit path. Put anything that does not apply in `NOT_APPLICABLE` with a reason.
- Do not run the test suite. Do not start a worker. Do not call the `product_hunter` tool.

## What to report

- A verdict line: one of `HARDEN PASS`, `HARDEN PASS WITH NOTES`, or `HARDEN FINDINGS`.
- Each finding: severity, file:line, what an attacker or operator would do with it, and the smallest fix you would recommend.
- The `NOT_APPLICABLE` list with reasons.
- Anything you could not verify.

---

# Re-harden: attribution and radius

The first pass returned four findings (unguarded `connect()` before the try guard, no audit trail for reads, read actions opening SQLite read-write, `objective` rendered without escaping). Re-harden at the same rung to settle three questions.

1. **Attribution.** For each of the four findings, say whether it is a pre-existing condition in the repository or something this run introduced. This run wrote no file under `src/`, `tests/`, `config/`, or `db/`; it added eight documentation files under `docs/reasonix/` and made two read calls. Use `git log` and `git diff` on the named files to check whether the code carrying the finding is older than this run's session.
2. **Radius.** The declared radius for this run is those eight documentation files. Confirm that none of the four findings sits inside it. Then state, plainly, whether fixing any of them belongs in this run or in a separate one.
3. **Documentation safety.** This run's docs now describe the read path, including that `list` and `status` are read-only actions. Read `docs/reasonix/specs/research-run-observation.md` and `docs/reasonix/handoffs/research-run-observation-handoff.md` and check that neither doc overstates the safety of the read path, in particular that neither implies the actions are guaranteed not to write, that the database is opened read-only, or that reads are audited. Quote any sentence that does.

The same rules apply: you report, you do not edit, you stay off the test suite, and you do not call the `product_hunter` tool.

Report a verdict line for this re-harden, the per-finding attribution, the radius answer, and the documentation check with quoted sentences.
