/**
 * CrewPanel — Bridge Crew Status + Integrated Clock
 *
 * Right-side panel for the space station theme.
 * Shows active officer count, per-department status rows,
 * and a real-time HH:MM clock with blinking colon.
 *
 * Position: x=1080, y=56, w=170, h=200 (PANEL_* constants)
 */

"use client";

import { type ReactNode, useCallback, useRef, useState, useEffect } from "react";
import { Graphics } from "pixi.js";
import { useTick } from "@pixi/react";
import {
  PANEL_X,
  PANEL_Y,
  PANEL_W,
  PANEL_H,
  GOLD,
  BLUE,
  GREEN,
  RED,
  VIOLET,
  goldAlpha,
  blueAlpha,
  whiteAlpha,
} from "@/constants/spaceTheme";

// ============================================================================
// CREW DATA
// ============================================================================

interface CrewRow {
  dept: string;
  status: string;
  color: number;
  pulse: boolean;
}

const CREW_ROWS: CrewRow[] = [
  { dept: "COMMANDER", status: "ONLINE",  color: GOLD,   pulse: false },
  { dept: "RESEARCH",  status: "ACTIVE",  color: GREEN,  pulse: false },
  { dept: "CONTENT",   status: "WRITING", color: BLUE,   pulse: false },
  { dept: "DESIGN",    status: "RENDER",  color: VIOLET, pulse: false },
  { dept: "SECURITY",  status: "PATROL",  color: RED,    pulse: true  },
];

// ============================================================================
// STATIC FRAME
// ============================================================================

function PanelFrame(): ReactNode {
  const drawFrame = useCallback((g: Graphics) => {
    g.clear();

    // Panel background
    g.roundRect(PANEL_X, PANEL_Y, PANEL_W, PANEL_H, 5);
    g.fill({ color: 0x060e1a, alpha: 0.92 });
    g.stroke({ width: 1, color: GOLD, alpha: 0.22 });
  }, []);

  return <pixiGraphics draw={drawFrame} />;
}

// ============================================================================
// STATIC LABELS (header, big-number, crew text, separator lines)
// ============================================================================

function StaticLabels(): ReactNode {
  const PX = PANEL_X;
  const PY = PANEL_Y;
  const PW = PANEL_W;

  // Header separator line y
  const headerSepY = PY + 18;
  // Clock separator line y
  const clockSepY  = PY + 135;

  const drawLines = useCallback((g: Graphics) => {
    g.clear();

    // Header separator
    g.moveTo(PX + 8,      headerSepY);
    g.lineTo(PX + PW - 8, headerSepY);
    g.stroke({ width: 0.5, color: GOLD, alpha: 0.35 });

    // Clock section separator
    g.moveTo(PX + 8,      clockSepY);
    g.lineTo(PX + PW - 8, clockSepY);
    g.stroke({ width: 0.5, color: GOLD, alpha: 0.35 });
  }, [PX, PY, PW, headerSepY, clockSepY]);

  return (
    <>
      <pixiGraphics draw={drawLines} />

      {/* ── Header ── */}
      <pixiText
        text="BRIDGE CREW"
        x={PX + PW / 2}
        y={PY + 9}
        anchor={0.5}
        style={{
          fontFamily: "monospace",
          fontSize: 10,
          fontWeight: "bold",
          fill: GOLD,
        }}
      />

      {/* ── Big number ── */}
      {/* Glow layer: slightly larger, lower alpha */}
      <pixiText
        text="12"
        x={PX + PW / 2}
        y={PY + 45}
        anchor={0.5}
        alpha={0.25}
        style={{
          fontFamily: "monospace",
          fontSize: 42,
          fontWeight: "bold",
          fill: GOLD,
        }}
      />
      {/* Crisp foreground */}
      <pixiText
        text="12"
        x={PX + PW / 2}
        y={PY + 45}
        anchor={0.5}
        style={{
          fontFamily: "monospace",
          fontSize: 38,
          fontWeight: "bold",
          fill: GOLD,
        }}
      />

      {/* Officers subtitle */}
      <pixiText
        text="OFFICERS ACTIVE"
        x={PX + PW / 2}
        y={PY + 68}
        anchor={0.5}
        style={{
          fontFamily: "monospace",
          fontSize: 9,
          fontWeight: "normal",
          fill: BLUE,
        }}
      />

      {/* ── Crew rows: dept name + status text (dots are animated below) ── */}
      {CREW_ROWS.map((row, i) => {
        const rowY = PY + 82 + i * 14;
        return (
          <pixiContainer key={row.dept}>
            {/* Dept name */}
            <pixiText
              text={row.dept}
              x={PX + 18}
              y={rowY + 7}
              anchor={{ x: 0, y: 0.5 }}
              alpha={0.82}
              style={{
                fontFamily: "monospace",
                fontSize: 7,
                fontWeight: "normal",
                fill: 0xffffff,
              }}
            />
            {/* Status text — non-pulsing rows only; pulsing row drawn in animated section */}
            {!row.pulse && (
              <pixiText
                text={row.status}
                x={PX + PW - 8}
                y={rowY + 7}
                anchor={{ x: 1, y: 0.5 }}
                style={{
                  fontFamily: "monospace",
                  fontSize: 7,
                  fontWeight: "bold",
                  fill: row.color,
                }}
              />
            )}
          </pixiContainer>
        );
      })}
    </>
  );
}

