"use client";

/**
 * MainScreen — "Bridge Activity Monitor" main screen for the space station command bridge.
 *
 * Positioned at x=230, y=56, w=810, h=180.
 *
 * Three sections:
 *   LEFT   — Department Radar: animated sweep, 8 dept dots, crosshair
 *   CENTER — Activity Log: scrolling live log entries
 *   RIGHT  — Metrics panel: uptime, agents, commands/hr, bridge, vps, queue + sparkline
 */

import { Graphics, TextStyle } from "pixi.js";
import { useCallback, useRef, type ReactNode } from "react";
import { useTick } from "@pixi/react";
import {
  SCREEN_X,
  SCREEN_Y,
  SCREEN_W,
  SCREEN_H,
  GOLD,
  BLUE,
  GREEN,
  RED,
  goldAlpha,
  blueAlpha,
  greenAlpha,
  redAlpha,
  blackAlpha,
  whiteAlpha,
} from "@/constants/spaceTheme";

// ============================================================================
// CONSTANTS
// ============================================================================

/** Padding between outer frame and content */
const PAD = 8;

// ── Section layout (all x coords are relative to SCREEN_X) ─────────────────

/** Radar section: center point */
const RADAR_CX = 76; // offset from SCREEN_X
const RADAR_CY = SCREEN_H / 2;
const RADAR_R = 60; // outer radius

/** Log section */
const LOG_X = 152; // left edge offset from SCREEN_X
const LOG_W = 418;
const LOG_H = SCREEN_H - PAD * 2;

/** Metrics section */
const METRICS_X = LOG_X + LOG_W + PAD; // left edge offset from SCREEN_X
const METRICS_W = SCREEN_W - METRICS_X - PAD;
const METRICS_H = SCREEN_H - PAD * 2;

/** Corner ornament radius */
const CORNER_R = 6;

// ============================================================================
// DEPARTMENT DOTS
// Eight departments placed at fixed polar angles around the radar.
// Format: [angle_degrees, label, colorHex]
// ============================================================================

const DEPT_DOTS: [number, string, number][] = [
  [  0, "RES", 0x3498db],  // Research  — top
  [ 45, "ADV", 0xe67e22],  // Advertising
  [ 90, "WRI", 0xf39c12],  // Writing   — right
  [135, "DSG", 0x9b59b6],  // Design
  [180, "CNT", 0xe74c3c],  // Content   — bottom
  [225, "SLS", 0x2ecc71],  // Sales
  [270, "PHI", 0xf1c40f],  // Phil cons — left
  [315, "TAK", 0x1abc9c],  // Takumi X
];

/** Radius at which dept dots are placed */
const DOT_ORBIT_R = 46;

// ============================================================================
// HARDCODED LOG DATA
// ============================================================================

interface LogEntry {
  t: string;
  a: string;
  c: number;
  m: string;
}

const LOG_DATA: LogEntry[] = [
  { t: "14:32:07", a: "Phil",    c: 0xf1c40f, m: "All departments briefed" },
  { t: "14:31:55", a: "Ryou",    c: 0x3498db, m: "FB group scan: 312 indexed" },
  { t: "14:31:42", a: "System",  c: 0x00ff88, m: "HMAC webhook authenticated" },
  { t: "14:31:30", a: "Haru",    c: 0xe74c3c, m: "Script v2 submitted" },
  { t: "14:31:18", a: "Kai",     c: 0xf39c12, m: "LP copy scored 88pts" },
  { t: "14:31:05", a: "Rick",    c: 0x9b59b6, m: "14 slides exported" },
  { t: "14:30:51", a: "Ena",     c: 0xe67e22, m: "CPA estimate x3" },
  { t: "14:30:38", a: "Rei",     c: 0x2ecc71, m: "Demo scheduled" },
  { t: "14:30:22", a: "System",  c: 0x4a9eff, m: "3 new commands queued" },
  { t: "14:30:10", a: "Tadashi", c: 0x1abc9c, m: "Market table updated" },
  { t: "14:29:58", a: "System",  c: 0x00ff88, m: "VPS: CPU 12% / MEM 34%" },
  { t: "14:29:45", a: "Phil",    c: 0xf1c40f, m: "shared_state synchronized" },
];

