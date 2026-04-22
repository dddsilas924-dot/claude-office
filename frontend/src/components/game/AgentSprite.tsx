/**
 * AgentSprite Component
 *
 * Renders a single agent character as a colored capsule with optional bubble.
 * Supports headset and sunglasses accessories.
 */

"use client";

import { memo, useMemo, useState, useCallback, useRef, type ReactNode } from "react";
import { useTick } from "@pixi/react";
import { Graphics, TextStyle, Texture } from "pixi.js";
import type { Position, BubbleContent } from "@/types";
import type { AgentPhase, BridgeAgentStatus, BridgeAgentVisibility } from "@/stores/gameStore";
import { isInElevatorZone } from "@/systems/queuePositions";
import { ICON_MAP } from "./shared/iconMap";
import { drawBubble, drawIconBadge } from "./shared/drawBubble";
import { drawRightArm, drawLeftArm } from "./shared/drawArm";

// ============================================================================
// TYPES
// ============================================================================

export interface AgentSpriteProps {
  id: string;
  name: string | null;
  color: string;
  number: number;
  position: Position;
  phase: AgentPhase;
  bubble: BubbleContent | null;
  headsetTexture?: Texture | null;
  sunglassesTexture?: Texture | null;
  characterTexture?: Texture | null; // Character portrait PNG (replaces capsule)
  renderBubble?: boolean; // Whether to render bubble (default true)
  renderLabel?: boolean; // Whether to render name label (default true)
  isTyping?: boolean; // Whether agent is typing (animates arms)
  // ── Bridge-only reaction props ──────────────────────────────────────────────
  /** Timestamp (Date.now()) of most-recent event — triggers jump animation. */
  reactionAt?: number;
  /** event_kind string — controls visual style of reaction ("ASK_STARTED" etc.) */
  eventKind?: string;
  /** Visual status from metadata — drives badge / animation speed variant. */
  agentStatus?: BridgeAgentStatus;
  /** Visibility level from metadata — drives glow intensity. */
  visibility?: BridgeAgentVisibility;
  /** Stagger delay in ms before this sprite starts its reaction animation. */
  reactionDelayMs?: number;
}

// ============================================================================
// CONSTANTS
// ============================================================================

const AGENT_WIDTH = 48; // 1.5 blocks × 32px (matches boss)
const AGENT_HEIGHT = 80; // 2.5 blocks × 32px (matches boss)
const STROKE_WIDTH = 4;

// ============================================================================
// DRAWING FUNCTIONS
// ============================================================================

function drawAgent(g: Graphics, color: string): void {
  g.clear();

  // Convert hex color string to number
  const colorNum = parseInt(color.replace("#", ""), 16) || 0xff6b6b;

  // Agent body (colored capsule with white border)
  // Position is at CENTER OF BOTTOM CIRCLE, so capsule extends from -54 to +22
  // Inset by half stroke width so total size matches AGENT_WIDTH × AGENT_HEIGHT
  const innerWidth = AGENT_WIDTH - STROKE_WIDTH;
  const innerHeight = AGENT_HEIGHT - STROKE_WIDTH;
  const agentRadius = innerWidth / 2; // 22px - radius of top/bottom circles
  g.roundRect(
    -innerWidth / 2,
    -innerHeight + agentRadius, // Bottom circle center at y=0
    innerWidth,
    innerHeight,
    agentRadius,
  );
  g.fill(colorNum);
  g.stroke({ width: STROKE_WIDTH, color: 0xffffff });
}

// ============================================================================
// BRIDGE STATUS BADGE
// Rendered above the character head to show agentStatus / visibility.
// ============================================================================

/** Glyph + color config for each BridgeAgentStatus value. */
const STATUS_CONFIG: Record<
  string,
  { glyph: string; glyphColor: number; bgColor: number }
> = {
  blocked: { glyph: "!", glyphColor: 0xffffff, bgColor: 0xe74c3c },
  busy: { glyph: "⚙", glyphColor: 0xffffff, bgColor: 0xe67e22 },
  has_output: { glyph: "📦", glyphColor: 0xffffff, bgColor: 0x27ae60 },
  idle: { glyph: "", glyphColor: 0xffffff, bgColor: 0x000000 }, // hidden
};

/**
 * Tiny badge rendered above the character head.
 * Only rendered when agentStatus != "idle" or visibility != "internal".
 */
interface BridgeStatusBadgeProps {
  agentStatus: BridgeAgentStatus;
  visibility: BridgeAgentVisibility;
  /** y coordinate of the badge center relative to agent origin. */
  yOffset: number;
}

