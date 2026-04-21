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

  // Shadow (depth)
  g.roundRect(-14, 42, 28, 6, 3);
  g.fill({ color: 0x010408, alpha: 0.4 });

  // Chair base (5-star caster base)
  g.roundRect(-16, 40, 32, 5, 2);
  g.fill({ color: 0x0a1828, alpha: 1 });
  g.stroke({ width: 0.5, color: GOLD, alpha: 0.15 });
  // Caster dots
  g.circle(-14, 43, 1.5);
  g.fill({ color: 0x1a3050, alpha: 1 });
  g.circle(14, 43, 1.5);
  g.fill({ color: 0x1a3050, alpha: 1 });
  g.circle(0, 44, 1.5);
  g.fill({ color: 0x1a3050, alpha: 1 });

  // Chair stem (hydraulic post)
  g.rect(-2, 34, 4, 8);
  g.fill({ color: 0x081420, alpha: 1 });
  g.stroke({ width: 0.3, color: BLUE, alpha: 0.15 });

  // Seat frame (outer)
  g.roundRect(-18, 22, 36, 16, 5);
  g.fill({ color: 0x0c1e30, alpha: 1 });
  g.stroke({ width: 1, color: GOLD, alpha: 0.25 });

  // Seat cushion (inner, lighter)
  g.roundRect(-14, 24, 28, 11, 3);
  g.fill({ color: 0x122840, alpha: 1 });
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.15 });

  // Seat highlight (top edge — gives rounded 3D feel)
  g.rect(-12, 24, 24, 1.5);
  g.fill({ color: BLUE, alpha: 0.08 });

  // Armrests
  g.roundRect(-20, 24, 5, 12, 2);
  g.fill({ color: 0x0a1828, alpha: 1 });
  g.stroke({ width: 0.5, color: GOLD, alpha: 0.15 });
  g.roundRect(15, 24, 5, 12, 2);
  g.fill({ color: 0x0a1828, alpha: 1 });
  g.stroke({ width: 0.5, color: GOLD, alpha: 0.15 });
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
