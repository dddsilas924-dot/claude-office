/**
 * OfficeBackground — Space Station Theme
 *
 * Renders the SF Mothership Bridge walls (armor plates, conduits, gold trim)
 * and floor (dark metal plates with subtle blue grid lines).
 *
 * No floor tile sprites — fully procedural for the space station aesthetic.
 */

import { type ReactNode, useCallback, useRef } from "react";
import { Graphics } from "pixi.js";
import { useTick } from "@pixi/react";

/** No-op draw so pixiGraphics with ref satisfies the required `draw` prop. */
const noop = (_g: Graphics) => {};
import { CANVAS_WIDTH, CANVAS_HEIGHT } from "@/constants/canvas";
import {
  WALL_HEIGHT,
  TRIM_HEIGHT,
  WALL_BASE,
  WALL_MID,
  FLOOR_BASE,
  FLOOR_DARK,
  GOLD,
  BLUE,
  goldAlpha,
  blueAlpha,
  blackAlpha,
} from "@/constants/spaceTheme";

// ============================================================================
// TYPES
// ============================================================================

// eslint-disable-next-line @typescript-eslint/no-empty-interface
interface OfficeBackgroundProps {
  floorTileTexture?: unknown; // kept for API compat, ignored
}

// ============================================================================
// WALL DRAWING
// ============================================================================

function drawWalls(g: Graphics): void {
  g.clear();

  // ── Wall background ─────────────────────────────────────────────────
  g.rect(0, 0, CANVAS_WIDTH, WALL_HEIGHT);
  g.fill(WALL_BASE);

  // Slightly lighter mid-band
  g.rect(0, WALL_HEIGHT * 0.3, CANVAS_WIDTH, WALL_HEIGHT * 0.4);
  g.fill({ color: WALL_MID, alpha: 0.4 });

  // ── Armor plates ────────────────────────────────────────────────────
  for (let py = 4; py < WALL_HEIGHT - 16; py += 86) {
    for (let px = 4; px < CANVAS_WIDTH; px += 160) {
      // Plate body
      g.roundRect(px + 2, py + 2, 156, 82, 3);
      g.fill({ color: 0x0e1e30, alpha: 0.5 });

      // Top edge highlight
      g.moveTo(px + 6, py + 2);
      g.lineTo(px + 154, py + 2);
      g.stroke({ width: 0.5, color: BLUE, alpha: 0.04 });

      // Bottom edge shadow
      g.moveTo(px + 6, py + 84);
      g.lineTo(px + 154, py + 84);
      g.stroke({ width: 0.5, color: 0x000000, alpha: 0.15 });

      // Corner bolts
      const bolts = [
        [px + 10, py + 10],
        [px + 150, py + 10],
        [px + 10, py + 76],
        [px + 150, py + 76],
      ];
      for (const [bx, by] of bolts) {
        g.circle(bx, by, 3);
        g.fill(goldAlpha(0.1));
        g.circle(bx + 0.5, by + 0.5, 1.5);
        g.fill(blackAlpha(0.25));
        g.circle(bx, by, 4);
        g.stroke({ width: 0.3, color: GOLD, alpha: 0.06 });
      }
    }
  }

  // ── Cable conduits (horizontal) ─────────────────────────────────────
  for (const cy of [WALL_HEIGHT * 0.33, WALL_HEIGHT * 0.66]) {
    g.rect(0, cy, CANVAS_WIDTH, 7);
    g.fill(blackAlpha(0.25));

    g.moveTo(0, cy);
    g.lineTo(CANVAS_WIDTH, cy);
    g.stroke({ width: 0.5, color: 0x283c50, alpha: 0.15 });

    g.moveTo(0, cy + 7);
    g.lineTo(CANVAS_WIDTH, cy + 7);
    g.stroke({ width: 0.5, color: 0x283c50, alpha: 0.15 });

    // Junction boxes
    for (let cx = 30; cx < CANVAS_WIDTH; cx += 100) {
      g.rect(cx, cy - 1, 14, 9);
      g.fill(goldAlpha(0.07));
    }
  }

  // ── Gold trim strip (bottom of wall) ────────────────────────────────
  g.rect(0, WALL_HEIGHT - TRIM_HEIGHT, CANVAS_WIDTH, TRIM_HEIGHT);
  g.fill(0x0b1828);

  // Trim blocks
  for (let tx = 0; tx < CANVAS_WIDTH; tx += 32) {
    g.rect(tx + 2, WALL_HEIGHT - 14, 12, 12);
    g.fill(goldAlpha(0.035));
  }

  // Gold line at wall/floor boundary
  g.moveTo(0, WALL_HEIGHT);
  g.lineTo(CANVAS_WIDTH, WALL_HEIGHT);
  g.stroke({ width: 2.5, color: GOLD, alpha: 0.5 });

  // ── Floor ───────────────────────────────────────────────────────────
  g.rect(0, WALL_HEIGHT, CANVAS_WIDTH, CANVAS_HEIGHT - WALL_HEIGHT);
  g.fill(FLOOR_BASE);

  // Subtle floor darkening at bottom
  g.rect(0, CANVAS_HEIGHT - 200, CANVAS_WIDTH, 200);
  g.fill({ color: FLOOR_DARK, alpha: 0.4 });

  // Floor plate grid
  for (let fy = WALL_HEIGHT; fy < CANVAS_HEIGHT; fy += 80) {
    for (let fx = 0; fx < CANVAS_WIDTH; fx += 80) {
      g.rect(fx + 1, fy + 1, 78, 78);
      g.stroke({ width: 0.4, color: BLUE, alpha: 0.02 });

      // Sparse highlight tiles
      if (
        (Math.floor(fx / 80) + Math.floor((fy - WALL_HEIGHT) / 80)) % 5 ===
        0
      ) {
        g.rect(fx + 1, fy + 1, 78, 78);
        g.fill(blueAlpha(0.008));
      }
    }
  }

  // Edge emission lines (left/right)
  for (const ex of [65, CANVAS_WIDTH - 65]) {
    g.moveTo(ex, WALL_HEIGHT + 4);
    g.lineTo(ex, CANVAS_HEIGHT - 4);
    g.stroke({ width: 1.5, color: BLUE, alpha: 0.1 });
  }

  // ── Status bar background (bottom 30px) ─────────────────────────────
  g.rect(0, CANVAS_HEIGHT - 30, CANVAS_WIDTH, 30);
  g.fill(0x081420);

  g.moveTo(0, CANVAS_HEIGHT - 30);
  g.lineTo(CANVAS_WIDTH, CANVAS_HEIGHT - 30);
  g.stroke({ width: 1, color: GOLD, alpha: 0.25 });
}