/** Height of each log row in pixels */
const ROW_H = 14;
/** Number of fully visible rows */
const VISIBLE_ROWS = 9;
/** Log content top margin (below header) */
const LOG_HEADER_H = 18;

// ============================================================================
// TEXT STYLES (static — created once)
// ============================================================================

const HEADER_STYLE = new TextStyle({
  fontFamily: '"Courier New", Courier, monospace',
  fontSize: 7,
  fontWeight: "bold",
  fill: GOLD,
  letterSpacing: 2,
});

const LABEL_STYLE = new TextStyle({
  fontFamily: '"Courier New", Courier, monospace',
  fontSize: 6,
  fontWeight: "bold",
  fill: BLUE,
  letterSpacing: 1,
});

const SUBHEADER_STYLE = new TextStyle({
  fontFamily: '"Courier New", Courier, monospace',
  fontSize: 7,
  fontWeight: "bold",
  fill: BLUE,
  letterSpacing: 2,
});

// ============================================================================
// HELPER — convert hex number to CSS color string for TextStyle.fill
// ============================================================================

function hexFill(hex: number): string {
  return `#${hex.toString(16).padStart(6, "0")}`;
}

// ============================================================================
// STATIC FRAME DRAW
// ============================================================================

function drawFrame(g: Graphics): void {
  g.clear();

  const x = SCREEN_X;
  const y = SCREEN_Y;
  const w = SCREEN_W;
  const h = SCREEN_H;

  // ── Screen body ────────────────────────────────────────────────────────────
  g.roundRect(x, y, w, h, 3);
  g.fill({ color: 0x020810, alpha: 1 });

  // ── Outer gold frame (glow simulation) ────────────────────────────────────
  g.roundRect(x, y, w, h, 3);
  g.stroke({ width: 3, color: GOLD, alpha: 0.15 }); // outer soft glow

  g.roundRect(x + 1, y + 1, w - 2, h - 2, 3);
  g.stroke({ width: 2, color: GOLD, alpha: 0.5 }); // crisp gold border

  // ── Inner frame (very faint) ───────────────────────────────────────────────
  g.roundRect(x + 4, y + 4, w - 8, h - 8, 2);
  g.stroke({ width: 0.5, color: GOLD, alpha: 0.1 });

  // ── Corner ornaments (gold circles with dark center dots) ─────────────────
  const corners: [number, number][] = [
    [x + 2, y + 2],
    [x + w - 2, y + 2],
    [x + 2, y + h - 2],
    [x + w - 2, y + h - 2],
  ];
  for (const [cx, cy] of corners) {
    // Outer ring
    g.circle(cx, cy, CORNER_R);
    g.fill(goldAlpha(0.0));
    g.stroke({ width: 1.5, color: GOLD, alpha: 0.7 });
    // Dark center dot
    g.circle(cx, cy, 2);
    g.fill({ color: 0x020810, alpha: 1 });
    g.stroke({ width: 1, color: GOLD, alpha: 0.5 });
  }

  // ── Section dividers (vertical lines) ─────────────────────────────────────
  const divX1 = x + LOG_X - PAD / 2;
  const divX2 = x + METRICS_X - PAD / 2;
  for (const dx of [divX1, divX2]) {
    g.moveTo(dx, y + 10);
    g.lineTo(dx, y + h - 10);
    g.stroke({ width: 0.5, color: GOLD, alpha: 0.2 });
  }

  // ── Radar section background ───────────────────────────────────────────────
  const rX = x + PAD;
  const rW = LOG_X - PAD * 2;
  g.rect(rX, y + PAD, rW, h - PAD * 2);
  g.fill(blackAlpha(0.3));

  // ── Log section background ─────────────────────────────────────────────────
  g.rect(x + LOG_X, y + PAD, LOG_W, LOG_H);
  g.fill({ color: 0x010608, alpha: 0.8 });

  // ── Metrics section background ─────────────────────────────────────────────
  g.rect(x + METRICS_X, y + PAD, METRICS_W, METRICS_H);
  g.fill({ color: 0x010608, alpha: 0.8 });

  // ── Radar crosshairs ──────────────────────────────────────────────────────
  const rcx = x + RADAR_CX;
  const rcy = y + RADAR_CY;
  // Horizontal
  g.moveTo(rcx - RADAR_R, rcy);
  g.lineTo(rcx + RADAR_R, rcy);
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.15 });
  // Vertical
  g.moveTo(rcx, rcy - RADAR_R);
  g.lineTo(rcx, rcy + RADAR_R);
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.15 });

  // ── Radar concentric circles ───────────────────────────────────────────────
  for (const r of [RADAR_R * 0.33, RADAR_R * 0.66, RADAR_R]) {
    g.circle(rcx, rcy, r);
    g.fill(blueAlpha(0.0));
    g.stroke({ width: 0.5, color: BLUE, alpha: 0.15 });
  }

  // ── Dept dots on radar ─────────────────────────────────────────────────────
  for (const [angleDeg, , color] of DEPT_DOTS) {
    const rad = (angleDeg - 90) * (Math.PI / 180);
    const dx = rcx + Math.cos(rad) * DOT_ORBIT_R;
    const dy = rcy + Math.sin(rad) * DOT_ORBIT_R;

    // Outer glow ring
    g.circle(dx, dy, 4);
    g.fill({ color, alpha: 0.2 });

    // Core dot
    g.circle(dx, dy, 2.5);
    g.fill({ color, alpha: 0.9 });
  }

  // ── Radar center dot (gold) ────────────────────────────────────────────────
  g.circle(rcx, rcy, 3);
  g.fill(goldAlpha(1));

  // ── Metrics separator lines ────────────────────────────────────────────────
  const mX = x + METRICS_X;
  const METRIC_ROWS = 6;
  const rowH = (METRICS_H - LOG_HEADER_H - 30) / METRIC_ROWS; // 30 = sparkline height
  for (let i = 1; i < METRIC_ROWS; i++) {
    const lineY = y + PAD + LOG_HEADER_H + i * rowH;
    g.moveTo(mX + 4, lineY);
    g.lineTo(mX + METRICS_W - 4, lineY);
    g.stroke({ width: 0.5, color: GOLD, alpha: 0.12 });
  }
}

