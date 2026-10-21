---
description: Start a research run for an objective
---

Call the `product_hunter` tool with action `create` and the objective `$ARGUMENTS`.

If the objective is empty, ask the user for it first. The worker owns the
execution: this command records the request and the research id, and nothing
else. Report the text the tool returns. When the tool reports that it reused
a run because a live run already holds the objective, report that run
instead of claiming a new one was created.