// ============================================================================
// ANIMATED RUNNING LIGHTS (on gold trim)
// ============================================================================

function RunningLights(): ReactNode {
  const gRef = useRef<Graphics>(null);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const g = gRef.current;
    if (!g) return;

    g.clear();
    const t = timeRef.current;

    // Gold running light (left → right)
    const x1 = (t * 100) % CANVAS_WIDTH;
    g.circle(x1, WALL_HEIGHT - 8, 4);
    g.fill(goldAlpha(0.6));

    // Blue running light (right → left)
    const x2 = CANVAS_WIDTH - ((t * 70) % CANVAS_WIDTH);
    g.circle(x2, WALL_HEIGHT - 8, 3);
    g.fill(blueAlpha(0.4));
  });

  return <pixiGraphics ref={gRef} draw={noop} />;
}

// ============================================================================
// ANIMATED VENT GRILLES
// ============================================================================

function VentGrilles(): ReactNode {
  const gRef = useRef<Graphics>(null);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const g = gRef.current;
    if (!g) return;

    g.clear();
    const t = timeRef.current;

    for (let vx = 200; vx < CANVAS_WIDTH - 100; vx += 280) {
      // Vent body
      g.roundRect(vx, 6, 90, 20, 2);
      g.fill(blackAlpha(0.5));

      // Animated slats
      for (let s = 0; s < 9; s++) {
        const va = 0.04 + Math.sin(t * 1.8 + s * 0.6 + vx * 0.01) * 0.025;
        g.rect(vx + 4 + s * 10, 9, 6, 14);
        g.fill(blueAlpha(va));
      }

      // Border
      g.roundRect(vx, 6, 90, 20, 2);
      g.stroke({ width: 0.5, color: BLUE, alpha: 0.08 });
    }
  });

  return <pixiGraphics ref={gRef} draw={noop} />;
}

// ============================================================================
// GOLD GUIDE PATHS (floor dashed lines)
// ============================================================================

function drawGuidePaths(g: Graphics): void {
  g.clear();

  const W = CANVAS_WIDTH;
  const WH = WALL_HEIGHT;
  const H = CANVAS_HEIGHT;

  // Center vertical
  for (let y = WH + 12; y < H - 36; y += 24) {
    g.rect(W / 2 - 0.4, y, 0.8, 14);
    g.fill(goldAlpha(0.07));
  }

  // Horizontal guide lines
  for (const ay of [WH + 145, WH + 310]) {
    for (let x = 100; x < W - 100; x += 24) {
      g.rect(x, ay - 0.4, 14, 0.8);
      g.fill(goldAlpha(0.07));
    }
  }
}

// ============================================================================
// SCROLLING STATUS BAR TEXT
// ============================================================================

function StatusBar(): ReactNode {
  const gRef = useRef<Graphics>(null);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
  });

  // The text is rendered via pixiText; the bar bg is in drawWalls
  return null;
}

// ============================================================================
// COMPONENT
// ============================================================================

export function OfficeBackground(
  _props: OfficeBackgroundProps,
): ReactNode {
  const drawWallsCallback = useCallback((g: Graphics) => drawWalls(g), []);
  const drawGuidePathsCallback = useCallback(
    (g: Graphics) => drawGuidePaths(g),
    [],
  );

  return (
    <>
      {/* Static walls + floor */}
      <pixiGraphics draw={drawWallsCallback} />

      {/* Guide paths on floor */}
      <pixiGraphics draw={drawGuidePathsCallback} />

      {/* Animated elements */}
      <VentGrilles />
      <RunningLights />
    </>
  );
}
