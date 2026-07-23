export type { components, operations, paths } from "./generated/openapi";

import type { components } from "./generated/openapi";

export type SessionSnapshot = components["schemas"]["SessionSnapshot"];
export type RealtimeBarrageEvent = components["schemas"]["BarrageSnapshot"];
export type RealtimeClientMessage =
  components["schemas"]["ClientMessageEnvelope"];
export type RealtimeServerMessage =
  components["schemas"]["ServerMessageEnvelope"];
export type RealtimeBinaryInputHeader =
  components["schemas"]["BinaryEnvelopeHeader"];
export type RealtimeIngestAck = components["schemas"]["IngestAck"];
export type RealtimeIngestRejected = components["schemas"]["IngestRejected"];
