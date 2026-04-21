/**
 * DeskGrid Components
 *
 * Renders the desk grid with:
 * - Procedural dark-metal command console surfaces (DeskSurfacesBase — behind agent arms)
 * - Procedural holographic monitors + LED indicators (DeskSurfacesTop — in front of agent arms)
 * - Task marquees on occupied desks
 */

import { type ReactNode, useMemo, useCallback } from "react";
import type { Graphics } from "pixi.js";
import { DeskMarquee } from "./DeskMarquee";
import { GOLD, BLUE, GREEN } from "@/constants/spaceTheme";

// ============================================================================
// TYPES
// ============================================================================

export interface DeskPosition {
  x: number;
  y: number;
  isEmpty: boolean;
}

// ============================================================================
// CONSTANTS
// ============================================================================

// Desk grid layout (unchanged)
const ROW_SIZE = 4;
const DESK_START_X = 256;
const DESK_START_Y = 408;
const DESK_SPACING_X = 256;
const DESK_SPACING_Y = 192;

// LED indicator colors cycling across desks
const LED_COLORS = [GREEN, BLUE, GOLD] as const;

// ============================================================================
// HOOKS
// ============================================================================

/**
 * Hook to compute desk positions based on desk count and occupancy.
 */
export function useDeskPositions(
  deskCount: number,
  occupiedDesks: Set<number>,
): DeskPosition[] {
  return useMemo(() => {
    const result: DeskPosition[] = [];

    for (let i = 0; i < deskCount; i++) {
      const row = Math.floor(i / ROW_SIZE);
      const col = i % ROW_SIZE;
      const x = DESK_START_X + col * DESK_SPACING_X;
      const y = DESK_START_Y + row * DESK_SPACING_Y;
      const deskNum = i + 1;
      const isEmpty = !occupiedDesks.has(deskNum);

      result.push({ x, y, isEmpty });
    }

    return result;
  }, [deskCount, occupiedDesks]);
}

// ============================================================================
// DRAW HELPERS
// ============================================================================

/**
 * Draws the dark-metal console desk surface + keyboard area.
 */
function drawConsoleSurface(g: Graphics): void {
  g.clear();

  // --- Desk surface: dark metal panel ---
  const dw = 120;
  const dh = 40;
  const dx = -dw / 2;
  const dy = 30;
  const r = 4;

  g.roundRect(dx, dy, dw, dh, r);
  g.fill({ color: 0x0a1520, alpha: 0.9 });
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.3 });

  // Top-edge highlight (thin line across the top)
  g.moveTo(dx + r, dy);
  g.lineTo(dx + dw - r, dy);
  g.stroke({ width: 1, color: BLUE, alpha: 0.1 });

  // --- Keyboard area: small recessed panel ---
  const kw = 40;
  const kh = 12;
  g.roundRect(-kw / 2, 42, kw, kh, 2);
  g.fill({ color: 0x060e1a, alpha: 1.0 });
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.2 });
}

/**
 * Returns a draw function for the holographic monitor + LED indicators.
 * Varies LED color by desk index.
 */
function makeMonitorDraw(deskIndex: number) {
  return function drawMonitor(g: Graphics): void {
    g.clear();

    // --- Monitor frame ---
    const mx = -70;
    const my = 10;
    const mw = 50;
    const mh = 35;
    const mr = 3;

    g.roundRect(mx, my, mw, mh, mr);
    g.fill({ color: 0x020810, alpha: 1.0 });
    g.stroke({ width: 1, color: BLUE, alpha: 0.4 });

    // Screen inner glow (slightly inset)
    const ig = 3;
    g.roundRect(mx + ig, my + ig, mw - ig * 2, mh - ig * 2, 2);
    g.fill({ color: BLUE, alpha: 0.03 });

    // --- LED indicator dots (3 stacked, right side of console) ---
    const ledX = 45;
    const ledStartY = 32;
    const ledSpacing = 7;
    const ledColors: readonly number[] = LED_COLORS;

    for (let dot = 0; dot < 3; dot++) {
      const color = ledColors[(deskIndex + dot) % ledColors.length];
      const isActive = dot === 0; // first dot always "on"
      g.circle(ledX, ledStartY + dot * ledSpacing, 2);
      g.fill({ color, alpha: isActive ? 0.9 : 0.25 });
    }
  };
}

// ============================================================================
// COMPONENTS
// ============================================================================

interface DeskSurfacesBaseProps {
  deskCount: number;
  occupiedDesks: Set<number>;
  // Sprite texture props are intentionally ignored — consoles are procedural.
  [key: string]: unknown;
}

/**
 * Renders procedural command-console desk surfaces (behind agent arms).
 */
export function DeskSurfacesBase({
  deskCount,
  occupiedDesks,
}: DeskSurfacesBaseProps): ReactNode {
  const desks = useDeskPositions(deskCount, occupiedDesks);

  return (
    <>
      {desks.map((desk, i) => (
        <pixiContainer key={i} x={desk.x} y={desk.y}>
          <ConsoleBase />
        </pixiContainer>
      ))}
    </>
  );
}

/** Memoised static console-surface graphics for a single desk. */
function ConsoleBase(): ReactNode {
  // draw is stable — drawConsoleSurface is a module-level function reference
  const draw = useCallback(drawConsoleSurface, []);
  return <pixiGraphics draw={draw} />;
}

interface DeskSurfacesTopProps {
  deskCount: number;
  occupiedDesks: Set<number>;
  deskTasks: Map<number, string>;
  // Sprite texture props are intentionally ignored — consoles are procedural.
  [key: string]: unknown;
}

/**
 * Renders procedural holographic monitors + LED indicators (in front of agent arms).
 */
export function DeskSurfacesTop({
  deskCount,
  occupiedDesks,
  deskTasks,
}: DeskSurfacesTopProps): ReactNode {
  const desks = useDeskPositions(deskCount, occupiedDesks);

  return (
    <>
      {desks.map((desk, i) => (
        <pixiContainer key={i} x={desk.x} y={desk.y}>
          <ConsoleTop deskIndex={i} />
          {/* Task marquee — only visible on occupied desks */}
          <DeskMarquee text={deskTasks.get(i + 1) ?? ""} />
        </pixiContainer>
      ))}
    </>
  );
}

interface ConsoleTopProps {
  deskIndex: number;
}

/** Memoised static monitor graphics for a single desk. */
function ConsoleTop({ deskIndex }: ConsoleTopProps): ReactNode {
  const draw = useCallback(makeMonitorDraw(deskIndex), [deskIndex]);
  return <pixiGraphics draw={draw} />;
}
