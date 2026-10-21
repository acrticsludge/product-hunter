---
description: Continue the one eligible research run
---

Call the `product_hunter` tool with action `continue` and no research id.

If the tool reports that more than one run is eligible, use the `question`
tool to let the user pick one of the candidates, then call the tool again
with action `continue` and the research id the user picked. Never pick a run
on the user's behalf.

Report the text the tool returns.
