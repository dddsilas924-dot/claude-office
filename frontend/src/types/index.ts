/**
 * Types barrel — re-exports backend-derived types from generated.ts and
 * defines purely-frontend types inline.
 *
 * Consumers can continue to import from "@/types" without changes.
 *
 * DO NOT hand-edit types that originate from backend Pydantic models.
 * Run `make gen-types` to regenerate generated.ts from the backend models.
 */

// ============================================================================
// GENERATED TYPES — re-exported from ./generated (do not edit)
// ============================================================================

export type {
  // Agents
  Agent,
  AgentState,
  Boss,
  BossState,
  // Common
  BubbleContent,
  BubbleType,
  SpeechContent,
  TodoStatus,
  TodoItem,
  // Office state
  OfficeState,
  ElevatorState,
  PhoneState,
  // Sessions / whiteboard
  AgentLifespan,
  BackgroundTask,
  FileEdit,
  NewsItem,
  WhiteboardData,
  GameState,
  Session,
  ConversationEntry,
  HistoryEntry,
  // Git
  FileStatus,
  ChangedFile,
  GitStatus,
  // Events (backend models)
  EventType,
  EventData,
  Event,
} from "./generated";

// Re-export Commit with the legacy GitCommit alias for backward compatibility
export type { Commit, Commit as GitCommit } from "./generated";

// ============================================================================
// FRONTEND-ONLY TYPES — not derived from backend models
// ============================================================================

/**
 * 2D position with typed x/y coordinates.
 * The backend uses dict[str, int] (no named model), so this stays frontend-only.
 * The index signature makes this compatible with the generated Position type
 * (which is { [k: string]: number } from dict[str, int]).
 */
export interface Position {
  x: number;
  y: number;
  [k: string]: number;
}

/**
 * Whiteboard display mode index (0–10).
 * Pure frontend concept — the backend has no equivalent.
 */
export type WhiteboardMode =
  | 0 // Todo List — hotkey T
  | 1 // Remote Workers (background tasks) — hotkey B
  | 2 // Tool Pizza
  | 3 // Org Chart
  | 4 // Stonks
  | 5 // Weather
  | 6 // Safety Board
  | 7 // Timeline
  | 8 // News Ticker
  | 9 // Coffee
  | 10; // Heat Map

/**
 * Shape of the optional event detail payload carried in WebSocket events.
 * Derived from the frontend event-log rendering requirements.
 */
export interface EventDetail {
  toolName?: string;
  toolInput?: Record<string, unknown>;
  resultSummary?: string;
  message?: string;
  thinking?: string;
  errorType?: string;
  taskDescription?: string;
  agentName?: string;
  prompt?: string;
}

/**
 * WebSocket message types sent from the backend over the /ws endpoint.
 *
 * The `event` field is intentionally typed for the hook-event shape (see
 * `EventData`/`EventType` from generated.ts); when `type === "external_event"`
 * the backend reuses the same field key but the payload matches
 * {@link BridgeEventPayload} instead. Consumers handling that branch must
 * cast via `as unknown` and validate with {@link isBridgeEventPayload} — do
 * not widen this interface to a union because the hook-event typing is
 * leveraged by `EventLogEntry` in stores/gameStore.ts and narrowing would
 * ripple through unrelated call sites.
 */
export interface WebSocketMessage {
  type:
    | "state_update"
    | "event"
    | "reload"
    | "git_status"
    | "session_deleted"
    | "external_event";
  timestamp: string;
  state?: import("./generated").GameState;
  event?: {
    id: string;
    type: import("./generated").EventType;
    agentId: string;
    summary: string;
    timestamp: string;
    detail?: EventDetail;
  };
  gitStatus?: import("./generated").GitStatus;
  session_id?: string;
}

/**
 * Shape of the payload the backend carries on `external_event` WebSocket
 * frames.
 *
 * Wire producer: `backend/app/api/routes/external.py::_broadcast_external`,
 * which wraps a {@code BridgeEventPayload} pydantic model under the
 * top-level `event` key. snake_case field names are the wire contract with
 * Commander Bridge — do NOT camelCase them, the producer signs the JSON
 * bytes and the verifier relies on exact key casing.
 *
 * Fields mirror the Commander Bridge `office_client.py::_build_payload`
 * producer; optional fields are omitted (not null) on the wire, so the TS
 * shape uses `| null` for the nullable ones to avoid forcing the consumer
 * to check both `undefined` and `null` separately. The runtime validator
 * {@link isBridgeEventPayload} accepts missing optional fields and
 * normalises them away from the call site.
 */
export interface BridgeEventPayload {
  session_id: string;
  dept_id: string;
  display_name: string;
  agent_color: string;
  event_kind: string;
  message: string | null;
  model: string | null;
  duration_s: number | null;
  status: string | null;
  metadata: Record<string, unknown> | null;
}

/**
 * Runtime validator for {@link BridgeEventPayload}.
 *
 * Guards the `case "external_event"` WebSocket branch against malformed
 * payloads that would otherwise crash the renderer (e.g. missing
 * `display_name` trips the AgentSprite label, missing `agent_color` trips
 * the PixiJS fill parse). Required fields: `session_id`, `dept_id`,
 * `display_name`, `agent_color`, `event_kind`. Optional fields are
 * accepted missing, present as the correct type, or present as `null`.
 */
export function isBridgeEventPayload(obj: unknown): obj is BridgeEventPayload {
  if (typeof obj !== "object" || obj === null) return false;
  const o = obj as Record<string, unknown>;
  // Required string fields — these drive rendering and routing.
  if (typeof o.session_id !== "string") return false;
  if (typeof o.dept_id !== "string") return false;
  if (typeof o.display_name !== "string") return false;
  if (typeof o.agent_color !== "string") return false;
  if (typeof o.event_kind !== "string") return false;
  // Optional fields — permissive: missing, null, or the expected type.
  if (o.message !== undefined && o.message !== null && typeof o.message !== "string") return false;
  if (o.model !== undefined && o.model !== null && typeof o.model !== "string") return false;
  if (o.duration_s !== undefined && o.duration_s !== null && typeof o.duration_s !== "number") return false;
  if (o.status !== undefined && o.status !== null && typeof o.status !== "string") return false;
  if (o.metadata !== undefined && o.metadata !== null && (typeof o.metadata !== "object" || Array.isArray(o.metadata))) return false;
  return true;
}
