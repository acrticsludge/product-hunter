---
description: List the research runs
---

Call the `product_hunter` tool with action `list` and no other argument.

Report the text the tool returns. When the user names one of the listed runs,
call the tool again with action `status` and that research id to show it, or
with action `continue` and that research id to resume it. Never pick a run on
the user's behalf.
