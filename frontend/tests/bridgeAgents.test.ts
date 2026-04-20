/**
 * Tests for the Commander Bridge sprite slice.
 *
 * Covers:
 *  1. `isBridgeEventPayload` runtime validator — guards the `case
 *     "external_event"` branch in `useWebSocketEvents`, so missing or
 *     wrongly-typed fields here are the failure mode that would crash the
 *     renderer.
 *  2. `bridgeAgentPosition` pure layout function — deterministic row/col
 *     math that `OfficeGame` depends on to keep sprites from stacking.
 *  3. `bridgeAgents` Zustand slice — upsert by dept_id, removal, TTL prune
 *     (with an injected `now` so the boundary is testable without mocking
 *     the global clock), and wipe.
 */

import { beforeEach, describe, expect, it } from "vitest";
import type { BridgeEventPayload } from "../src/types";
import { isBridgeEventPayload } from "../src/types";
import {
  bridgeAgentPosition,
  BRIDGE_ROW_ORIGIN,
  BRIDGE_ROW_SPACING_X,
  BRIDGE_ROW_SPACING_Y,
  BRIDGE_ROW_MAX_PER_ROW,
} from "../src/constants/positions";
import { useGameStore } from "../src/stores/gameStore";

// ─── Test helpers ──────────────────────────────────────────────────────

/** Minimal valid payload — exactly the required fields, nothing more. */
function validPayload(
  overrides: Partial<BridgeEventPayload> = {},
): BridgeEventPayload {
  return {
    session_id: "session-1",
    dept_id: "research",
    display_name: "Research Lin",
    agent_color: "#FF00FF",
    event_kind: "ASK_STARTED",
    message: null,
    model: null,
    duration_s: null,
    status: null,
    metadata: null,
    ...overrides,
  };
}

// ─── isBridgeEventPayload ─────────────────────────────────────────────

describe("isBridgeEventPayload", () => {
  it("accepts a minimal valid payload", () => {
    expect(isBridgeEventPayload(validPayload())).toBe(true);
  });

  it("accepts a full payload with every optional field populated", () => {
    expect(
      isBridgeEventPayload(
        validPayload({
          message: "hello",
          model: "haiku",
          duration_s: 1.2,
          status: "ok",
          metadata: { source_command: "/ask" },
        }),
      ),
    ).toBe(true);
  });

  it("rejects null and non-object inputs", () => {
    expect(isBridgeEventPayload(null)).toBe(false);
    expect(isBridgeEventPayload(undefined)).toBe(false);
    expect(isBridgeEventPayload("hello")).toBe(false);
    expect(isBridgeEventPayload(42)).toBe(false);
    expect(isBridgeEventPayload([])).toBe(false);
  });

  it.each([
    "session_id",
    "dept_id",
    "display_name",
    "agent_color",
    "event_kind",
  ] as const)("rejects payloads missing the required %s field", (field) => {
    const payload = validPayload() as unknown as Record<string, unknown>;
    delete payload[field];
    expect(isBridgeEventPayload(payload)).toBe(false);
  });

  it("rejects payloads with a wrongly-typed required field", () => {
    const payload = validPayload() as unknown as Record<string, unknown>;
    payload.dept_id = 42;
    expect(isBridgeEventPayload(payload)).toBe(false);
  });

  it("rejects payloads with a wrongly-typed optional field", () => {
    const payload = validPayload() as unknown as Record<string, unknown>;
    payload.duration_s = "1.2"; // string instead of number
    expect(isBridgeEventPayload(payload)).toBe(false);
  });

  it("rejects metadata arrays (arrays pass typeof object but are not records)", () => {
    const payload = validPayload() as unknown as Record<string, unknown>;
    payload.metadata = ["nope"];
    expect(isBridgeEventPayload(payload)).toBe(false);
  });
});

// ─── bridgeAgentPosition ──────────────────────────────────────────────

