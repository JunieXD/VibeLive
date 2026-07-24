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
export type CanonicalRuntimeSpec =
  components["schemas"]["CanonicalRuntimeSpec"];
export type PersonaTemplate = components["schemas"]["PersonaTemplate"];
export type PersonaOverride = components["schemas"]["PersonaOverride"];
export type ModeDefinition = components["schemas"]["ModeDefinition"];
export type RuntimeSettings = components["schemas"]["RuntimeSettings"];
export type RuntimeSessionSnapshot =
  components["schemas"]["RuntimeSessionSnapshot"];
export type ProviderCapabilityProbeResult =
  components["schemas"]["ProviderCapabilityProbeResult"];
export type ViewerRequestTrace = components["schemas"]["ViewerRequestTrace"];
export type TraceQueryResponse = components["schemas"]["TraceQueryResponse"];