// ============================================================================
// SHARED: no-op draw callback for purely imperative graphics nodes
// ============================================================================

function noop(_g: Graphics): void {
  // intentionally empty — content is drawn imperatively in useTick
}

// ============================================================================
// ANIMATED RADAR SWEEP
// A pie-slice arc that rotates continuously, drawn imperatively via useTick.
// ============================================================================

function RadarSweep(): ReactNode {
  const gRef = useRef<Graphics>(null);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const g = gRef.current;
    if (!g) return;

    g.clear();

    const t = timeRef.current;
    const angle = (t * 1.5) % (Math.PI * 2);
    const sweepSpan = Math.PI / 4; // 45 degree sweep arc

    const rcx = SCREEN_X + RADAR_CX;
    const rcy = SCREEN_Y + RADAR_CY;

    // Sweep arc (filled sector with low alpha to simulate radial gradient glow)
    // Draw multiple concentric sectors with decreasing alpha for gradient feel
    const steps = 6;
    for (let i = steps; i >= 1; i--) {
      const rr = (RADAR_R * i) / steps;
      const alpha = (0.18 * i) / steps;

      g.moveTo(rcx, rcy);
      g.arc(rcx, rcy, rr, angle - sweepSpan, angle);
      g.closePath();
      g.fill({ color: GREEN, alpha });
    }

    // Leading sweep line (solid)
    g.moveTo(rcx, rcy);
    g.lineTo(
      rcx + Math.cos(angle) * RADAR_R,
      rcy + Math.sin(angle) * RADAR_R,
    );
    g.stroke({ width: 1, color: GREEN, alpha: 0.7 });
  });

  return <pixiGraphics ref={gRef} draw={noop} />;
}

// ============================================================================
// ANIMATED LOG SCROLL
// Redraws the log panel background and visible entry backgrounds via useTick.
// Text entries are rendered as pixiText nodes (positioned via React state).
// ============================================================================

