/// <reference types="vite/client" />

import type { OverlayApi } from "../../shared/contracts";

declare global {
  interface Window {
    advxOverlay: OverlayApi;
  }
}

export {};