function BridgeStatusBadge({
  agentStatus,
  visibility,
  yOffset,
}: BridgeStatusBadgeProps): ReactNode {
  // Glow ring config based on visibility level
  const glowColor =
    visibility === "public"
      ? 0xf1c40f // gold
      : visibility === "shared"
        ? 0x3498db // blue
        : null; // no glow for "internal"

  const statusCfg = STATUS_CONFIG[agentStatus];
  const showStatusBadge = agentStatus !== "idle" && statusCfg;

  if (!glowColor && !showStatusBadge) return null;

  return (
    <pixiContainer y={yOffset}>
      {/* Visibility glow ring */}
      {glowColor && (
        <pixiGraphics
          draw={(g) => {
            g.clear();
            g.circle(0, 0, 14);
            g.stroke({ width: 2.5, color: glowColor, alpha: 0.85 });
          }}
        />
      )}
      {/* Status badge (blocked / busy / has_output) */}
      {showStatusBadge && (
        <>
          <pixiGraphics
            draw={(g) => {
              g.clear();
              g.circle(0, 0, 8);
              g.fill(statusCfg.bgColor);
              g.stroke({ width: 1.5, color: 0xffffff });
            }}
          />
          <pixiContainer scale={0.5}>
            <pixiText
              text={statusCfg.glyph}
              anchor={0.5}
              style={{
                fontFamily:
                  '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",monospace',
                fontSize: 16,
                fill: statusCfg.glyphColor,
                fontWeight: "bold",
              }}
              resolution={2}
            />
          </pixiContainer>
        </>
      )}
    </pixiContainer>
  );
}

// ============================================================================
// NAME LABEL (with dept glyph split-off)
// ============================================================================

/**
 * Regex that peels a leading Extended_Pictographic glyph (optionally part
 * of a ZWJ sequence, optionally tailed by VS16) plus any trailing space(s).
 *
 * We treat Bridge-produced names like "🏠 不動産事業部 アイリ" by pulling
 * the 🏠 out so it can render as a large badge above the capsule while
 * the Japanese division + character text stays as the compact label.
 */
const LEADING_EMOJI_RE =
  /^(\p{Extended_Pictographic}(?:\u200D\p{Extended_Pictographic})*\uFE0F?)\s*/u;

/**
 * Font stack tuned for Pixi.js text rendering: emoji color fonts first for
 * the glyph badge, then a Japanese-aware serif/sans fallback for the label
 * so CJK glyphs don't degrade to tofu on older Chrome/Pixi combos.
 */
const EMOJI_FONT_FAMILY =
  '"Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif';
const LABEL_FONT_FAMILY =
  '"Hiragino Kaku Gothic ProN", "ヒラギノ角ゴ ProN", "BIZ UDPGothic", "Yu Gothic", "Meiryo", "Helvetica Neue", Arial, sans-serif';

interface AgentNameLabelProps {
  name: string;
}

/**
 * Renders the sprite's name tag. Splits an optional leading pictographic
 * glyph into a big floating badge at y=-104 and draws the remaining text
 * at y=-70 so the two layers never overlap. The regex match is cheap and
 * React Compiler caches pure sub-computations, so no manual useMemo.
 */
function AgentNameLabel({ name }: AgentNameLabelProps): ReactNode {
  const match = name.match(LEADING_EMOJI_RE);
  const emoji = match ? match[1] : null;
  const label = match ? name.slice(match[0].length) : name;

  return (
    <>
      {emoji && (
        <pixiContainer y={-104} scale={0.5}>
          <pixiText
            text={emoji}
            anchor={0.5}
            style={{
              fontFamily: EMOJI_FONT_FAMILY,
              fontSize: 44,
              fill: 0xffffff,
            }}
            resolution={2}
          />
        </pixiContainer>
      )}
      {label && (
        <pixiContainer y={-70} scale={0.5}>
          <pixiText
            text={label}
            anchor={0.5}
            style={{
              fontFamily: LABEL_FONT_FAMILY,
              fontSize: 22,
              fill: 0xffffff,
              fontWeight: "bold",
              stroke: { width: 4, color: 0x000000 },
            }}
            resolution={2}
          />
        </pixiContainer>
      )}
    </>
  );
}

// ============================================================================
// BUBBLE COMPONENT
// ============================================================================

interface BubbleProps {
  content: BubbleContent;
  yOffset: number;
  /** Which side the bubble appears: "right" (default) or "left" of agent */
  side?: "right" | "left";
}