describe("bridgeAgentPosition", () => {
  it("places index 0 at the row origin", () => {
    expect(bridgeAgentPosition(0)).toEqual({
      x: BRIDGE_ROW_ORIGIN.x,
      y: BRIDGE_ROW_ORIGIN.y,
    });
  });

  it("advances columns horizontally by BRIDGE_ROW_SPACING_X", () => {
    expect(bridgeAgentPosition(3)).toEqual({
      x: BRIDGE_ROW_ORIGIN.x + 3 * BRIDGE_ROW_SPACING_X,
      y: BRIDGE_ROW_ORIGIN.y,
    });
  });

  it("wraps to the next row at BRIDGE_ROW_MAX_PER_ROW", () => {
    // Last slot of row 0.
    expect(bridgeAgentPosition(BRIDGE_ROW_MAX_PER_ROW - 1)).toEqual({
      x: BRIDGE_ROW_ORIGIN.x + (BRIDGE_ROW_MAX_PER_ROW - 1) * BRIDGE_ROW_SPACING_X,
      y: BRIDGE_ROW_ORIGIN.y,
    });
    // First slot of row 1 resets column, advances row.
    expect(bridgeAgentPosition(BRIDGE_ROW_MAX_PER_ROW)).toEqual({
      x: BRIDGE_ROW_ORIGIN.x,
      y: BRIDGE_ROW_ORIGIN.y + BRIDGE_ROW_SPACING_Y,
    });
    // Arbitrary index deep into a later row.
    const deepIndex = BRIDGE_ROW_MAX_PER_ROW * 2 + 4;
    expect(bridgeAgentPosition(deepIndex)).toEqual({
      x: BRIDGE_ROW_ORIGIN.x + 4 * BRIDGE_ROW_SPACING_X,
      y: BRIDGE_ROW_ORIGIN.y + 2 * BRIDGE_ROW_SPACING_Y,
    });
  });
});

// ─── bridgeAgents Zustand slice ───────────────────────────────────────

