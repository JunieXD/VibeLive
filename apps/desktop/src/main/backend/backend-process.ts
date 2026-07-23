import { ChildProcessWithoutNullStreams } from "node:child_process";

export interface BackendProcessController {
  readonly process: ChildProcessWithoutNullStreams | null;
  start(): Promise<void>;
  stop(): Promise<void>;
}

// The concrete launcher depends on the selected Python freezing layout.
export class UnconfiguredBackendProcess implements BackendProcessController {
  readonly process = null;

  async start(): Promise<void> {
    throw new Error("Backend process packaging is not configured yet.");
  }

  async stop(): Promise<void> {}
}