function Bubble({ content, yOffset, side = "right" }: BubbleProps): ReactNode {
  const { text, type = "thought", icon } = content;

  // Convert icon name to emoji if needed
  const iconEmoji = icon ? (ICON_MAP[icon] ?? icon) : undefined;

  // Icon badge constants
  const badgeRadius = 16; // Radius of the circular badge

  // Calculate bubble dimensions (at display scale) - icon is outside bubble now
  const charWidth = 7.5;
  const paddingH = 30;
  const maxW = 220;
  const rawWidth = text.length * charWidth + paddingH;
  const bWidth = Math.min(maxW, Math.max(80, rawWidth));
  const capacity = (bWidth - paddingH) / charWidth;
  const lines = Math.max(1, Math.ceil(text.length / capacity));
  const bHeight = 35 + lines * 14;
  // Tail points back toward the agent
  const tailSide = side === "right" ? ("left" as const) : ("right" as const);

  // Text style at 2x for sharp rendering. Japanese-first font stack so
  // Bridge bubbles (えむ's message text is usually 日本語) don't hit the
  // Courier fallback path that ships without CJK glyphs.
  const textStyle = useMemo<Partial<TextStyle>>(
    () => ({
      fontFamily:
        '"Hiragino Kaku Gothic ProN", "ヒラギノ角ゴ ProN", "BIZ UDPGothic", "Yu Gothic", "Meiryo", "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", "Courier New", monospace',
      fontSize: 20,
      fill: "#000000",
      fontWeight: "bold",
      wordWrap: true,
      wordWrapWidth: (bWidth - 30) * 2,
      breakWords: true,
      align: "left",
      lineHeight: 28,
      stroke: { width: 0, color: 0x000000 },
    }),
    [bWidth],
  );

  // Icon style - larger emoji
  const iconStyle = useMemo<Partial<TextStyle>>(
    () => ({
      fontFamily:
        '"Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif',
      fontSize: 40, // Large emoji for badge
      fill: "#000000",
    }),
    [],
  );

  // Horizontal offset: positive = right of agent, negative = left
  const xOffset = side === "right" ? 45 : -45;
  // Icon badge position: outer edge of bubble
  const badgeX =
    side === "right" ? -bWidth / 2 - 6 : bWidth / 2 + 6;

  return (
    <pixiContainer y={yOffset} x={xOffset}>
      <pixiGraphics
        draw={(g) => drawBubble(g, bWidth, bHeight, type, tailSide)}
      />
      {/* Icon badge on outer corner of bubble */}
      {iconEmoji && (
        <pixiContainer x={badgeX} y={-bHeight + 6}>
          <pixiGraphics draw={(g) => drawIconBadge(g, badgeRadius)} />
          <pixiContainer scale={0.5} x={0} y={1}>
            <pixiText
              text={iconEmoji}
              anchor={0.5}
              style={iconStyle}
              resolution={2}
            />
          </pixiContainer>
        </pixiContainer>
      )}
      {/* Text rendered at 2x and scaled down for sharpness */}
      <pixiContainer x={-bWidth / 2 + 15} y={-bHeight / 2} scale={0.5}>
        <pixiText
          text={text}
          anchor={{ x: 0, y: 0.5 }}
          style={textStyle}
          resolution={2}
        />
      </pixiContainer>
    </pixiContainer>
  );
}

// ============================================================================
// MAIN COMPONENT
// ============================================================================

// Duration of jump reaction animation in ms
const JUMP_ANIM_DURATION_MS = 600;
// Peak jump height (negative Y = up)
const JUMP_PEAK_PX = 12;

