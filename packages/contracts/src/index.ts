export type { components, operations, paths } from "./generated/openapi";

import type { components } from "./generated/openapi";

export type SessionSnapshot = components["schemas"]["SessionSnapshot"];
export type SessionStartRequest =
  components["schemas"]["SessionStartRequest"];
export type SessionStartResponse =
  components["schemas"]["SessionStartResponse"];
export type DirectorRequest = components["schemas"]["DirectorRequest"];
export type CrowdDecision = components["schemas"]["CrowdDecision"];
export type MemeCandidate = components["schemas"]["MemeCandidate"];
export type ViewerGenerationRequest =
  components["schemas"]["ViewerGenerationRequest"];
export type ViewerGenerationResult =
  components["schemas"]["ViewerGenerationResult"];
export type RealtimeBarrageEvent = components["schemas"]["BarrageSnapshot"];
export type RealtimeClientMessage =
  components["schemas"]["ClientMessageEnvelope"];
export type RealtimeServerMessage =
  components["schemas"]["ServerMessageEnvelope"];
export type RealtimeBinaryInputHeader =
  components["schemas"]["BinaryEnvelopeHeader"];
export type RealtimeIngestAck = components["schemas"]["IngestAck"];
export type RealtimeIngestRejected = components["schemas"]["IngestRejected"];
export type RealtimePersonaMemoryRevisionCommitted =
  components["schemas"]["PersonaMemoryRevisionCommitted"];
export type RealtimeModeMemeChanged =
  components["schemas"]["ModeMemeChanged"];