function LogScrollOverlay(): ReactNode {
  const gRef = useRef<Graphics>(null);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const g = gRef.current;
    if (!g) return;

    g.clear();

    // Scroll offset: move one row every 3 seconds, wrap around LOG_DATA.length rows
    const totalRows = LOG_DATA.length;
    const scrollPx = (timeRef.current * (ROW_H / 3)) % (totalRows * ROW_H);

    const lx = SCREEN_X + LOG_X;
    const ly = SCREEN_Y + PAD + LOG_HEADER_H;

    // Draw alternating row tint for even rows
    for (let i = 0; i < VISIBLE_ROWS + 1; i++) {
      const rowY = ly + i * ROW_H - (scrollPx % ROW_H);
      if (rowY < ly || rowY > ly + LOG_H - LOG_HEADER_H) continue;

      // Determine which log entry this row corresponds to
      const entryIdx =
        (Math.floor(scrollPx / ROW_H) + i) % totalRows;

      // Alternating row background
      if (entryIdx % 2 === 0) {
        g.rect(lx + 2, rowY, LOG_W - 4, ROW_H);
        g.fill(blueAlpha(0.04));
      }
    }

    // Fade edges (top and bottom gradient simulation via stacked rects)
    // Top fade
    for (let i = 0; i < 4; i++) {
      g.rect(lx + 2, ly + i * 2, LOG_W - 4, 2);
      g.fill({ color: 0x010608, alpha: 0.6 - i * 0.12 });
    }
    // Bottom fade
    const bottomY = ly + (VISIBLE_ROWS * ROW_H) - 8;
    for (let i = 0; i < 4; i++) {
      g.rect(lx + 2, bottomY + i * 2, LOG_W - 4, 2);
      g.fill({ color: 0x010608, alpha: i * 0.15 });
    }
  });

  return <pixiGraphics ref={gRef} draw={noop} />;
}

// ============================================================================
// LOG TEXT ENTRIES (pixiText-based, scroll position updated via useTick)
// We render a fixed pool of VISIBLE_ROWS+1 text slots and update their content
// and y-position each frame using refs.
// ============================================================================

/**
 * A single scrolling log row (timestamp + agent + message).
 * The container y-position is updated imperatively each tick.
 */
interface LogRowProps {
  slotIndex: number; // which visual slot (0..VISIBLE_ROWS)
  scrollRef: React.MutableRefObject<number>; // shared scroll offset
}

const TS_STYLE = new TextStyle({
  fontFamily: '"Courier New", Courier, monospace',
  fontSize: 7,
  fill: BLUE,
});

const MSG_STYLE = new TextStyle({
  fontFamily: '"Courier New", Courier, monospace',
  fontSize: 7,
  fill: 0xaabbcc,
});

/**
 * Builds a per-agent TextStyle with the agent's color.
 * Memoised per hex value — created once per unique color.
 */
const agentStyleCache = new Map<number, TextStyle>();
function getAgentStyle(color: number): TextStyle {
  let s = agentStyleCache.get(color);
  if (!s) {
    s = new TextStyle({
      fontFamily: '"Courier New", Courier, monospace',
      fontSize: 7,
      fontWeight: "bold",
      fill: hexFill(color),
    });
    agentStyleCache.set(color, s);
  }
  return s;
}