function AgentSpriteComponent({
  id: _id,
  name,
  color,
  number: _number,
  position,
  phase: _phase,
  bubble,
  headsetTexture: _headsetTexture,
  sunglassesTexture,
  characterTexture,
  renderBubble = true,
  renderLabel = true,
  isTyping = false,
  reactionAt,
  eventKind = "",
  agentStatus = "idle",
  visibility = "internal",
  reactionDelayMs = 0,
}: AgentSpriteProps): ReactNode {
  // Memoize draw callback
  const drawCallback = useMemo(
    () => (g: Graphics) => drawAgent(g, color),
    [color],
  );

  // ---------- Idle breathing animation for character sprites ----------
  const [breathOffset, setBreathOffset] = useState(0);
  const breathTimeRef = useState(() => ({ t: Math.random() * Math.PI * 2 }))[0];

  // ---------- Bridge reaction jump animation ----------
  // When reactionAt changes (new event arrived), the character jumps.
  // A sine arch: jumpOffset goes 0 → -JUMP_PEAK_PX → 0 over JUMP_ANIM_DURATION_MS.
  // HEARTBEAT events produce no jump — just a subtle pulse via opacity.
  const [jumpOffset, setJumpOffset] = useState(0);
  const jumpStateRef = useRef<{
    startMs: number;
    active: boolean;
    delayRemaining: number;
  }>({ startMs: 0, active: false, delayRemaining: 0 });

  // Track previous reactionAt to detect new events
  const prevReactionAtRef = useRef<number | undefined>(undefined);
  if (reactionAt !== prevReactionAtRef.current) {
    prevReactionAtRef.current = reactionAt;
    const isHeartbeat = eventKind === "HEARTBEAT";
    if (!isHeartbeat && reactionAt !== undefined) {
      // Arm the jump — will start after delayRemaining ticks drain
      jumpStateRef.current = {
        startMs: 0,
        active: false,
        delayRemaining: reactionDelayMs,
      };
    }
  }

  // Bubble fade-in alpha (0→1 over 300ms when reaction fires)
  const [bubbleAlpha, setBubbleAlpha] = useState(1);
  const bubbleFadeRef = useRef<{ startMs: number; active: boolean }>({
    startMs: 0,
    active: false,
  });

  useTick((ticker) => {
    const deltaMs = ticker.deltaMS;

    // Breathing animation
    if (characterTexture) {
      // For ASK_STARTED: busy animation speed
      const isBusy = agentStatus === "busy" || isTyping;
      const speed = isBusy ? 0.12 : 0.04;
      const amplitude = isBusy ? 3 : 1.5;
      breathTimeRef.t += ticker.deltaTime * speed;
      setBreathOffset(Math.sin(breathTimeRef.t) * amplitude);
    }

    // Jump animation timing
    const js = jumpStateRef.current;
    if (js.delayRemaining > 0) {
      js.delayRemaining -= deltaMs;
      if (js.delayRemaining <= 0) {
        js.delayRemaining = 0;
        js.active = true;
        js.startMs = 0;
        // Arm bubble fade
        bubbleFadeRef.current = { startMs: 0, active: true };
        setBubbleAlpha(0);
      }
    } else if (js.active) {
      js.startMs += deltaMs;
      const t = Math.min(js.startMs / JUMP_ANIM_DURATION_MS, 1);
      // Sine arch: sin(π·t) peaks at t=0.5
      const newOffset = -Math.sin(Math.PI * t) * JUMP_PEAK_PX;
      setJumpOffset(newOffset);
      if (t >= 1) {
        js.active = false;
        setJumpOffset(0);
      }
    }

    // Bubble fade-in
    const bf = bubbleFadeRef.current;
    if (bf.active) {
      bf.startMs += deltaMs;
      const t = Math.min(bf.startMs / 300, 1);
      setBubbleAlpha(t);
      if (t >= 1) {
        bf.active = false;
        setBubbleAlpha(1);
      }
    }
  });

  // Derive thinking indicator: show "..." inside bubble for ASK_STARTED when no message
  const isThinking = eventKind === "ASK_STARTED";
  // For HEARTBEAT: character just blinks lightly (handled via alpha below)
  const isHeartbeat = eventKind === "HEARTBEAT";
  const heartbeatAlpha = isHeartbeat ? 0.7 : 1;

  // Bubble offset for capsule rendering
  const bubbleOffset = -93;

  // Combined Y offset: breathing bob + jump reaction
  const spriteYOffset = breathOffset + jumpOffset;

  return (
    <pixiContainer x={position.x} y={position.y} alpha={heartbeatAlpha}>
      {/* Character portrait PNG if available, otherwise capsule */}
      {characterTexture ? (
        <pixiSprite
          texture={characterTexture}
          anchor={{ x: 0.5, y: 0.9 }}
          x={0}
          y={spriteYOffset}
          scale={0.65}
        />
      ) : (
        <>
          {/* Fallback: Agent capsule body */}
          <pixiContainer y={spriteYOffset}>
            <pixiGraphics draw={drawCallback} />
          </pixiContainer>

          {/* Sunglasses (only on capsule fallback) */}
          {sunglassesTexture && (
            <pixiSprite
              texture={sunglassesTexture}
              anchor={0.5}
              x={0}
              y={-37 + spriteYOffset}
              scale={{ x: 0.036, y: 0.04 }}
            />
          )}
        </>
      )}

      {/* Agent name if present - hide when in elevator or when renderLabel is false. */}
      {renderLabel && name && !isInElevatorZone(position) && (
        <AgentNameLabel name={name} />
      )}

      {/* Bridge status badge (blocked / busy / has_output / visibility glow) */}
      {(agentStatus !== "idle" || visibility !== "internal") && (
        <BridgeStatusBadge
          agentStatus={agentStatus}
          visibility={visibility}
          yOffset={-110 + spriteYOffset}
        />
      )}

      {/* Thinking indicator for ASK_STARTED with no bubble */}
      {isThinking && !bubble && renderBubble && !isInElevatorZone(position) && (
        <Bubble
          content={{ type: "thought", text: "..." }}
          yOffset={bubbleOffset + spriteYOffset}
        />
      )}

      {/* Bubble - hide when in elevator or when renderBubble is false */}
      {renderBubble && bubble && !isInElevatorZone(position) && (
        <pixiContainer alpha={bubbleAlpha}>
          <Bubble content={bubble} yOffset={bubbleOffset + spriteYOffset} />
        </pixiContainer>
      )}
    </pixiContainer>
  );
}

