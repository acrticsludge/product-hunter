# Final audit 05: the local BGE-M3 embedding layer

**Probe:** Final Audit Probe #5. **Date:** 2026-10-22. **Mode:** read-only. No code was changed.

**Path note.** The brief asked for `docs/resonix/reports/final-audit-05-bge-m3.md`.
`resonix` is the spelling this tree was renamed away from. The repository's
documentation root is `docs/reasonix/` with one `o`, and `docs/reasonix/reports/`
is the only one that exists, so the report lands there. No `resonix` directory
was created.

**Target.** Branch `wave3/qdrant-semantic`, worktree
`C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave3-qdrant`, HEAD `357dd5e`,
base `00a6ca3`. The implementation landed in commit `769d260`, "wave3: semantic
retrieval layer (Qdrant) with local BGE-M3 provider". The branch is not merged
into `main`, and everything below describes that branch rather than `main`.

**Verdict.** The provider seam is real and the space guard holds. One checklist
point fails outright: a degenerate vector is accepted as a successful embedding.
Three are partial: the resolved weight revision never reaches the payload,
acquisition errors lose their cause at the CLI, and the one network-marked test
is not skipped by default the way its own marker text claims. The harden pass
added two gaps the checklist does not name: every model lifecycle event is
dropped because no sink is wired, and the cache directory is not constrained to
the repository.

**Checklist at a glance.**

| # | Checklist point | Verdict |
|---|---|---|
| 1 | BGE-M3 is the default local embedding provider | PASS |
| 2 | A provider abstraction exists | PASS |
| 3 | BGE-M3 is not hard-coded through business logic | PASS |
| 4 | Model weights can be acquired automatically on first use | PASS |
| 5 | Weights are cached and reused | PASS |
| 6 | The model is not downloaded repeatedly | PASS |
| 7 | The model is not reloaded for every document | PASS |
| 8 | Batching exists | PASS |
| 9 | CUDA is preferred when available | PASS |
| 10 | CPU fallback works | PASS |
| 11 | Device selection is configurable | PASS |
| 12 | Model / version / revision is recorded | PARTIAL |
| 13 | Vector dimensions are validated (not inside the provider) | PASS |
| 14 | Qdrant collection dimensions match the model | PASS |
| 15 | Incompatible embedding spaces cannot be silently mixed | PASS |
| 16 | Content-hash / idempotent embedding behaviour exists | PASS |
| 17 | Changing model / version triggers controlled re-embedding | PASS |
| 18 | Model download cannot be controlled by scraped content or LLM output | PASS |
| 19 | Model acquisition errors fail clearly | PARTIAL |
| 20 | Fake / zero / random vectors cannot be accepted as successful embeddings | FAIL |
| 21 | Tests do not require downloading several GB on every run | PARTIAL |
| 22 | Diagnostics expose model / cache / device / vector information | PASS |

**Tally:** 18 PASS, 3 PARTIAL, 1 FAIL across the 22 points.

The one FAIL is the soft spot in the model identity story. The space guard is
the strongest thing in this branch, and the gap the checklist found sits next
to it: the revision the weights actually resolved to never leaves the cache
path.

## 1. Implementation status

**The gate.** The audited worktree's suite passes:

```
2259 passed, 1 skipped in 40.14s      (exit 0)
```

The suite was run twice, once by the audit and once by the independent verify
node, and both runs agree on the counts and the exit code while the wall time
differs, 40.14s against 47.28s.

The single skip is the `network`-marked integration test. That skip is not a
configured default. It is what happens when the optional extra is missing, and
section 4 explains why that distinction matters.

**The dependency posture.** The ML stack is not installed in the audited
worktree's virtualenv. An import probe found `torch`, `FlagEmbedding`,
`huggingface_hub`, `sentence_transformers` and `qdrant_client` all missing.
`pyproject.toml` declares the model libraries as an optional extra:

```toml
[project.optional-dependencies]
embeddings = [
    "flagembedding>=1.3.0,<2",
    "huggingface-hub>=0.23,<1",
    "torch>=2.2",
]
```

So the branch imports, runs and tests without the stack present. The heavy
imports are all lazy and live inside functions: `providers.py:583`,
`providers.py:600` and `providers.py:619`. Importing `product_hunter.semantic`
pulls in none of them.

