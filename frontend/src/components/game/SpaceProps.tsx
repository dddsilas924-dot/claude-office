/**
 * SpaceProps — Procedural space station props (chair, bio-pod, etc.)
 *
 * Replaces old office sprite props (chair, plant) with procedural PixiJS Graphics.
 */

import { type ReactNode, useCallback } from "react";
import type { Graphics } from "pixi.js";
import { GOLD, BLUE, GREEN } from "@/constants/spaceTheme";

// ============================================================================
// COMMAND CHAIR
// ============================================================================

function drawCommandChair(g: Graphics): void {
  g.clear();

  // Seat base — dark metal oval
  g.roundRect(-16, 24, 32, 18, 6);
  g.fill({ color: 0x0a1520, alpha: 0.9 });
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.2 });

  // Seat cushion — slightly lighter
  g.roundRect(-12, 26, 24, 12, 4);
  g.fill({ color: 0x0e1e30, alpha: 0.8 });

  // Chair stem
  g.rect(-2, 36, 4, 6);
  g.fill({ color: 0x081420, alpha: 0.9 });

  // Base — small star pattern
  g.roundRect(-14, 40, 28, 5, 2);
  g.fill({ color: 0x081420, alpha: 0.8 });
  g.stroke({ width: 0.3, color: GOLD, alpha: 0.1 });
}

export function CommandChair(): ReactNode {
  const draw = useCallback(drawCommandChair, []);
  return <pixiGraphics draw={draw} />;
}

// ============================================================================
// BIO-POD (replaces plant)
// ============================================================================

function drawBioPod(g: Graphics): void {
  g.clear();

  // Pod container — cylindrical dark shell
  g.roundRect(-14, -30, 28, 36, 6);
  g.fill({ color: 0x060e1a, alpha: 0.9 });
  g.stroke({ width: 0.8, color: BLUE, alpha: 0.25 });

  // Glass window — showing green glow inside
  g.roundRect(-9, -24, 18, 20, 3);
  g.fill({ color: GREEN, alpha: 0.06 });
  g.stroke({ width: 0.5, color: GREEN, alpha: 0.2 });

  // Bio-matter glow dot
  g.circle(0, -14, 4);
  g.fill({ color: GREEN, alpha: 0.15 });
  g.circle(0, -14, 2);
  g.fill({ color: GREEN, alpha: 0.35 });

  // Base ring
  g.roundRect(-16, 4, 32, 6, 2);
  g.fill({ color: 0x081420, alpha: 0.9 });
  g.stroke({ width: 0.3, color: GOLD, alpha: 0.08 });

  // Status LED
  g.circle(10, -2, 1.5);
  g.fill({ color: GREEN, alpha: 0.8 });
}

export function BioPod({
  x,
  y,
}: {
  x: number;
  y: number;
}): ReactNode {
  const draw = useCallback(drawBioPod, []);
  return (
    <pixiContainer x={x} y={y}>
      <pixiGraphics draw={draw} />
    </pixiContainer>
  );
}
