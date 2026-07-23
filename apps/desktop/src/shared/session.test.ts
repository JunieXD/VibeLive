import { describe, expect, it } from "vitest";
import { canStopSession } from "./session";

describe("canStopSession", () => {
  it("keeps an error session stoppable", () => {
    expect(canStopSession("error")).toBe(true);
  });
});