**What the branch gets right.** The chain `Evidence -> EmbeddingProvider ->
Local BGE-M3 -> Qdrant` exists, not as a sketch. `config/semantic.yaml:7-8`
names `bge-m3-local` and `BAAI/bge-m3`, the registry at `providers.py:536-539`
maps that name to `_build_local_bge_m3` (`providers.py:513-526`), and the CLI
builds the provider from the config at `cli.py:1513`. `ph doctor` on the
audited machine reports the same thing:

```json
"semantic": {
  "provider": "bge-m3-local", "model": "BAAI/bge-m3", "revision": "",
  "dimensions": 1024,
  "space_id": "bad3b529fc028546c4dc6a77c36b9931d6f3596be9fa2b9d94da33bdb77a625b",
  "cached": false, "cache_dir": "...\\wt-wave3-qdrant\\data\\models",
  "device": "cpu", "device_requested": "auto", "cuda_available": false,
  "qdrant_url": "http://localhost:6333", "collection": "product_hunter_evidence",
  "collection_exists": false, "vector_size": null, "collection_space_id": "",
  "space_matches": false, "qdrant_reachable": false
}
```

The provider lifecycle, point by point, with the code behind each:

| Behaviour | Where | What happens |
|---|---|---|
| Provider seam | `providers.py:48-71` | A `runtime_checkable` `Protocol` with `space`, `embed`, `diagnostics`, `release` |
| Second implementation | `providers.py:431-489` | `SentenceTransformerProvider` proves the seam is a real interface, not a single class with a rename |
| Unknown name refused | `providers.py:542-553` | `build_provider` raises `UnknownProvider` and lists the names it knows |
| First-use download | `providers.py:267-306`, `providers.py:578-594` | `snapshot_download(repo_id=..., cache_dir=..., revision=..., local_files_only=...)`, called with `local_only=False` on a cache miss |
| Cache reuse | `providers.py:308-321` | The cache is probed with `local_files_only=True` before every load; `data/` is gitignored, so weights never land in a commit |
| No repeat download | `providers.py:269-278` | A cache hit returns the path and never reaches the network branch |
| Load once | `providers.py:236-245` | The model is cached on `self._model`; every later `embed` reuses it |
| Batching | `providers.py:179-198` | Texts are chunked by `batch_size` and the size is passed to the model; `batch_size: 16` in `config/semantic.yaml:13` |
| Device preference | `providers.py:247-265` | `auto` returns `cuda` when the probe says so, `cpu` otherwise |
| CPU fallback | `providers.py:340-373` | A failed cuda load retries on the cpu and emits a `device_fallback` event naming both devices, which no caller receives (finding 30) |
| Diagnostics | `providers.py:200-222` | Provider, model, revision, cache, device, dimensions, space id, fallback, without loading the model or touching the network |

**What is missing or wrong.** One checklist point fails, three are partial, and
nine further findings fall out of the checklist. Sections 2 through 5 take them
one at a time. Section 7 lists the fixes in the order an engineer should make
them.

### 1.1 Not checked

These are the limits of the audit. Nothing below was done, and no claim in this
report rests on any of it.

- The real BGE-M3 model never ran. `flagembedding`, `torch` and
  `huggingface_hub` are not installed in the audited worktree. The provider was
  exercised through its injected downloader, model factory and
  `cuda_available` callable. The `FlagEmbedding` `encode()` contract was read
  from the code's own adapter (`providers.py:556-575`), not from a live model.
- No Qdrant server was reached. The adapter is driven through an `httpx`
  transport in the tests. The `ph doctor` run found the port closed:
  `QdrantUnavailable: GET collections/product_hunter_evidence could not be
  reached: [WinError 10061]`.
- The `ph index sync`, `rebuild`, `search` and `status` success paths were not
  run against a live Qdrant and a live model.
- The upstream snapshot's file list was not read from the model host, so
  finding 27 rests on the code's own two-file check and on the tokenizer files
  the FlagEmbedding loader requires.
- No download was performed. `cached: false` on the audited machine.
- The `ph doctor` run was made against the audited worktree with no
  `PH_DB_PATH` set.

## 2. Architecture assessment

**The index knows nothing about the model.** `index.py:24` imports only
`EmbeddingProvider` from the provider module. No file in
`src/product_hunter/semantic/` except `providers.py` names a model library, a
model name or a provider. `index.py` never sees `torch`, `huggingface_hub` or
`FlagEmbedding`.

