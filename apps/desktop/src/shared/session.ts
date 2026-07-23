export type SessionState =
  | "idle"
  | "starting"
  | "running"
  | "paused"
  | "stopping"
  | "error";

export function canStopSession(state: SessionState): boolean {
  return state !== "idle" && state !== "stopping";
}