function LogRow({ slotIndex, scrollRef }: LogRowProps): ReactNode {
  const containerRef = useRef<{ y: number } | null>(null);
  const tsRef = useRef<{ text: string } | null>(null);
  const agentRef = useRef<{ text: string; style: TextStyle } | null>(null);
  const msgRef = useRef<{ text: string } | null>(null);

  // Base Y for the log content area
  const baseY = SCREEN_Y + PAD + LOG_HEADER_H;
  const lx = SCREEN_X + LOG_X;

  useTick(() => {
    const scrollPx = scrollRef.current;
    const totalRows = LOG_DATA.length;

    // Which entry index this slot shows
    const entryIdx =
      (Math.floor(scrollPx / ROW_H) + slotIndex) % totalRows;
    const entry = LOG_DATA[entryIdx];

    // Y position (sub-pixel scroll within a row)
    const rowY = baseY + slotIndex * ROW_H - (scrollPx % ROW_H);

    // Clip: hide rows outside the visible band
    const alpha =
      rowY < baseY - ROW_H || rowY > baseY + VISIBLE_ROWS * ROW_H ? 0 : 1;

    if (containerRef.current) {
      (containerRef.current as unknown as { y: number; alpha: number }).y =
        rowY;
      (containerRef.current as unknown as { alpha: number }).alpha = alpha;
    }

    if (tsRef.current) {
      tsRef.current.text = entry.t;
    }
    if (agentRef.current) {
      agentRef.current.text = entry.a.padEnd(8, " ");
      agentRef.current.style = getAgentStyle(entry.c);
    }
    if (msgRef.current) {
      msgRef.current.text = entry.m;
    }
  });

  // Initial values from first entry
  const initEntry = LOG_DATA[slotIndex % LOG_DATA.length];
  const initY = baseY + slotIndex * ROW_H;

  return (
    <pixiContainer
      ref={(c) => {
        containerRef.current = c as unknown as { y: number } | null;
      }}
      x={lx + 4}
      y={initY}
    >
      {/* Timestamp */}
      <pixiText
        ref={(t) => {
          tsRef.current = t as unknown as { text: string } | null;
        }}
        text={initEntry.t}
        x={0}
        y={0}
        style={TS_STYLE}
      />
      {/* Agent name */}
      <pixiText
        ref={(t) => {
          agentRef.current = t as unknown as { text: string; style: TextStyle } | null;
        }}
        text={initEntry.a.padEnd(8, " ")}
        x={52}
        y={0}
        style={getAgentStyle(initEntry.c)}
      />
      {/* Message */}
      <pixiText
        ref={(t) => {
          msgRef.current = t as unknown as { text: string } | null;
        }}
        text={initEntry.m}
        x={116}
        y={0}
        style={MSG_STYLE}
      />
    </pixiContainer>
  );
}

// ============================================================================
// ANIMATED METRICS PANEL
// Numbers that animate (CMDS/H, QUEUE) and a sine-wave sparkline.
// ============================================================================