The model reaches the provider from the config file and nothing else.
`config/semantic.yaml` names it, `EmbeddingConfig` carries it
(`config.py:163-191`), and only two functions read it: `_space_of`
(`providers.py:492-500`) and `_build_local_bge_m3` (`providers.py:513-526`).
The registry key `"bge-m3-local"` (`providers.py:537`) and the class name
`LocalBGEM3Provider` carry the name too, and that is the intended shape. A
provider named after the model it serves, behind a registry, is how you get
point 3 right without pretending the model is generic.

**The space guard is the strongest thing in the branch.** It has four layers,
and they do not overlap.

`VectorSpace` (`clustering/similarity.py:35-76`) is frozen and its model
validator refuses a `space_id` that does not match its own fields, so the id
cannot be hand-written. `space_id` (`clustering/similarity.py:79-86`) is a
sha256 over provider, model, model version and dimensions. The collection
metadata holds that id, and `ensure_ready` (`index.py:152-185`) compares the
held id and the held vector width against the configured space, raising
`EmbeddingSpaceMismatch` naming both spaces and both widths and telling the
operator to rebuild. Every search filter carries the space id
(`index.py:294-296`, `semantic/vectors.py:87`), and the member-vector scroll
filters on it too (`index.py:450`). A collection holding two spaces is not
reachable through this code.

**The width check sits one layer too high.** `embed_all`
(`semantic/embeddings.py:30-44`) compares every returned vector's length with
`embedder.space.dimensions` and raises `MixedVectorSpaceError` on a mismatch.
The index always goes through it (`index.py:291`, `index.py:377`,
`index.py:407`). The provider's own `embed` (`providers.py:179-198`) checks the
count of vectors, not their width, so a caller holding a provider directly gets
no width check. That is why point 13 passes with the caveat rather than
cleanly. A guard that the index happens to route through is a guard the next
caller can route around.

**Idempotence works, and it is content-based.** `semantic/source.py:346-348`
computes a sha256 digest of the text. Evidence uses the stored `evidence_hash`
and falls back to the digest of the quote (`source.py:177`); a pain point uses
the digest of its canonical text (`source.py:223`); a cluster uses the digest of
its sorted member ids (`source.py:257`). The payload carries `content_hash`
(`vectors.py:62`), and `index.py:240-242` skips an entity whose held payload
matches, so an unchanged entity is never re-embedded.

**Re-embedding is deliberate when it happens.** The space id is a digest of the
model identity, so a change of model, version, revision or dimensions moves
the id. `config.py:298-309` folds a pinned revision into the model version, so
a revision change also moves it. `ensure_ready` then refuses the existing
collection and names the rebuild as the answer (`index.py:169-181`), and
`rebuild` (`index.py:187-192`) drops the collection and syncs again. The
re-embedding is full and on purpose. `index_version` is in the payload
(`vectors.py:68`), so bumping it in `config/semantic.yaml` re-embeds everything
inside the same space.

**Two embedding blocks live in the repository.** `config/clustering.yaml:13-16`
names `provider: local`, `model: sentence-transformers/all-MiniLM-L6-v2`,
`dimensions: 384`. `config/semantic.yaml:7-13` names BGE-M3 at 1024. The
wave-3 report records this as assumption 4. The two waves keep separate spaces
and the space guard turns a divergence loud, so it is a note rather than a
defect. The operator risk is specific: change one file and not the other and
you get two different spaces in two subsystems, with nothing in either file
pointing at the other.

## 3. Security assessment

**The model identifier cannot be steered by content.** `providers.py:43-45`
defines `_MODEL_ID` as an `org/name` pair over `[A-Za-z0-9._-]`, and
`providers.py:114-126` refuses anything else: a second slash, a leading dot, a
dot-dot, a scheme. The check runs in `LocalBGEM3Provider.__init__`
(`providers.py:155`), before any downloader call. The model name reaches the
provider only from `EmbeddingConfig.model`, which is only ever read from the
config file. Scraped text and model output have no path to the download.

**Error messages drop the cause on purpose.** The exception types are clear and
specific, each with a `reason_code` (`providers.py:74-111`):
`ModelAcquisitionFailed`, `ModelCacheCorrupt`, `ModelLoadFailed`,
`DeviceUnavailable`, `UnknownProvider`. A missing package names the package to
install (`providers.py:583-588`, `providers.py:600-605`), and a corrupt cache
names the missing file and the cache path (`providers.py:323-338`).