// ============================================================================
// AGENT ARMS COMPONENT (rendered separately after desk surfaces)
// ============================================================================

export interface AgentArmsProps {
  position: Position;
  isTyping: boolean;
}

function AgentArmsComponent({ position, isTyping }: AgentArmsProps): ReactNode {
  // Animation state for typing
  const [typingTime, setTypingTime] = useState(0);

  // Animate typing - oscillate hands up/down
  useTick((ticker) => {
    if (isTyping) {
      setTypingTime((t) => t + ticker.deltaTime * 0.15);
    } else {
      setTypingTime(0);
    }
  });

  // Calculate arm animation offsets (subtle, out of phase for natural look)
  const rightArmOffset = isTyping ? Math.sin(typingTime * 8) * 2 : 0;
  const leftArmOffset = isTyping
    ? Math.sin(typingTime * 8 + Math.PI * 0.7) * 2
    : 0;

  // Agent arm params: body half-width 22px, shoulder at y=-16, keyboard at y=16
  const agentArmParams = useMemo(
    () => ({
      bodyHalfWidth: (AGENT_WIDTH - STROKE_WIDTH) / 2,
      startY: -16,
      endY: 16,
      handColor: 0x1f2937,
    }),
    [],
  );

  // Arm draw callbacks
  const drawRightArmCallback = useCallback(
    (g: Graphics) =>
      drawRightArm(g, { ...agentArmParams, animOffset: rightArmOffset }),
    [agentArmParams, rightArmOffset],
  );

  const drawLeftArmCallback = useCallback(
    (g: Graphics) =>
      drawLeftArm(g, { ...agentArmParams, animOffset: leftArmOffset }),
    [agentArmParams, leftArmOffset],
  );

  return (
    <pixiContainer x={position.x} y={position.y}>
      <pixiGraphics draw={drawRightArmCallback} />
      <pixiGraphics draw={drawLeftArmCallback} />
    </pixiContainer>
  );
}

export const AgentArms = memo(AgentArmsComponent);

// ============================================================================
// AGENT HEADSET COMPONENT (rendered after arms for correct z-order)
// ============================================================================

export interface AgentHeadsetProps {
  position: Position;
  headsetTexture: Texture;
}

function AgentHeadsetComponent({
  position,
  headsetTexture,
}: AgentHeadsetProps): ReactNode {
  return (
    <pixiSprite
      texture={headsetTexture}
      anchor={0.5}
      x={position.x}
      y={position.y - 38}
      scale={{ x: 0.66825, y: 0.675 }}
    />
  );
}

export const AgentHeadset = memo(AgentHeadsetComponent);

// ============================================================================
// AGENT LABEL COMPONENT (rendered separately for z-ordering)
// ============================================================================

export interface AgentLabelProps {
  name: string;
  position: Position;
}

function AgentLabelComponent({ name, position }: AgentLabelProps): ReactNode {
  // Mirrors AgentNameLabel's CJK font stack so regular Claude Code agents
  // — which sometimes carry Japanese dept names in multi-agent runs —
  // render with the same glyph fidelity as Bridge sprites above.
  return (
    <pixiContainer x={position.x} y={position.y - 70} scale={0.5}>
      <pixiText
        text={name}
        anchor={0.5}
        style={{
          fontFamily: LABEL_FONT_FAMILY,
          fontSize: 22,
          fill: 0xffffff,
          fontWeight: "bold",
          stroke: { width: 4, color: 0x000000 },
        }}
        resolution={2}
      />
    </pixiContainer>
  );
}

export const AgentLabel = memo(AgentLabelComponent);

export const AgentSprite = memo(AgentSpriteComponent);

// Export Bubble component for use in top-level bubbles layer
export { Bubble };
