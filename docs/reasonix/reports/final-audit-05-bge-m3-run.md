# Final Audit Probe #5: run report

**Probe:** Final Audit Probe #5. **Date:** 2026-10-22. **Mode:** read-only. No code was changed.

**Audit artifact:** `C:\Anubhav\Web Dev Projects\product-hunter\docs\reasonix\reports\final-audit-05-bge-m3.md`, committed as `a22d18d` (626 lines). This run report summarises it.

**Target.** Branch `wave3/qdrant-semantic`, worktree `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave3-qdrant`, HEAD `357dd5e`, implementation commit `769d260`. The branch is not merged into `main`. The brief asked for `docs/resonix/reports/`, a misspelling; the repository's docs root is `docs/reasonix/`, and that is where the report landed.

## 1. What the run did

Final Audit Probe #5 read the local BGE-M3 embedding implementation on the unmerged branch `wave3/qdrant-semantic` and wrote a 626-line report against 22 checklist points. It ran the audited worktree's suite twice and the main repository's suite once, and folded two gaps from the harden pass into the final verdict. No code was changed anywhere, in the audited worktree or in `main`.

## 2. The verdict

18 PASS, 3 PARTIAL, 1 FAIL across 22 checklist points.

The single FAIL: a degenerate vector, zero, NaN or constant, is accepted as a successful embedding. The provider's `embed` checks the vector count and `embed_all` checks the width, but neither rejects a degenerate vector, so a model returning 1024 zeros is written to Qdrant and counted as indexed.

The three PARTIALs: the resolved weight revision never reaches the payload, the model version and revision are recorded but the commit sha the weights resolved to is not, so upstream weight movement does not move the space id. Acquisition errors lose their cause at the CLI, where each failed entity records only the class name instead of the provider's own message. The network-marked test is not skipped by default, despite its marker text claiming it is, so a machine with the embeddings extra installed downloads the full BGE-M3 snapshot on every run.

The space guard is the strongest part of the branch: four layers, none overlapping, none bypassable through the index.

## 3. The gates, verbatim

```
audited worktree: 2259 passed, 1 skipped in 40.14s      (exit 0)
second run:       2259 passed, 1 skipped in 47.28s      (exit 0)
main repository:  2138 passed in 44.16s                (exit 0)
```

The single skip is the `network`-marked integration test. It skips because the optional `embeddings` extra is missing on the audited machine, not because of a configured default.

## 4. The five findings that matter most

1. **Degenerate vectors accepted.** The provider's `embed` checks the vector count and `embed_all` checks the width, but nothing rejects a zero-norm, NaN, infinity or constant vector. A model answering with 1024 zeros is accepted and written to Qdrant as a successful embedding, in `providers.py:179-198` and `semantic/embeddings.py:30-44`.

2. **No event sink wired.** The provider emits seven lifecycle events through `_emit`, which returns immediately when no sink was passed, and no caller passes one. `build_provider` takes no `events` parameter, so a download, a load failure and a device fallback leave no record, and the `shortcut:` comment at `providers.py:422` claiming the index wires a sink is wrong.

3. **Cache directory unconstrained.** `_cache_dir` returns an absolute path as it stands and expands `~`, and nothing refuses a `..` segment. Weights can land outside the repository, in `providers.py:503-510`; the shipped value is relative and safe, but the guard is missing.

4. **Resolved revision not recorded.** Hugging Face resolves a commit sha into the cache path, but nothing reads it into the payload, collection metadata or space id. With no revision pinned in `config/semantic.yaml`, the space id does not move when upstream weights move, in `providers.py:267-306` and `config.py:298-309`.

5. **Acquisition reason lost.** The CLI catches every exception from a batch and records only the class name, so a machine without the `embeddings` extra produces 64 identical rows reading `EmbeddingUnavailable`. The provider's own clear message never reaches the sync report, in `index.py:378-413`.

## 5. What was not checked

- The real BGE-M3 model never ran. `torch`, `FlagEmbedding`, `huggingface_hub`, `sentence_transformers` and `qdrant_client` are not installed in the audited worktree. The provider was exercised through its injected downloader, model factory and `cuda_available` callable. The `FlagEmbedding` `encode()` contract was read from the code's own adapter, not from a live model.
- No Qdrant server was reached. The adapter is driven through an `httpx` transport in the tests. `ph doctor` found the port closed.
- The `ph index sync`, `rebuild`, `search` and `status` success paths were not run against a live Qdrant and a live model.
- The upstream snapshot's file list was not read from the model host, so the cache probe finding rests on the code's own two-file check and the tokenizer files the loader requires.
- No download was performed. `cached: false` on the audited machine.
- The `ph doctor` run was made against the audited worktree with no `PH_DB_PATH` set.

## 6. Summary block

```
GOAL: audit the local BGE-M3 embedding implementation on wave3/qdrant-semantic
TIER: free. Models per node, in order: implement (longcat-2.5-preview-free), verify-code (longcat-2.5-preview-free), harden (step-5-preview-free), implement again (longcat-2.5-preview-free), review (step-5-preview-free). The review node was dispatched three times: two HTTP 429 rate limits on rung 1, two "model unavailable" responses on rung 2, then a success on rung 1 whose first reply was truncated by the output limit, followed by a compact verdict from the same session.
RISK: declared=medium floor=medium. Backend specialization. Read-only audit, no schema, no secret, no payment.
ADAPTER: Product Hunter [repo], 8 forbidden rules in force.
STAGES: 11/11 complete, skipped: stage 5b VISUAL only. The adapter sets visualRequired to never and no UI file is in the radius.
GATES: audited worktree `2259 passed, 1 skipped in 40.14s      (exit 0)`, run twice with a second time of 47.28s and the same counts and exit code. Main repository `2138 passed in 44.16s` (exit 0).
CHANGES: 1 file, 626 insertions, the report only, committed as a22d18d.
HARDEN: two findings the draft report missed, both folded in. Must: no events sink is wired in production, so model downloads, load failures and device fallbacks are silent, and the `shortcut:` comment at providers.py:422 claiming the index wires a sink is wrong. Should: cache_dir accepts absolute paths and .. segments outside the intended root. The audit changed no code.
REVIEW: fix-then-ship. The adversarial reviewer found no verdict wrong in either direction, no citation pointing at the wrong code, and no claim the report cannot support. Its one Nice finding, that the stage-0 snap block padded the report, is applied.
WAIVERS: none
STOPPED BECAUSE: all gates pass and the review's one finding is applied
NEXT: hand section 7's fourteen integration fixes to the wave that owns the semantic layer. The first two are the degenerate-vector guard in embed_all and the acquisition reason in the sync report.
```
