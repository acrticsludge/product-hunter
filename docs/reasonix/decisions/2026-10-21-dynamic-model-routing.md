# Decision note — dynamic model routing

**Date:** 2026-10-21
**Status:** accepted, implemented.
**Spec:** `docs/reasonix/specs/model-routing.md`
**Plan:** `docs/reasonix/plans/model-routing-implementation-plan.md`

Two conventions came out of this change that later phases will reuse. They are
recorded here because both are easy to break by accident.

---

## 1. Availability is derived, never reported

A catalogue entry does not carry a trust flag. `derive_availability(status,
enabled)` maps what the source reported onto one of five states, and only
`available` may be selected.

| Reported | State | Selectable |
|---|---|---|
| `status == "active"` and `enabled == true` | `available` | yes |
| `enabled == false` | `disabled` | no |
| `status` present and not `active` | `retired` | no |
| no status at all | `unknown` | no |
| known from an earlier catalogue, gone now | `missing` | no |

`unknown` is the load-bearing one. The fallback discovery surface,
`opencode models`, reports no status at all. Treating an unconfirmed model as
usable would mean handing the pipeline a model nobody checked, so an
unconfirmed model is not selectable. A source that cannot say whether a model
works contributes nothing to the routing decision.

**Do not** add a way to mark an entry available without a source that said so.

---

## 2. Roles bind by pattern, never by model id

`config/model_routing.yaml` names no model. Each of the thirteen roles owns an
ordered list of glob patterns over the live catalogue, and the router picks the
cheapest available match.

This is what makes the two required properties hold without a code change: a
model that appears is picked up at the next refresh, and a model that
disappears is skipped at the next refresh. An explicit `provider/model` binding
remains supported in the file format for an operator who wants one, but the
shipped file uses patterns only.

The corollary is that the selection is not stable across runs. A run records
the `catalog_version` it decided against, so a selection that changed is
explained rather than mysterious.

**Do not** put a model id in a source file. If the routing needs a different
model, the config is where that belongs.

---

## 3. A model id is not an identity

`ModelIdentity` is the pair `(provider_id, model_id)` and its key is
`provider/model`. The same model id on two providers is two different models:
`opencode-go/deepseek-v4.1-flash` and
`nvidia/deepseek-ai/deepseek-v4.1-flash` are not the same model and must never
share a catalogue row, an inventory row, or an execution row.

**Do not** key the catalogue, the inventory, or the execution log on the model
id alone.