function MetricsPanel(): ReactNode {
  const gRef = useRef<Graphics>(null);
  const timeRef = useRef(0);

  // Refs for animated text nodes
  const cmdsRef = useRef<{ text: string } | null>(null);
  const queueRef = useRef<{ text: string } | null>(null);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const t = timeRef.current;
    const g = gRef.current;

    // ── Animated text values ───────────────────────────────────────────────
    const cmdsPerHour = Math.round(42 + Math.sin(t * 0.3) * 8);
    const queueLen = Math.max(0, Math.round(3 + Math.sin(t * 0.7) * 2));

    if (cmdsRef.current) cmdsRef.current.text = `${cmdsPerHour}`;
    if (queueRef.current) queueRef.current.text = `${queueLen}`;

    // ── Sparkline ─────────────────────────────────────────────────────────
    if (!g) return;
    g.clear();

    const mX = SCREEN_X + METRICS_X + 4;
    const mW = METRICS_W - 8;
    const sparkY = SCREEN_Y + PAD + METRICS_H - 22;
    const sparkH = 14;
    const points = 40;

    // Background bar
    g.rect(mX, sparkY, mW, sparkH);
    g.fill(blueAlpha(0.07));

    // Sine wave path
    g.moveTo(mX, sparkY + sparkH / 2);
    for (let i = 0; i <= points; i++) {
      const px = mX + (i / points) * mW;
      const phase = (i / points) * Math.PI * 4 + t * 2;
      const py = sparkY + sparkH / 2 - Math.sin(phase) * (sparkH * 0.38);
      if (i === 0) {
        g.moveTo(px, py);
      } else {
        g.lineTo(px, py);
      }
    }
    g.stroke({ width: 1, color: GREEN, alpha: 0.7 });

    // LIVE blink indicator (draws on this same graphics for efficiency)
    const blinkOn = Math.floor(t * 2) % 2 === 0;
    const liveDotX = SCREEN_X + LOG_X + LOG_W - 10;
    const liveDotY = SCREEN_Y + PAD + 9;
    g.circle(liveDotX, liveDotY, 3);
    g.fill({ color: RED, alpha: blinkOn ? 0.9 : 0.2 });
  });

  // ── Metric row layout ──────────────────────────────────────────────────────
  const mX = SCREEN_X + METRICS_X;
  const mY = SCREEN_Y + PAD;
  const METRIC_ROWS = 6;
  const rowH =
    (METRICS_H - LOG_HEADER_H - 30) / METRIC_ROWS;

  // Static metric definitions [label, staticValue, color, isAnimated-key]
  const staticMetrics: [string, string, number][] = [
    ["UPTIME", "99.7%", GREEN],
    ["AGENTS", "8/8",   GOLD],
    ["CMDS/H", "—",     BLUE],   // animated
    ["BRIDGE", "SYNC",  GREEN],
    ["VPS",    "OK",    GREEN],
    ["QUEUE",  "—",     GOLD],   // animated
  ];

  return (
    <>
      {/* Animated graphics: sparkline + LIVE dot */}
      <pixiGraphics ref={gRef} draw={noop} />

      {/* Static metric labels */}
      {staticMetrics.map(([label, _val, color], i) => {
        const ry = mY + LOG_HEADER_H + i * rowH + rowH / 2 - 3;
        return (
          <pixiContainer key={label}>
            {/* Row label */}
            <pixiText
              text={label}
              x={mX + 6}
              y={ry}
              style={
                new TextStyle({
                  fontFamily: '"Courier New", Courier, monospace',
                  fontSize: 7,
                  fill: 0x7a9ab8,
                  letterSpacing: 1,
                })
              }
            />
            {/* Static value (for non-animated rows) */}
            {label !== "CMDS/H" && label !== "QUEUE" && (
              <pixiText
                text={_val}
                x={mX + METRICS_W - 6}
                y={ry}
                anchor={{ x: 1, y: 0 }}
                style={
                  new TextStyle({
                    fontFamily: '"Courier New", Courier, monospace',
                    fontSize: 7,
                    fontWeight: "bold",
                    fill: hexFill(color),
                    letterSpacing: 1,
                  })
                }
              />
            )}
          </pixiContainer>
        );
      })}

      {/* Animated value: CMDS/H */}
      <pixiText
        ref={(t) => {
          cmdsRef.current = t as unknown as { text: string } | null;
        }}
        text="42"
        x={mX + METRICS_W - 6}
        y={mY + LOG_HEADER_H + 2 * rowH + rowH / 2 - 3}
        anchor={{ x: 1, y: 0 }}
        style={
          new TextStyle({
            fontFamily: '"Courier New", Courier, monospace',
            fontSize: 7,
            fontWeight: "bold",
            fill: hexFill(BLUE),
            letterSpacing: 1,
          })
        }
      />

      {/* Animated value: QUEUE */}
      <pixiText
        ref={(t) => {
          queueRef.current = t as unknown as { text: string } | null;
        }}
        text="3"
        x={mX + METRICS_W - 6}
        y={mY + LOG_HEADER_H + 5 * rowH + rowH / 2 - 3}
        anchor={{ x: 1, y: 0 }}
        style={
          new TextStyle({
            fontFamily: '"Courier New", Courier, monospace',
            fontSize: 7,
            fontWeight: "bold",
            fill: hexFill(GOLD),
            letterSpacing: 1,
          })
        }
      />

      {/* Sparkline label */}
      <pixiText
        text="ACTIVITY"
        x={mX + 6}
        y={SCREEN_Y + PAD + METRICS_H - 20}
        alpha={0.5}
        style={
          new TextStyle({
            fontFamily: '"Courier New", Courier, monospace',
            fontSize: 5,
            fill: BLUE,
            letterSpacing: 1,
          })
        }
      />
    </>
  );
}

// ============================================================================
// DEPT DOT LABELS on radar (static pixiText nodes)
// ============================================================================