What the messages do not carry is the underlying reason. `providers.py:295-298`
and `providers.py:358-373` put `type(exc).__name__` in the text and nothing
else, so a DNS failure, a 403 and a timeout all read the same. The module
docstring states the intent: a path or a token in a message would ride into a
log line. That is a deliberate trade-off, not an oversight, and the audit
accepts it.

**The guard is provider-specific where it should be a property of the
configuration.** `_checked_model_id` runs only inside
`LocalBGEM3Provider.__init__` (`providers.py:155`). The `sentence-transformers`
provider goes through `SentenceTransformerEmbedder._load`
(`clustering/similarity.py:139-150`), which hands `self._space.model` straight
to `SentenceTransformer(...)` with no identifier check. The name still comes
from the config file, so this is not a scraped-content path, but the guard
belongs on the config value rather than on one class that happens to use it.

**The cache directory is not constrained to the repository.** `_cache_dir`
(`providers.py:503-510`) returns an absolute path as it stands and expands `~`;
a relative path is joined to the root. Nothing refuses an absolute path outside
the repository, and nothing refuses a `..` segment. The shipped value is
relative and safe, `cache_dir: data/models` (`config/semantic.yaml:11`), and
`data/` is gitignored (`.gitignore:6`), so a path under `data/` never reaches a
commit. A path outside `data/` is not covered by that. The value comes from the
config file, so this is an operator-mistake vector rather than a remote one.

**The model host is not declared in `config/sources.yaml`.** That file lists
`hn.algolia.com`, `api.github.com` and an empty, disabled `blog_http`. The
BGE-M3 download goes to the Hugging Face model host, which is not there. The
adapter's forbidden rule reads "Never fetch a URL that is not declared in
config/sources.yaml; URL admission is a worker decision, not a model
decision." The provider's download is triggered by the configuration and never
by scraped content, so the intent of the rule holds. The literal wording does
not cover it, and that gap wants a decision recorded in the config or the
adapter rather than a silent reading in either direction.

## 4. Performance and lifecycle assessment

**The model is loaded once per process.** `_ensure_loaded`
(`providers.py:236-245`) caches the model on `self._model` and every later
`embed` reuses it. The index builds the provider once (`cli.py:1513`), and the
per-entity retry path (`index.py:398-416`) reuses the same loaded model rather
than rebuilding it. There is no reload per document.

**Batching has two levels.** `providers.py:179-198` chunks the texts by
`self._batch_size` and passes `batch_size` to the model, with 16 from
`config/semantic.yaml:13`. The index adds its own: `index.py:360-370` chunks
by `write_batch_size` of 64, and `index.py:372-396` embeds the chunk in one
call. Twenty-five texts arrive at the model as batches of 8, 8, 8, 1 in the
test that pins the behaviour.

**Device selection has two fallbacks, and they are not the same fallback.**
When CUDA is absent, `auto` picks `cpu` (`providers.py:260-261`). When the
device was chosen and the load then fails, `_load` (`providers.py:340-373`)
retries on the cpu, emits a `device_fallback` event and records the reason in
`self._fallback`. An explicit `cuda` request that cannot be honoured is a
refusal (`providers.py:256-259`, `providers.py:367-370`), not a quiet
downgrade, and the diagnostics report `device`, `device_requested` and
`fallback` separately so an operator can see that a fallback happened.

**The model lifecycle is unobservable.** The provider emits events around a
cache hit, a download, a load and a device fallback, and all of them go through
`_emit` (`providers.py:416-428`), which returns immediately when no sink was
passed (`providers.py:426-427`). No caller passes one. `build_provider`
(`providers.py:542-553`) takes no `events` parameter, `_build_local_bge_m3`
(`providers.py:513-526`) does not pass one to the provider it builds, and
`cli.py:346` and `cli.py:1513` call `build_provider(config, root=...)` with no
events. The only `events=` argument anywhere in `src/` is
`runtime/__main__.py:66`, for the WorkerRuntime, which is unrelated to the
provider.

So a 2.3 GB download, a load failure and a CUDA-to-CPU fallback leave no record
anywhere, and the only trace a download leaves is the cache directory on disk.