// ============================================================================
// ANIMATED DOTS + PULSING ROW
// ============================================================================

function AnimatedCrewDots(): ReactNode {
  const gRef = useRef<Graphics>(null);
  const timeRef = useRef(0);

  // No-op draw: the ref + useTick handles all rendering imperatively
  const noop = useCallback(() => {}, []);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const g = gRef.current;
    if (!g) return;

    const t = timeRef.current;
    g.clear();

    const PX = PANEL_X;
    const PY = PANEL_Y;

    CREW_ROWS.forEach((row, i) => {
      const rowY = PY + 82 + i * 14;
      const cx = PX + 10;
      const cy = rowY + 7;

      if (row.pulse) {
        // Security dot: alpha oscillates
        const alpha = 0.55 + 0.45 * (Math.sin(t * 4) * 0.5 + 0.5);
        g.circle(cx, cy, 2.5);
        g.fill({ color: row.color, alpha });
      } else {
        g.circle(cx, cy, 2.5);
        g.fill({ color: row.color, alpha: 0.9 });
      }
    });
  });

  return <pixiGraphics ref={gRef} draw={noop} />;
}

// ============================================================================
// PULSING STATUS TEXT (SECURITY row only)
// ============================================================================

function PulsingStatusText(): ReactNode {
  const pulsingRow = CREW_ROWS.find((r) => r.pulse)!;
  const rowIndex   = CREW_ROWS.findIndex((r) => r.pulse);

  // We drive alpha via a simple ref → text alpha isn't directly mutable
  // in PixiJS React declaratively, so we keep a state updated at ~10fps
  const [alpha, setAlpha] = useState(1);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const a = Math.sin(timeRef.current * 4) > 0 ? 1.0 : 0.3;
    setAlpha(a);
  });

  const PX = PANEL_X;
  const PY = PANEL_Y;
  const PW = PANEL_W;
  const rowY = PY + 82 + rowIndex * 14;

  return (
    <pixiText
      text={pulsingRow.status}
      x={PX + PW - 8}
      y={rowY + 7}
      anchor={{ x: 1, y: 0.5 }}
      alpha={alpha}
      style={{
        fontFamily: "monospace",
        fontSize: 7,
        fontWeight: "bold",
        fill: pulsingRow.color,
      }}
    />
  );
}

// ============================================================================
// CLOCK
// ============================================================================

function ClockDisplay(): ReactNode {
  const [now, setNow] = useState<Date>(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Blinking colon alpha driven by useTick
  const [colonAlpha, setColonAlpha] = useState(1);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const a = Math.sin(timeRef.current * 4) > 0 ? 1.0 : 0.3;
    setColonAlpha(a);
  });

  const hh  = now.getHours().toString().padStart(2, "0");
  const mm  = now.getMinutes().toString().padStart(2, "0");
  const ss  = now.getSeconds().toString().padStart(2, "0");

  const PX = PANEL_X;
  const PY = PANEL_Y;
  const PW = PANEL_W;

  // Clock section starts at y=135+8=143
  const clockBaseY = PY + 143;

  // Render HH and MM as separate text nodes with a blinking colon in between
  // HH is left-of-center, MM is right-of-center, colon sits at center
  const centerX  = PX + PW / 2;
  const halfW    = 28; // half-width of HH or MM block

  return (
    <>
      {/* HH */}
      <pixiText
        text={hh}
        x={centerX - halfW}
        y={clockBaseY + 10}
        anchor={{ x: 1, y: 0.5 }}
        style={{
          fontFamily: "monospace",
          fontSize: 16,
          fontWeight: "bold",
          fill: GOLD,
        }}
      />

      {/* Blinking colon */}
      <pixiText
        text=":"
        x={centerX}
        y={clockBaseY + 10}
        anchor={0.5}
        alpha={colonAlpha}
        style={{
          fontFamily: "monospace",
          fontSize: 16,
          fontWeight: "bold",
          fill: GOLD,
        }}
      />

      {/* MM */}
      <pixiText
        text={mm}
        x={centerX + halfW}
        y={clockBaseY + 10}
        anchor={{ x: 0, y: 0.5 }}
        style={{
          fontFamily: "monospace",
          fontSize: 16,
          fontWeight: "bold",
          fill: GOLD,
        }}
      />

      {/* Seconds */}
      <pixiText
        text={ss}
        x={centerX}
        y={clockBaseY + 23}
        anchor={0.5}
        style={{
          fontFamily: "monospace",
          fontSize: 9,
          fontWeight: "bold",
          fill: BLUE,
        }}
      />
    </>
  );
}

// ============================================================================
// EXPORT
// ============================================================================

export function CrewPanel(): ReactNode {
  return (
    <pixiContainer>
      <PanelFrame />
      <StaticLabels />
      <AnimatedCrewDots />
      <PulsingStatusText />
      <ClockDisplay />
    </pixiContainer>
  );
}
