export type { components, operations, paths } from "./generated/openapi";

import type { components } from "./generated/openapi";

export type SessionSnapshot = components["schemas"]["SessionSnapshot"];
export type RealtimeClientMessage =
  components["schemas"]["ClientMessageEnvelope"];
export type RealtimeServerMessage =
  components["schemas"]["ServerMessageEnvelope"];