**The prune has a hard ceiling of 10,000 points.** `index.py:56` sets
`_SCROLL_LIMIT = 10_000`, and the comment above it says the adapter does not
page. `_prune` (`index.py:471-486`) scrolls once with that limit. A collection
holding more than 10,000 points has its extra points never visited, so a
removed entity beyond the ceiling is never pruned. The same limit shapes the
read side: `index.py:219-221` issues one `get_point` per entity, so a full sync
of 10,000 entities is 10,000 HTTP round trips.

**The network test is not skipped by default.** The good half first:
`tests/test_semantic_providers.py` runs on an injected downloader and an
injected model factory (`tests/test_semantic_providers.py:62-194`), so 35 tests
touch no network, no torch and no weight, and the file's own docstring says so.

The gap is the one real test, `test_the_real_bge_m3_embeds_a_sentence`
(`tests/test_semantic_providers.py:572-595`). It is marked `@pytest.mark.network`
and guarded by `pytest.importorskip("FlagEmbedding")`. The marker is declared in
`pyproject.toml:32-35`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
markers = ["network: live network/OpenCode integration test, skipped by default"]
```

There is no `-m "not network"` in `addopts` and no
`pytest_collection_modifyitems` hook in `tests/conftest.py`. The marker text
says "skipped by default"; the code does not skip it. On a machine with the
`embeddings` extra installed, `uv run pytest` downloads the whole BGE-M3
snapshot, roughly 2.3 GB, on every run, into `tmp_path`, and never cleans it.
On the audited machine the extra is missing, so the test skips and the gate is
green. That is the only reason the gate is green.

## 5. Missing functionality

### 5.1 Degenerate vectors are accepted as successful embeddings (FAIL)

This is the one point that fails outright, and it is a real gap.

`providers.py:179-198` `embed` checks the number of vectors against the number
of texts and nothing else. `semantic/embeddings.py:30-44` `embed_all` checks
the width and nothing else. Nothing checks for a zero-norm vector, a NaN or an
infinity, and nothing checks that two different texts produced two different
vectors. A model that answers with 1024 zeros, or with a constant, or with
NaNs, is accepted and written to Qdrant as a successful embedding, and
`index.py:391-396` counts it as `indexed`. A silent `cosine_similarity` of
`0.0` for a zero-norm vector (`clustering/similarity.py:165-167`) absorbs the
degenerate case instead of surfacing it, so the ranking quietly returns nothing
useful rather than failing.

The wave-3 report's hardening pass says it found "no fake vectors, no zero or
random vectors" clean (`docs/reasonix/reports/wave-3-qdrant.md`, section 5,
line 98). Read strictly, that claim is about the code not fabricating vectors,
and it is true. It is not a claim that a degenerate vector is rejected, and
nothing in the code rejects one. The gap is real.

### 5.2 No event sink is wired, so every model lifecycle event is silent (Must, finding 30)

The provider has a sink parameter that no caller can reach.
`LocalBGEM3Provider` accepts `events` (`providers.py:151`) and stores it
(`providers.py:168`), but `build_provider` (`providers.py:542-553`) takes no
`events` parameter and `_build_local_bge_m3` (`providers.py:513-526`) does not
pass one to the provider it builds. `cli.py:346` and `cli.py:1513` call
`build_provider(config, root=...)` with no events, and the only `events=`
argument anywhere in `src/` is `runtime/__main__.py:66`, for the WorkerRuntime,
which is unrelated to the provider.

All seven events are dropped: `model_cache_hit` (`providers.py:272-273`),
`model_download_started` (`providers.py:279-280`), `model_download_completed`
(`providers.py:300-301`), `device_fallback` (`providers.py:348-349`),
`model_load_started` (`providers.py:377-378`), `model_load_failed`
(`providers.py:388-389`) and `model_loaded` (`providers.py:395-396`). `_emit`
(`providers.py:416-428`) returns on `self._events is None`
(`providers.py:426-427`).

The `shortcut:` comment at `providers.py:422-424` claims "the index wires a
sink that records the events when it runs". Nothing does. The wave-3 report's
debt ledger repeats the claim (`docs/reasonix/reports/wave-3-qdrant.md`,
section 9, line 195: "The worker wires a sink").

The smallest fix is to pass a sink from the CLI into `build_provider`, or to
delete the claim from the comment and the debt ledger and say plainly that the
provider is silent without a sink.

### 5.3 The resolved revision is never recorded (PARTIAL)

Recorded: `VectorPayload.embedding_model`, `embedding_model_version` and
`space_id` (`vectors.py:63-68`); the collection metadata carries `space_id`,
`provider`, `model`, `model_version` and `dimensions` (`index.py:509-517`); the
diagnostics carry `model`, `revision` and `revision_pinned`
(`providers.py:206-222`).

Not recorded: the commit the weights actually resolved to. The Hugging Face
cache path contains the commit sha, but nothing puts it in the payload, the
collection metadata or the space id. `config/semantic.yaml` ships no
`revision`, so `snapshot_download` resolves the `main` ref on a cold cache.
`space_model_version` (`config.py:298-309`) returns `model_version` alone when
no revision is pinned. The space id therefore does not move when the upstream
weights move, which is the exact case the space id exists to catch.

### 5.4 Acquisition errors lose their cause at the CLI (PARTIAL)

The exception types are clear (section 3). The CLI is where the clarity is
lost. `index.py:378-383` catches every exception from a batch and retries the
batch one entity at a time, and `index.py:408-413` records only the class
name, in the form `f"...: {type(exc).__name__} ({type(cause).__name__})"`.

A machine without the `embeddings` extra therefore produces one failed row per
entity in the chunk, 64 per chunk, each reading
`EmbeddingUnavailable (EmbeddingUnavailable)`. The clear sentence, "the
flagembedding package is not installed; install the 'embeddings' extra", never
reaches the report. The command still exits non-zero, so the failure is not
silent, but the operator gets 64 copies of the class name instead of the one
line that tells them what to install.

### 5.5 A cluster centroid can go stale (Should, finding 23)

`source.py:257` gives a cluster the digest of its sorted member ids as its
`content_hash`. A member whose embedded text changed but whose membership did
not leaves the cluster's `content_hash` unchanged, so `index.py:240-242` skips
the cluster and `index.py:418-433` never recomputes its centroid. The member
is re-embedded, the cluster point keeps the old centroid.
`VectorPayload.same_content` (`vectors.py:82-93`) also drops `updated_at` from
the comparison, so a cluster row touched for any other reason is skipped too.
The cluster vector is a derived aggregate, so this is drift rather than
corruption, but the search ranks clusters with it.

### 5.6 The index is not wired into the pipeline (Should, finding 25)

`SemanticIndex.handle_projection_task` exists (`index.py:255-278`) and the
store enqueues a `target="qdrant"` task, but the outbox drain registers only
the obsidian handler: `cli.py:1629-1635` passes `{"obsidian": handle}` to
`process_outbox`. Nothing calls `build_provider` outside the `ph index` command
and `ph doctor`.

The wave-3 report names this as the next wave's work
(`docs/reasonix/reports/wave-3-qdrant.md`, section 11), so it is a known gap
rather than a discovery. It is listed here because the branch's own usefulness
depends on it: today the semantic index is refreshed by a human typing a
command.

### 5.7 The cache check looks at two files (Nice, finding 27)

`providers.py:152` sets `required_files=("config.json", "model.safetensors")`.
The BGE-M3 snapshot also carries the tokenizer files the model needs to load. A
cache holding only the two checked files passes the check and then fails at
load time with `ModelLoadFailed`. The failure is still clear, so this is a
Nice.

### 5.8 The cache directory is not constrained to the repository (Should, finding 31)

`_cache_dir` (`providers.py:503-510`) returns an absolute path as it stands and
expands `~`, and joins a relative path to the root. Nothing refuses an
absolute path outside the repository, and nothing refuses a `..` segment.

The shipped value is safe. `config/semantic.yaml:11` sets
`cache_dir: data/models`, which is relative and lands under the root, and
`data/` is gitignored (`.gitignore:6`), so the weights never reach a commit. A
path outside `data/` is not covered by that.

The value comes from the config file, so this is an operator-mistake vector and
not a remote one. The weights land where the operator pointed them, outside the
directory the rest of the repository assumes.

The smallest fix is to require the resolved cache directory to sit under
`<root>/data`, or to refuse an absolute path.

## 6. Exact files

The seven files that carry the implementation:

| File | Lines | Holds |
|---|---|---|
| `src/product_hunter/semantic/providers.py` | 622 | `EmbeddingProvider` protocol, `LocalBGEM3Provider`, `SentenceTransformerProvider`, the provider registry, `DenseEncoder`, the default downloader, model factory and cuda probe |
| `src/product_hunter/semantic/index.py` | 554 | `SemanticIndex`: `ensure_ready`, `sync`, `rebuild`, `search`, `status`, the write path, the outbox handler |
| `src/product_hunter/semantic/source.py` | 376 | `SemanticSource`: the authoritative reader, `Entity`, content hashes |
| `src/product_hunter/semantic/qdrant.py` | 397 | The REST adapter over `httpx` |
| `src/product_hunter/semantic/vectors.py` | 127 | `vector_id`, `VectorPayload`, `SearchFilter` |
| `src/product_hunter/semantic/embeddings.py` | 44 | `build_embedder`, `embed_all` with the width guard |
| `src/product_hunter/clustering/similarity.py` | 196 | `VectorSpace`, `space_id`, the `Embedder` protocol, `SentenceTransformerEmbedder`, `cosine_similarity`, `centroid`, `MixedVectorSpaceError`, `EmbeddingUnavailable` |

Supporting files:

| File | Holds |
|---|---|
| `src/product_hunter/config.py` | `EmbeddingConfig` at 163-191, `SemanticConfig` at 275-309, `space_model_version` |
| `config/semantic.yaml` | `provider: bge-m3-local`, `model: BAAI/bge-m3`, `model_version: "1"`, `dimensions: 1024`, `cache_dir: data/models`, `device: auto`, `batch_size: 16`, `normalize: true` |
| `config/clustering.yaml` | The second embedding block: `provider: local`, `model: sentence-transformers/all-MiniLM-L6-v2`, `dimensions: 384` |
| `src/product_hunter/cli.py` | Diagnostics at 330-396, the index command at 1487-1549, the outbox drain at 1611-1635 |
| `pyproject.toml` | The `embeddings` optional extra and the pytest marker declaration at 32-35 |
| `tests/test_semantic_providers.py` | The injected downloader and model factory, 35 offline tests, and the one network-marked test |
| `tests/test_semantic_index.py` | The space mismatch and dimension change cases |
| `tests/test_semantic_vectors.py` | Payload and filter behaviour |
| `tests/test_semantic_qdrant.py` | The REST adapter against an in-memory transport |
| `tests/test_db_store.py` | The outbox enqueue that targets qdrant |

Nothing in this repository was changed by this audit.

## 7. Required integration fixes

Ordered by what breaks first.

1. **Refuse a degenerate vector in `embed_all`
   (`semantic/embeddings.py:30-44`).** Reject a zero-norm vector, a NaN and an
   infinity, and raise `ProviderError` with the offending entity. The check
   belongs in `embed_all` because that is the only path the index uses
   (`index.py:291`, `index.py:377`, `index.py:407`). Point 20, the one FAIL.
2. **Wire an event sink into the provider, or remove the claim that one exists
   (`providers.py:151`, `providers.py:416-428`, `providers.py:513-526`,
   `providers.py:542-553`, `cli.py:346`, `cli.py:1513`).** `build_provider`
   takes no `events` parameter, so the sink the provider was written for cannot
   be supplied at all. Pass one from the CLI and record the seven lifecycle
   events, or delete the `shortcut:` comment at `providers.py:422-424` and the
   debt ledger entry in the wave-3 report and say plainly that the provider is
   silent without a sink. A silent download is the one failure an operator
   cannot reconstruct afterwards. Finding 30.
3. **Pass the acquisition reason through to the sync report
   (`index.py:408-413`).** Keep the class name in the payload and in the log
   line, but carry the provider's own `detail` into the report so the operator
   sees "install the 'embeddings' extra" once instead of 64 copies of
   `EmbeddingUnavailable`. Point 19, the CLI half.
4. **Record the resolved weight revision in the payload and the space id
   (`providers.py:267-306`, `vectors.py:63-68`, `config.py:298-309`).** Read
   the commit sha out of the cache path after the download and fold it into
   the space id, so upstream weight movement moves the space and forces the
   re-embedding that the space id exists to force. Point 12.
5. **Skip the network test by default (`pyproject.toml:32-35`,
   `tests/conftest.py`).** Add `-m "not network"` to `addopts`, or add a
   `pytest_collection_modifyitems` hook that skips the marker. Right now the
   marker text promises a behaviour the code does not implement, and a machine
   with the extra installed pays 2.3 GB per run. Point 21.
6. **Move the width guard into the provider (`providers.py:179-198`).** The
   provider checks the count and not the width, so a direct caller gets no
   check. Either check the width in `embed` as well, or make the provider the
   only way to get vectors. Point 13, the caveat.
7. **Apply `_checked_model_id` to the configuration, not to one provider
   (`clustering/similarity.py:139-150`, `providers.py:114-126`).** Validate
   `EmbeddingConfig.model` once, at config load, so every provider inherits
   the check. Finding 26.
8. **Recompute a cluster centroid when a member's vector changed
   (`source.py:257`, `index.py:240-242`, `index.py:418-433`).** Include the
   members' content hashes in the cluster's `content_hash`, or compare the
   cluster's member digests against the collection on each sync. Finding 23.
9. **Page the prune and the read path (`index.py:56`, `index.py:219-221`,
   `index.py:471-486`).** The adapter does not page and the scroll stops at
   10,000 points, so points past the ceiling are never pruned. Either page the
   scroll or raise the limit and document the ceiling. Finding 24.
10. **Register the qdrant handler in the outbox drain (`cli.py:1629-1635`).**
    `SemanticIndex.handle_projection_task` (`index.py:255-278`) is ready. The
    wave-3 report already calls this the next wave's job. Finding 25.
11. **Decide where the model host sits in the source policy
    (`config/sources.yaml`).** Either declare the Hugging Face host or record
    in the adapter that the model download is outside the scraped-URL policy.
    The rule's intent already holds, so this is a decision to write down, not
    a hole to plug. Finding 29.
12. **Check the tokenizer files in the cache probe (`providers.py:152`).** The
    two-file check passes a cache that cannot load. Finding 27, Nice.
13. **Cross-reference the two embedding blocks
    (`config/clustering.yaml:13-16`, `config/semantic.yaml:7-13`).** One
    comment in each file pointing at the other is enough. Finding 28, Note.
14. **Constrain the cache directory to the repository
    (`providers.py:503-510`, `config/semantic.yaml:11`).** Require the resolved
    cache directory to sit under `<root>/data`, or refuse an absolute path, so
    the weights cannot land outside the directory the rest of the repository
    assumes. Finding 31.

---

```
GOAL: audit the local BGE-M3 embedding implementation on wave3/qdrant-semantic
TIER: free (models used per node in order)
RISK: declared=medium floor=medium (backend, read-only audit, no code change)
ADAPTER: Product Hunter [repo] 8 forbidden rules in force
STAGES: 10/11 complete, skipped: stage 5b VISUAL (the adapter sets
  visualRequired to never and no UI file is in the radius)
