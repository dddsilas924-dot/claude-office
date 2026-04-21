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

  const dw = 130;
  const dh = 44;
  const dx = -dw / 2;
  const dy = 26;
  const r = 4;

  // Shadow/depth layer (offset down-right for 3D effect)
  g.roundRect(dx + 2, dy + 3, dw, dh, r);
  g.fill({ color: 0x020810, alpha: 0.6 });

  // Side panel thickness (front edge — gives depth illusion)
  g.roundRect(dx, dy + dh - 4, dw, 8, 2);
  g.fill({ color: 0x071018, alpha: 1 });
  g.stroke({ width: 0.5, color: GOLD, alpha: 0.12 });

  // Main desk surface
  g.roundRect(dx, dy, dw, dh, r);
  g.fill({ color: 0x0c1e30, alpha: 1 });
  g.stroke({ width: 1.5, color: GOLD, alpha: 0.3 });

  // Inner border (blue accent like other panels)
  g.roundRect(dx + 3, dy + 3, dw - 6, dh - 6, 2);
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.2 });

  // Top-edge highlight (bright gold trim)
  g.rect(dx + 8, dy, dw - 16, 1.5);
  g.fill({ color: GOLD, alpha: 0.25 });

  // Corner bolts (4 corners, matching station aesthetic)
  const boltInset = 7;
  const bolts: [number, number][] = [
    [dx + boltInset, dy + boltInset],
    [dx + dw - boltInset, dy + boltInset],
    [dx + boltInset, dy + dh - boltInset],
    [dx + dw - boltInset, dy + dh - boltInset],
  ];
  for (const [bx, by] of bolts) {
    g.circle(bx, by, 2);
    g.fill({ color: 0x1a3050, alpha: 1 });
    g.stroke({ width: 0.6, color: GOLD, alpha: 0.4 });
  }

  // Keyboard recess (centered, slightly recessed)
  const kw = 44;
  const kh = 14;
  g.roundRect(-kw / 2, dy + 14, kw, kh, 2);
  g.fill({ color: 0x060e1a, alpha: 1 });
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.25 });

  // Keyboard key rows (tiny lines for detail)
  for (let row = 0; row < 3; row++) {
    const ky = dy + 17 + row * 4;
    g.rect(-kw / 2 + 4, ky, kw - 8, 1);
    g.fill({ color: BLUE, alpha: 0.08 });
  }

  // Desk support legs (visible below desk for depth)
  g.rect(-dw / 2 + 10, dy + dh + 2, 6, 10);
  g.fill({ color: 0x081420, alpha: 0.8 });
  g.stroke({ width: 0.3, color: GOLD, alpha: 0.1 });
  g.rect(dw / 2 - 16, dy + dh + 2, 6, 10);
  g.fill({ color: 0x081420, alpha: 0.8 });
  g.stroke({ width: 0.3, color: GOLD, alpha: 0.1 });
}

/**
 * Returns a draw function for the holographic monitor + LED indicators.
 * Varies LED color by desk index.
 */
function makeMonitorDraw(deskIndex: number) {
  return function drawMonitor(g: Graphics): void {
    g.clear();

    // --- Monitor ---
    const mx = -70;
    const my = 6;
    const mw = 54;
    const mh = 38;
    const mr = 3;

    // Monitor stand (thin post from desk to screen)
    g.rect(mx + mw / 2 - 2, my + mh, 4, 8);
    g.fill({ color: 0x081420, alpha: 1 });
    g.stroke({ width: 0.3, color: GOLD, alpha: 0.15 });

    // Monitor stand base
    g.roundRect(mx + mw / 2 - 10, my + mh + 6, 20, 4, 2);
    g.fill({ color: 0x0a1828, alpha: 1 });
    g.stroke({ width: 0.5, color: GOLD, alpha: 0.1 });

    // Monitor shadow
    g.roundRect(mx + 2, my + 2, mw, mh, mr);
    g.fill({ color: 0x010408, alpha: 0.4 });

    // Monitor frame (outer)
    g.roundRect(mx, my, mw, mh, mr);
    g.fill({ color: 0x0a1520, alpha: 1 });
    g.stroke({ width: 1.5, color: GOLD, alpha: 0.25 });

    // Screen area (inner glow)
    const ig = 4;
    g.roundRect(mx + ig, my + ig, mw - ig * 2, mh - ig * 2, 2);
    g.fill({ color: 0x020810, alpha: 1 });
    g.stroke({ width: 0.5, color: BLUE, alpha: 0.35 });

    // Screen content glow
    g.roundRect(mx + ig + 2, my + ig + 2, mw - ig * 2 - 4, mh - ig * 2 - 4, 1);
    g.fill({ color: BLUE, alpha: 0.04 });

    // Scan lines (subtle horizontal lines for CRT effect)
    for (let sy = my + ig + 3; sy < my + mh - ig - 2; sy += 3) {
      g.rect(mx + ig + 2, sy, mw - ig * 2 - 4, 0.5);
      g.fill({ color: BLUE, alpha: 0.03 });
    }

    // --- LED indicator dots (right side of console) ---
    const ledX = 48;
    const ledStartY = 30;
    const ledSpacing = 8;
    const ledColors: readonly number[] = LED_COLORS;

    for (let dot = 0; dot < 3; dot++) {
      const color = ledColors[(deskIndex + dot) % ledColors.length];
      const isActive = dot === 0;
      // LED glow halo
      if (isActive) {
        g.circle(ledX, ledStartY + dot * ledSpacing, 4);
        g.fill({ color, alpha: 0.15 });
      }
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
