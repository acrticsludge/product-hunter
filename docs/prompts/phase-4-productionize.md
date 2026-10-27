# Productionize prompt: Phase 4 Wave 1, research contract and tool gate foundation

**RUN_ID:** phase4-20261021-resume-01
**Node:** PRODUCTIONIZE (stage 8)
**You are:** a fresh-context implementer closing the gap between the shipped
code and the shipped documents. You write documents. You do not change
behaviour.

## The one thing this wave must deliver

The spec was written before the code and the code has moved since. The spec
is half-synced with the code. Closing that gap is the job.

The code is the tested artefact and it has been reviewed seven times. The
documents must match it. Where the two disagree, the code wins, and the
document says so.

## Read first

1. `docs/reasonix/specs/phase-4-research-contract.md` — the spec you are
   rewriting.
2. `src/product_hunter/research/` — all seven modules. The code is the source
   of truth for every behaviour you write down.
3. `config/research_contract.yaml` — the shipped baseline contract.
4. `docs/reasonix/handoffs/phase-4-research-contract-handoff.md` — the handoff,
   with the run history and the drift inventory.
5. `docs/reasonix/RESEARCH_CONTRACT.md` and `docs/reasonix/ARCHITECTURE.md` —
   the two documents that describe the package to a reader.

## What to write

### 1. `docs/reasonix/specs/phase-4-research-contract.md`

Rewrite the sections that are out of date so the spec describes the code that
exists. Read the code for every behaviour you write down. The drift
inventory in the handoff section 11.4 is the starting list, and the code has
moved further since it was written. Check each item against the code.

The changes you will need, at minimum:

- **Section 4.2** the counter-evidence field is checked at the stage level
  and at the run level, and both accept a recorded explicit zero the same way.
- **Section 4.3** the flags stay as they are. The extraction booleans stay
  absent, and the section must say why.
- **Section 4.5** the validation rules grew. The rule list must match the
  code exactly, in the code's order, and each new rule must say what it
  refuses. At minimum the list now covers the two promotion gate flags
  against the served gate keys, the reserved `promotion` gate key that no
  stage may serve, and the run-level quality check that a stage may not use.
  The limit checks refuse a limit that is not greater than zero, which also
  refuses NaN, and the soft threshold keeps its own interval.
- **Section 5.4** the two stage timestamps are timezone-aware, and a naive one
  is refused at construction.
- **Section 6.1** state the counting rule for artifacts: a metric counts an
  artifact only when the artifact is valid and traces to a successful tool
  call in the same stage, and a stage that declares no required tools holds
  artifacts that do not count toward a metric. State that the queries and the
  hosts are trimmed before they are counted.
- **Section 6.2** the two neighbour and duplicate predicates require a
  traceable artifact. A recorded zero is accepted only when every claimed
  query family is carried by a successful search call.
- **Section 7.1** `provenance_mismatch` now also covers an execution filed
  under a stage it does not belong to.
- **Section 7.2** the check order must match the code, including the new
  stage binding check, the timeout rule when the elapsed time cannot be
  computed, and the fact that the derivation check applies when the stage
  declares required tools.
- **Section 7.3** the run-level thresholds count the stages the gate actually
  evaluated and accepted.
- **Section 7.4** the promotion blockers must match the code, including the
  run id binding, the contract binding, the run-level thresholds, and the
  budget read from the run result.
- **Section 8** the budget limits must be greater than zero, and the budget
  violation names the dimension and not the scope that overran. Say plainly
  that naming the scope needs the tracker to change, and that is a later
  wave.
- **Section 9** the planned migration is `0003_research_gate.sql`, because
  `0002` is taken.
- **Section 10** the extraction booleans do not become hard requirement
  flags; they arrive with the extraction stage. The shipped contract's
  stages declare the artifact kinds their own thresholds and quality checks
  read, and that is why the shipped baseline is satisfiable.
- **Section 11** the test matrix must cover the cases the suite now covers.
- **Section 12** the acceptance criteria still hold. Say which one is proven
  where.

Remove nothing that is still true. Do not describe a behaviour the code does
not have.

### 2. `docs/reasonix/reports/phase-4-research-contract.md`

The report of this wave. It must carry:

- what the wave delivered, in plain words
- the gate evidence: the command, the pass count, and the exit code, from the
  run that is green now
- the test count for the research package on its own
- what was found and fixed, in order: the two must-fix items from the first
  security pass, the two must-fix items from the first review, the two from
  the second review, the two from the third, the crash path from the fourth,
  the two from the fifth, the two from the sixth, and the one from the
  seventh
- what was deliberately left, and why: the budget scope naming, the length
  bounds, the logging, the hand-built run result, the hand-built budget spec,
  the whitespace query predicate, the untrimmed family comparison
- the known residuals, stated plainly: no live research run has exercised
  this gate, every input is a test fixture, the budget limits are starting
  values and not measured ones, the package has no caller yet, and the
  persistence protocols have no implementation
- the debts: every `shortcut:` or `ponytail:` comment in the research
  package, with its file and line. If there are none, say there are none.

### 3. `docs/reasonix/RESEARCH_CONTRACT.md`

It still says the persistence docstring names migration `0002_research_gate.sql`.
The docstring says `0003`. Fix that, and check the rest of the document
against the code. It describes the package to a reader, so a stale sentence
is a defect.

### 4. `docs/reasonix/ARCHITECTURE.md`

It records the migration number collision as a live hazard. The collision is
now resolved in the docstring and the spec, so the hazard is closed. Update
that section, and check the paragraph that describes the research package and
the gate against the code.

### 5. `docs/reasonix/handoffs/phase-4-research-contract-handoff.md`

Append a closing section: the run is finished, the gate is green, the review
verdict is ship, and the next wave is the persistence wave that writes the
tool calls to SQLite. Name what that wave inherits.

## Rules

- Do not change any behaviour. You are writing documents, not code. If you
  find a code defect, report it in your reply and do not fix it.
- Stay inside the declared radius. The five files above, and nothing else.
- Do not touch `src/`, `config/`, or `tests/`.
- Write prose that a tired engineer can read in one pass. No filler, no
  "in today's fast-paced world", no binary contrasts, no rule-of-three
  lists, no em-dash as a connector. Say what is true and stop.
- Every claim about the code must be checkable against the code. If you
  cannot point at the line, do not write the claim.
- Do not claim a behaviour is proven by a test you have not read.

## Definition of done

Report:

- the files you wrote, with line counts
- the drift items you closed, one line each
- the drift items you did not close, and why
- the `shortcut:` or `ponytail:` comments you found, with file and line
- what you did not check
