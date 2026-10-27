// tui/src/index.tsx
import { createCliRenderer } from "@opentui/core";
import { createRoot } from "@opentui/react";
import React from "react";
import { App } from "./App";

const renderer = await createCliRenderer({
  exitOnCtrlC: true,
});
const root = createRoot(renderer);
root.render(React.createElement(App));