GATES: 2259 passed, 1 skipped in 40.14s      (exit 0)
CHANGES: 1 file, the report only
HARDEN: the harden pass found two gaps the draft missed, the silent event
  sink (finding 30) and the unconstrained cache directory (finding 31). The
  audit changed no code. The 14 items in section 7 are open in the audited
  branch.
REVIEW: fix-then-ship. The adversarial reviewer found no verdict wrong in
  either direction, no citation pointing at the wrong code, and no claim the
  report cannot support. Its one Nice finding, that the stage-0 snap block
  padded the report, is applied: the block is gone and its one substantive
  sentence is kept in the summary above.
WAIVERS: none
STOPPED BECAUSE: the node's scope is one report file. The audited worktree and
  the main repository were read only, and the branch was not changed.
NEXT: hand section 7 to the wave that owns the semantic layer. The first two
  fixes are the degenerate-vector guard in embed_all and the acquisition
  reason in the sync report.
```

**Stage 0 snap.** One call, no retries, no errors.

```
model=subq-snap-preview  cache_key=40307bc36aca213b
calls_attempts=1  errors=0  input_tokens=533  output_tokens=0
```

`risk_class` (score over low / medium / high): score 0.15556969493627548,
probabilities low 0.856594979763031, medium 0.13124051690101624, high
0.012164589017629623, confidence 0.7666454575955868.
`backend_specialization` (noul over yes / no): noul 0.02258334867656231.
`integration_focus` (score over model identity and vector-space drift / model
acquisition and offline failure modes / index freshness and re-embedding
triggers): score 0.30578537937253714, probabilities 0.6990034580230713 /
0.2962076663970947 / 0.004788856487721205, confidence 0.5413219309411943.