describe("bridgeAgents store slice", () => {
  beforeEach(() => {
    // Tests share a module-level store; wipe between cases so insertion
    // order doesn't leak across tests.
    useGameStore.getState().clearBridgeAgents();
  });

  it("applyBridgeEvent inserts a new bridge agent keyed by dept_id", () => {
    useGameStore.getState().applyBridgeEvent(validPayload());
    const agents = useGameStore.getState().bridgeAgents;
    expect(agents.size).toBe(1);
    const research = agents.get("research");
    expect(research).toBeDefined();
    expect(research?.displayName).toBe("Research Lin");
    expect(research?.agentColor).toBe("#FF00FF");
    expect(research?.eventKind).toBe("ASK_STARTED");
    // Default null fields land as null (not undefined) so the UI can use a
    // single `?? "—"` fallback at the render site.
    expect(research?.message).toBeNull();
    expect(research?.status).toBeNull();
  });

  it("applyBridgeEvent upserts: second event for the same dept_id mutates in place", () => {
    const store = useGameStore.getState();
    store.applyBridgeEvent(validPayload({ event_kind: "ASK_STARTED" }));
    store.applyBridgeEvent(
      validPayload({
        event_kind: "ASK_COMPLETED",
        message: "done",
        status: "ok",
        duration_s: 3.4,
      }),
    );

    const agents = useGameStore.getState().bridgeAgents;
    expect(agents.size).toBe(1);
    const research = agents.get("research");
    expect(research?.eventKind).toBe("ASK_COMPLETED");
    expect(research?.message).toBe("done");
    expect(research?.status).toBe("ok");
    expect(research?.durationS).toBe(3.4);
  });

  it("applyBridgeEvent tracks insertion order across multiple depts", () => {
    const store = useGameStore.getState();
    store.applyBridgeEvent(validPayload({ dept_id: "research" }));
    store.applyBridgeEvent(
      validPayload({
        dept_id: "security",
        display_name: "Sec Gate",
        agent_color: "#00FFAA",
      }),
    );
    store.applyBridgeEvent(
      validPayload({
        dept_id: "ai_inv",
        display_name: "AI Invest",
        agent_color: "#FFAA00",
      }),
    );

    const keys = Array.from(useGameStore.getState().bridgeAgents.keys());
    expect(keys).toEqual(["research", "security", "ai_inv"]);
  });

  it("removeBridgeAgent removes by dept_id and is a no-op for unknown depts", () => {
    const store = useGameStore.getState();
    store.applyBridgeEvent(validPayload({ dept_id: "research" }));
    store.applyBridgeEvent(validPayload({ dept_id: "security" }));

    store.removeBridgeAgent("research");
    expect(useGameStore.getState().bridgeAgents.has("research")).toBe(false);
    expect(useGameStore.getState().bridgeAgents.has("security")).toBe(true);

    // No-op path: removing a dept that was never added must not mutate
    // state (the selector subscribers would re-render for nothing).
    const before = useGameStore.getState().bridgeAgents;
    store.removeBridgeAgent("does-not-exist");
    expect(useGameStore.getState().bridgeAgents).toBe(before);
  });

  it("pruneStaleBridgeAgents drops agents older than ttlMs based on injected now", () => {
    const store = useGameStore.getState();
    // Seed two agents at different wall-clock times. We apply them
    // back-to-back so the store assigns a lastEventAt of Date.now() — the
    // prune test then injects a future `now` to exercise the boundary
    // without waiting.
    store.applyBridgeEvent(validPayload({ dept_id: "old" }));
    store.applyBridgeEvent(validPayload({ dept_id: "fresh" }));
    const agents = useGameStore.getState().bridgeAgents;
    const oldRef = agents.get("old");
    const freshRef = agents.get("fresh");
    expect(oldRef).toBeDefined();
    expect(freshRef).toBeDefined();

    // Rewrite lastEventAt on "old" so it falls outside the TTL window
    // while "fresh" stays inside. We do this by re-applying through the
    // public API after manually stamping the Map entry — keeps us on the
    // public surface.
    const patched = new Map(agents);
    patched.set("old", { ...oldRef!, lastEventAt: 1000 });
    patched.set("fresh", { ...freshRef!, lastEventAt: 9000 });
    // Splice the patched map in via the internal set — we don't expose a
    // back-door action, so fall through to useGameStore.setState which is
    // part of the Zustand public API even when no action wraps it.
    useGameStore.setState({ bridgeAgents: patched });

    // ttlMs = 5000, now = 10000 → cutoff = 5000. "old" (1000) is < cutoff
    // and must drop; "fresh" (9000) is ≥ cutoff and must stay.
    store.pruneStaleBridgeAgents(5000, 10000);

    const after = useGameStore.getState().bridgeAgents;
    expect(after.has("old")).toBe(false);
    expect(after.has("fresh")).toBe(true);
  });

  it("pruneStaleBridgeAgents is a no-op reference-wise when nothing is stale", () => {
    const store = useGameStore.getState();
    store.applyBridgeEvent(validPayload({ dept_id: "research" }));
    const before = useGameStore.getState().bridgeAgents;
    // now is well within ttl — nothing should expire.
    store.pruneStaleBridgeAgents(1_000_000);
    expect(useGameStore.getState().bridgeAgents).toBe(before);
  });

  it("clearBridgeAgents wipes all entries", () => {
    const store = useGameStore.getState();
    store.applyBridgeEvent(validPayload({ dept_id: "research" }));
    store.applyBridgeEvent(validPayload({ dept_id: "security" }));
    store.clearBridgeAgents();
    expect(useGameStore.getState().bridgeAgents.size).toBe(0);
  });

  it("clearBridgeAgents is a no-op reference-wise when already empty", () => {
    const store = useGameStore.getState();
    const before = useGameStore.getState().bridgeAgents;
    store.clearBridgeAgents();
    expect(useGameStore.getState().bridgeAgents).toBe(before);
  });
});
