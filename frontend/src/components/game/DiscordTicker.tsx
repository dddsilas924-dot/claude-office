/**
 * DiscordTicker
 *
 * Canvas-bottom scrolling ticker that displays the last N Commander Bridge
 * messages received via WebSocket `external_event` frames.
 *
 * Rendered as a native HTML overlay (absolute-positioned div) on top of the
 * PixiJS canvas so we can use CSS animations and standard DOM text rendering
 * rather than fighting Pixi for a marquee implementation.
 *
 * Layout:
 *   [dept-emoji] displayName: message (≤40 chars)
 *
 * Newest message slides in from the right (or fades in on first appearance).
 * Up to 5 entries are shown simultaneously; older entries shift left.
 */

"use client";

import { useMemo, useEffect, useState, type ReactNode } from "react";
import { useGameStore, selectRecentBridgeMessages } from "@/stores/gameStore";
import { useShallow } from "zustand/react/shallow";
import type { BridgeMessageEntry } from "@/stores/gameStore";

// ============================================================================
// CONSTANTS
// ============================================================================

const MAX_VISIBLE = 5;
const TICKER_HEIGHT = 32; // px
const FADE_DURATION_MS = 400;

// event_kind → display colour for left-border accent
const KIND_COLOR: Record<string, string> = {
  ASK_STARTED: "#3498db",
  ASK_COMPLETED: "#2ecc71",
  STATUS_UPDATE: "#e67e22",
  HEARTBEAT: "#95a5a6",
};

// ============================================================================
// HELPERS
// ============================================================================

function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen - 1) + "…";
}

function labelFor(entry: BridgeMessageEntry): string {
  // Strip leading emoji from displayName (already split into deptEmoji)
  const LEADING_EMOJI_RE =
    /^(\p{Extended_Pictographic}(?:\u200D\p{Extended_Pictographic})*\uFE0F?)\s*/u;
  const nameWithoutEmoji = entry.displayName.replace(LEADING_EMOJI_RE, "");
  // Compact: "部門名 キャラ名" → take last word only if Japanese (≥4 chars)
  const parts = nameWithoutEmoji.trim().split(/\s+/);
  const shortName = parts.length > 1 ? parts[parts.length - 1] : parts[0];
  return shortName ?? entry.deptId;
}

// ============================================================================
// TICKER ENTRY COMPONENT
// ============================================================================

interface TickerEntryProps {
  entry: BridgeMessageEntry;
  /** True when this entry just arrived (triggers fade-in). */
  isNew: boolean;
}

function TickerEntry({ entry, isNew }: TickerEntryProps): ReactNode {
  const [visible, setVisible] = useState(!isNew);

  useEffect(() => {
    if (!isNew) return;
    // requestAnimationFrame to ensure the element is in the DOM before fading
    const raf = requestAnimationFrame(() => {
      setTimeout(() => setVisible(true), 16);
    });
    return () => cancelAnimationFrame(raf);
  }, [isNew]);

  const accentColor = KIND_COLOR[entry.eventKind] ?? "#7f8c8d";
  const name = labelFor(entry);
  const rawText = entry.text ?? `[${entry.eventKind}]`;
  const displayText = truncate(rawText, 40);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "6px",
        padding: "0 10px 0 6px",
        height: `${TICKER_HEIGHT}px`,
        borderLeft: `3px solid ${accentColor}`,
        marginLeft: "8px",
        flexShrink: 0,
        opacity: visible ? 1 : 0,
        transition: `opacity ${FADE_DURATION_MS}ms ease-in`,
        whiteSpace: "nowrap",
      }}
    >
      {/* Dept emoji badge */}
      {entry.deptEmoji && (
        <span style={{ fontSize: "14px", lineHeight: 1 }}>
          {entry.deptEmoji}
        </span>
      )}
      {/* Name label */}
      <span
        style={{
          color: accentColor,
          fontWeight: 700,
          fontSize: "12px",
          fontFamily: '"Hiragino Kaku Gothic ProN","BIZ UDPGothic",sans-serif',
        }}
      >
        {name}:
      </span>
      {/* Message text */}
      <span
        style={{
          color: "#e0e0e0",
          fontSize: "12px",
          fontFamily: '"Hiragino Kaku Gothic ProN","BIZ UDPGothic",sans-serif',
        }}
      >
        {displayText}
      </span>
    </div>
  );
}

// ============================================================================
// MAIN COMPONENT
// ============================================================================

export interface DiscordTickerProps {
  /** Canvas pixel width — ticker spans the full width. */
  canvasWidthPx: number;
  /** Canvas pixel height — ticker is anchored to the bottom. */
  canvasHeightPx: number;
}

export function DiscordTicker({
  canvasWidthPx,
  canvasHeightPx,
}: DiscordTickerProps): ReactNode {
  const allMessages = useGameStore(useShallow(selectRecentBridgeMessages));

  // Show the newest MAX_VISIBLE messages
  const visible = useMemo(
    () => allMessages.slice(0, MAX_VISIBLE),
    [allMessages],
  );

  // Track which keys are "new" (just arrived) for fade-in
  const [prevKeys, setPrevKeys] = useState<Set<string>>(new Set());
  const currentKeys = useMemo(
    () => new Set(visible.map((e) => e.key)),
    [visible],
  );

  useEffect(() => {
    setPrevKeys(currentKeys);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  if (visible.length === 0) return null;

  return (
    <div
      style={{
        position: "absolute",
        bottom: 0,
        left: 0,
        width: `${canvasWidthPx}px`,
        height: `${TICKER_HEIGHT}px`,
        background: "rgba(0,0,0,0.65)",
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "center",
        overflow: "hidden",
        pointerEvents: "none",
        zIndex: 10,
        // Separator line at top
        borderTop: "1px solid rgba(255,255,255,0.12)",
        // Limit height so nothing bleeds into canvas
        maxHeight: `${TICKER_HEIGHT}px`,
        // Offset to align with actual canvas position on screen
        // The canvas is centered inside the wrapper div, so bottom=0 works
        // only if the wrapper exactly matches the canvas height.
        // We compensate via the parent's relative positioning.
        boxSizing: "border-box",
      }}
    >
      {/* Separator glyph */}
      <span
        style={{
          color: "rgba(255,255,255,0.4)",
          fontSize: "11px",
          padding: "0 6px",
          flexShrink: 0,
          fontFamily: "monospace",
        }}
      >
        ◈ DISCORD
      </span>

      {/* Message entries — newest first, scrolling left */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          flexDirection: "row",
          gap: 0,
          overflow: "hidden",
          flex: 1,
        }}
      >
        {visible.map((entry) => (
          <TickerEntry
            key={entry.key}
            entry={entry}
            isNew={!prevKeys.has(entry.key)}
          />
        ))}
      </div>
    </div>
  );
}
