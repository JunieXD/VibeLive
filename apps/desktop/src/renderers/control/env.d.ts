/// <reference types="vite/client" />

import type { ControlApi } from "../../shared/contracts";

declare global {
  interface Window {
    advx: ControlApi;
  }
}

export {};
