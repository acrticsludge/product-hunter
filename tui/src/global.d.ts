import type { ExtendedIntrinsicElements } from "@opentui/react";
import { baseComponents } from "@opentui/react";

declare global {
  namespace JSX {
    interface IntrinsicElements extends ExtendedIntrinsicElements<typeof baseComponents> {}
  }
  namespace React {
    namespace JSX {
      interface IntrinsicElements extends ExtendedIntrinsicElements<typeof baseComponents> {}
    }
  }
}

export {};
