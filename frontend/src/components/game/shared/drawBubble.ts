/**
 * Shared Bubble Drawing Utility
 *
 * Common bubble drawing function used by both BossSprite and AgentSprite.
 * Draws a thought or speech bubble with shadow, main body, and tail.
 */

import { Graphics } from "pixi.js";

/**
 * Draws a thought or speech bubble on a PIXI Graphics object.
 *
 * @param g - The PIXI Graphics instance to draw on
 * @param width - Width of the bubble in pixels
 * @param height - Height of the bubble in pixels
 * @param type - Bubble type: "thought" renders rounded dots, "speech" renders a triangular tail
 * @param tailSide - Which side the tail points: "left" (default) or "right"
 */
export function drawBubble(
  g: Graphics,
  width: number,
  height: number,
  type: "thought" | "speech" = "thought",
  tailSide: "left" | "right" = "left",
): void {
  g.clear();

  const halfW = width / 2;
  const radius = type === "thought" ? 20 : 12;
  const shadowOff = 2;
  const shadowAlpha = 0.2;
  // Mirror multiplier: 1 for left (original), -1 for right
  const m = tailSide === "left" ? 1 : -1;

  // Shadow pass
  if (type === "thought") {
    g.circle(-10 * m + shadowOff, 6 + shadowOff, 4);
    g.fill({ color: 0x000000, alpha: shadowAlpha });
    g.circle(-20 * m + shadowOff, 14 + shadowOff, 2);
    g.fill({ color: 0x000000, alpha: shadowAlpha });
  } else {
    g.moveTo(-15 * m + shadowOff, 0 + shadowOff);
    g.lineTo(-20 * m + shadowOff, 12 + shadowOff);
    g.lineTo(-5 * m + shadowOff, 0 + shadowOff);
    g.closePath();
    g.fill({ color: 0x000000, alpha: shadowAlpha });
  }
  g.roundRect(-halfW + shadowOff, -height + shadowOff, width, height, radius);
  g.fill({ color: 0x000000, alpha: shadowAlpha });

  // Main bubble
  g.roundRect(-halfW, -height, width, height, radius);
  g.fill(0xffffff);
  g.stroke({ width: 1.5, color: 0x000000 });

  // Tail (drawn after bubble)
  if (type === "thought") {
    g.circle(-10 * m, 6, 4);
    g.fill(0xffffff);
    g.stroke({ width: 1.5, color: 0x000000 });
    g.circle(-20 * m, 14, 2);
    g.fill(0xffffff);
    g.stroke({ width: 1, color: 0x000000 });
  } else {
    // Speech tail - fill extends into bubble to cover the stroke
    g.moveTo(-15 * m, -2);
    g.lineTo(-20 * m, 12);
    g.lineTo(-5 * m, -2);
    g.closePath();
    g.fill(0xffffff);
    // Stroke only the outer V edges
    g.moveTo(-15 * m, 0);
    g.lineTo(-20 * m, 12);
    g.lineTo(-5 * m, 0);
    g.stroke({ width: 1.5, color: 0x000000 });
  }
}

/**
 * Draws a circular badge background for an icon overlay.
 *
 * @param g - The PIXI Graphics instance to draw on
 * @param radius - Radius of the circular badge in pixels
 */
export function drawIconBadge(g: Graphics, radius: number): void {
  g.clear();
  // Shadow
  g.circle(1, 1, radius);
  g.fill({ color: 0x000000, alpha: 0.2 });
  // White background
  g.circle(0, 0, radius);
  g.fill(0xffffff);
  g.stroke({ width: 1.5, color: 0x000000 });
}