function RadarDeptLabels(): ReactNode {
  const rcx = SCREEN_X + RADAR_CX;
  const rcy = SCREEN_Y + RADAR_CY;
  const LABEL_ORBIT_R = DOT_ORBIT_R + 12;

  return (
    <>
      {DEPT_DOTS.map(([angleDeg, label, color]) => {
        const rad = (angleDeg - 90) * (Math.PI / 180);
        const lx = rcx + Math.cos(rad) * LABEL_ORBIT_R;
        const ly = rcy + Math.sin(rad) * LABEL_ORBIT_R;

        return (
          <pixiText
            key={label}
            text={label}
            x={lx}
            y={ly}
            anchor={0.5}
            alpha={0.8}
            style={
              new TextStyle({
                fontFamily: '"Courier New", Courier, monospace',
                fontSize: 5,
                fontWeight: "bold",
                fill: hexFill(color),
              })
            }
          />
        );
      })}
    </>
  );
}

// ============================================================================
// LOG SECTION — shared scroll ref + row pool
// ============================================================================

function LogSection(): ReactNode {
  // Shared mutable scroll position (updated by a ticker, read by each LogRow)
  const scrollRef = useRef(0);

  // Master ticker: advance scroll time
  useTick((ticker) => {
    scrollRef.current += ticker.deltaTime * 0.016 * (ROW_H / 3);
  });

  const slots = Array.from({ length: VISIBLE_ROWS + 1 }, (_, i) => i);

  return (
    <>
      {slots.map((i) => (
        <LogRow key={i} slotIndex={i} scrollRef={scrollRef} />
      ))}
    </>
  );
}

// ============================================================================
// MAIN COMPONENT
// ============================================================================

export function MainScreen(): ReactNode {
  const drawStaticFrame = useCallback((g: Graphics) => {
    drawFrame(g);
  }, []);

  // Convenient anchors
  const cx = SCREEN_X + SCREEN_W / 2;
  const headerY = SCREEN_Y + PAD + 5;

  // Log section header positions
  const logHeaderX = SCREEN_X + LOG_X + LOG_W / 2;

  // Metrics section header
  const metricsHeaderX = SCREEN_X + METRICS_X + METRICS_W / 2;

  return (
    <pixiContainer>
      {/* ── Static frame, section backgrounds, radar geometry ───────────── */}
      <pixiGraphics draw={drawStaticFrame} />

      {/* ── Panel header labels ──────────────────────────────────────────── */}
      {/* Left: "MAIN SCREEN" */}
      <pixiText
        text="MAIN SCREEN"
        x={SCREEN_X + 12}
        y={headerY}
        style={HEADER_STYLE}
      />
      {/* Right: "BRIDGE ACTIVITY MONITOR" */}
      <pixiText
        text="BRIDGE ACTIVITY MONITOR"
        x={SCREEN_X + SCREEN_W - 12}
        y={headerY}
        anchor={{ x: 1, y: 0 }}
        style={HEADER_STYLE}
      />

      {/* ── Radar section ────────────────────────────────────────────────── */}
      <RadarSweep />
      <RadarDeptLabels />

      {/* Radar "DEPT MAP" label */}
      <pixiText
        text="DEPT MAP"
        x={SCREEN_X + RADAR_CX}
        y={SCREEN_Y + SCREEN_H - PAD - 8}
        anchor={{ x: 0.5, y: 1 }}
        style={LABEL_STYLE}
      />

      {/* ── Activity Log section ─────────────────────────────────────────── */}
      {/* Header */}
      <pixiText
        text="ACTIVITY LOG"
        x={logHeaderX - 16}
        y={headerY}
        style={SUBHEADER_STYLE}
      />
      {/* "LIVE" text label */}
      <pixiText
        text="LIVE"
        x={SCREEN_X + LOG_X + LOG_W - 18}
        y={headerY}
        style={
          new TextStyle({
            fontFamily: '"Courier New", Courier, monospace',
            fontSize: 6,
            fontWeight: "bold",
            fill: RED,
            letterSpacing: 1,
          })
        }
      />

      {/* Log rows (animated scroll) */}
      <LogSection />
      {/* Log overlay (row tint + edge fades + LIVE blink) — rendered by MetricsPanel ticker */}
      <LogScrollOverlay />

      {/* ── Metrics section ──────────────────────────────────────────────── */}
      <pixiText
        text="METRICS"
        x={metricsHeaderX}
        y={headerY}
        anchor={{ x: 0.5, y: 0 }}
        style={SUBHEADER_STYLE}
      />
      <MetricsPanel />
    </pixiContainer>
  );
}
