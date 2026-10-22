# Wave 3, Session 3: the harden fix round

Three findings from the harden pass. Read
`docs/reasonix/prompts/wave-3-source-expansion-harden.md` for the invariants
and the files, then fix these three things.

Do not add a feature. Do not widen the surface.

## H1. The community file is checked against the source registry on the load path

`communities.py` has `validate_against(registry)`, and nothing calls it. A
`config/communities.yaml` naming a source the registry does not hold loads
without an error, so the documented refusal is dead code.

`load_communities` must do the check:

```python
def load_communities(
    path: str | Path | None = None, *, registry: SourceRegistry | None = None
) -> CommunityRegistry:
    """Load the community registry and refuse a community of an unknown source.

    The check is what keeps this file from introducing a source: a community
    whose source is not in the registry names something nobody reviewed.
    """
    resolved = Path(path) if path is not None else repo_root() / COMMUNITIES_NAME
    parsed = CommunityRegistry.from_yaml(resolved)
    parsed.validate_against(registry if registry is not None else load_registry())
    return parsed
```

`communities.py` already imports `SourceRegistry` from `product_hunter.sources`.
Add `load_registry` to that import. There is no cycle: `sources.py` does not
import `communities.py`.

`cli.py` loads the registry and the communities in the same command, so pass
the registry it already has: `load_communities(registry=registry)`.

Add a test to `tests/test_communities.py`: a community file naming a source
the registry does not hold is refused by `load_communities`, and the same
file is accepted when the registry holds that source.

## H2. The approved entry must carry the schemes the decision allows

`source_expansion._registry_entry` writes `"schemes": ["https"]` for every
decision. `safety.admit_url` refuses a URL whose scheme is not in
`source.schemes`, so an approval for an HTTP collector produces an admitted
source that can never be fetched.

Carry the decision's schemes in `ReviewPolicy`:

```python
schemes: list[str] = field(default_factory=lambda: ["https"])
```

The default is the safe one, HTTPS only. A reviewer who needs plain HTTP
says so, which is what the spec asks for: HTTP only where the source policy
explicitly allows it.

`_registry_entry` writes `"schemes": list(policy.schemes)`.

## H3. An approval only succeeds when the entry it produces would load

Right now a decision carrying a bad domain is accepted, written to the audit
row, and handed to the operator as text. The problem only surfaces when the
operator pastes it and `load_registry` refuses the file.

`approve_candidate` must check the entry before it returns it. Build the
entry, then validate it exactly the way the file will be validated:

```python
from product_hunter.sources import SourcesFile
...
try:
    SourcesFile(version=1, sources=[entry])
except ValidationError as exc:
    # refuse with the reason
```

The refusal carries `reason_code="invalid_entry"` and a detail that names
the first problem, taken from `exc.errors()[0]["msg"]`. The detail is
bounded; it is a validation message, not a document.

Refactor `_registry_entry` so the entry dict can be built once and used
twice: once to validate, once to serialise. A function that returns the
dictionary and a function that renders it are two small functions, not one
that returns both.

Add a test to `tests/test_source_expansion.py`: a decision carrying a
domain the registry would refuse, such as `localhost` or an IP literal, is
refused with `invalid_entry`, and nothing is written to `source_reviews`.

## After the fixes

`uv run pytest` must be green, with more tests than the 2186 it showed
before. Do not commit. Do not write the report file.
