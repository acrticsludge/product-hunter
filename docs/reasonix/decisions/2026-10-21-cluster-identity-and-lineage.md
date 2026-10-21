# Decision note — cluster identity and lineage

**Date:** 2026-10-21
**Status:** accepted, implemented.
**Spec:** `docs/reasonix/specs/wave-2-clustering.md`
**Plan:** `docs/reasonix/plans/wave-2-clustering.md`

Four conventions came out of the clustering wave that the scoring wave and the
independence wave will lean on. They are recorded here because each of them is
easy to break by accident, and because breaking one of them silently rewrites
what an old score meant.

---

## 1. The store owns the durable cluster id, not the engine

The engine is a pure function. It proposes a cluster with a provisional id,
`cl_` plus a digest of the anchor pain id. The store decides what the durable
id is, because the store knows what already exists: a proposal takes over the
stored active cluster it overlaps most, and an unmatched proposal becomes a new
cluster under its provisional id.

The reason is the anchor. A cluster's anchor is the member with the smallest
pain id, and the anchor can change when a member leaves. An engine that owned
the id would hand out a new id every time the anchor moved, and the scoring
wave's cluster ids would stop pointing at the same thing from one run to the
next.

**Do not** let the engine's provisional id reach a durable row. A proposal that
is adopted has to keep the stored id, and the version must not move when only
the anchor changed.

---

## 2. A merge and a split each create a new id, and the predecessor keeps its history

A merged cluster and a split parent do not survive as the same row. The merge
writes a new successor with its own id, and the split writes new children with
their own ids. The predecessors get `status = merged` or `status = split`, they
keep every row they had, and a lineage edge points at the successor.

The spec is explicit that a score attached to a cluster stays attached to the
historical cluster. A report may not reinterpret an old score as belonging to a
new cluster, and the only way to guarantee that is to give the successor a
different identity and to leave the predecessor's rows alone.

**Do not** reuse a predecessor's id for a successor, and do not rewrite a
predecessor's version rows. A version moves when the membership or the status
changes, and the old version rows stay.

---

## 3. A membership is an interval, and it is closed, never deleted

`cluster_members` rows carry `valid_from` and `valid_to`. A row with no
`valid_to` is the current membership. A membership that ends gets its
`valid_to` set, and the row is kept. Two invariants hold over the intervals: a
pain point has at most one open membership, and the intervals of one pain point
never overlap.

The first invariant is enforced twice: in the store, and by a partial unique
index on `pain_id` where `valid_to is null`. The index is the backstop. The
split path depends on it, because a split closes the parent's rows before it
opens the children's, and the order is what the index demands.

**Do not** delete a membership row, and do not open a second one for a pain
point that already has one.

---

## 4. Two vector spaces are never compared, and the refusal is on every write path

`VectorSpace` carries the provider, the model, the model version and the
dimension count, and its `space_id` is a digest of those four. Two vectors may
only be compared when they come from the same space. A comparison across spaces
raises `MixedVectorSpaceError`, and the store refuses to apply a result, run a
merge or run a split that would put two spaces in the same store.

The refusal had to be on all three paths, not only on the apply. A merge that
writes the run's space id into a successor built in another space leaves the
store holding two spaces, and the guard that was meant to stop it stops working
because the old space is still in the list.

**Do not** compare vectors across spaces, and do not add a write path that does
not check the space of the rows it touches.

---

## 5. A decision is scoped to its run

`cluster_decisions` rows carry the provider, the model, the model version, the
prompt version, the confidence and the basis. The row id is scoped to the run,
so two runs that reach the same decision about the same pair write two rows.
Without that, the second run's model call leaves no record of its own and the
decision table shows the first run's model for ever.

A decision row names the durable cluster the store allocated, and it does not
carry a foreign key on it: a decision is a record about two pain points, and it
has to stay writable when one of them has left the store.

**Do not** persist the adjudicator's free-text reason, and do not persist the
text sent to the adjudicator. A persisted detail never carries the raw text of
a quote or of a model answer. The canonical text of a pain point is a pure
function of its row in `pain_points`, so the input of a decision is
reconstructible from the two pain ids the row already names.
