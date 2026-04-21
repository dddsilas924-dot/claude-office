"use client";

/**
 * TitleBanner — "MR.D AI OFFICE" space-station command bridge header panel
 *
 * Positioned at the top of the wall (x=230, y=4, w=810, h=48).
 * Features a dark panel with gold/blue borders, decorative corner brackets,
 * side accent lines with a diamond ornament, centered title text, subtitle,
 * and an animated scan line that sweeps left-to-right.
 */

import { Graphics, TextStyle } from "pixi.js";
import { useCallback, useRef, type ReactNode } from "react";
import { useTick } from "@pixi/react";
import {
  BANNER_X,
  BANNER_Y,
  BANNER_W,
  BANNER_H,
  GOLD,
  BLUE,
  goldAlpha,
  blueAlpha,
  blackAlpha,
} from "@/constants/spaceTheme";

// ============================================================================
// CONSTANTS
// ============================================================================

/** Corner bracket arm length in pixels */
const CORNER_ARM = 14;

/** Width of the center text zone — brackets + side ornaments occupy the wings */
const SIDE_ORNAMENT_W = 110;

// ============================================================================
// STATIC PANEL DRAW
// ============================================================================

function drawStaticPanel(g: Graphics): void {
  g.clear();

  const x = BANNER_X;
  const y = BANNER_Y;
  const w = BANNER_W;
  const h = BANNER_H;

  // ── Background ──────────────────────────────────────────────────────────
  // Outermost glow halo (gold, very faint)
  g.roundRect(x - 2, y - 2, w + 4, h + 4, 4);
  g.fill(goldAlpha(0.08));

  // Main dark panel body
  g.roundRect(x, y, w, h, 2);
  g.fill(blackAlpha(0.82));

  // Subtle inner highlight stripe at top (gives a subtle gradient feel)
  g.rect(x + 2, y + 1, w - 4, 6);
  g.fill({ color: BLUE, alpha: 0.04 });

  // ── Outer gold border (glow simulation via stacked strokes) ──────────────
  g.roundRect(x, y, w, h, 2);
  g.stroke({ width: 3, color: GOLD, alpha: 0.18 }); // outer soft glow

  g.roundRect(x + 1, y + 1, w - 2, h - 2, 2);
  g.stroke({ width: 1.5, color: GOLD, alpha: 0.55 }); // crisp inner line

  // ── Inner blue border ────────────────────────────────────────────────────
  g.roundRect(x + 4, y + 4, w - 8, h - 8, 1);
  g.stroke({ width: 1, color: BLUE, alpha: 0.45 });

  // ── Corner brackets (L-shaped, gold) — all 4 corners ────────────────────
  const corners: [number, number, number, number][] = [
    [x + 2, y + 2, 1, 1],   // top-left
    [x + w - 2, y + 2, -1, 1],   // top-right
    [x + 2, y + h - 2, 1, -1],   // bottom-left
    [x + w - 2, y + h - 2, -1, -1], // bottom-right
  ];

  for (const [cx, cy, dx, dy] of corners) {
    // Horizontal arm
    g.moveTo(cx, cy);
    g.lineTo(cx + dx * CORNER_ARM, cy);
    g.stroke({ width: 2, color: GOLD, alpha: 0.9 });

    // Vertical arm
    g.moveTo(cx, cy);
    g.lineTo(cx, cy + dy * CORNER_ARM);
    g.stroke({ width: 2, color: GOLD, alpha: 0.9 });
  }

  // ── Left side ornament: accent lines + diamond ───────────────────────────
  const leftOrnX = x + 22;
  const midY = y + h / 2;

  // Horizontal accent line (left wing)
  g.moveTo(leftOrnX, midY);
  g.lineTo(leftOrnX + SIDE_ORNAMENT_W - 24, midY);
  g.stroke({ width: 1, color: GOLD, alpha: 0.55 });

  // Upper thin accent line
  g.moveTo(leftOrnX + 8, midY - 6);
  g.lineTo(leftOrnX + SIDE_ORNAMENT_W - 24, midY - 6);
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.4 });

  // Lower thin accent line
  g.moveTo(leftOrnX + 8, midY + 6);
  g.lineTo(leftOrnX + SIDE_ORNAMENT_W - 24, midY + 6);
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.4 });

  // Diamond ornament (left)
  const ldx = leftOrnX + 4;
  const ldy = midY;
  const ds = 5; // half-size
  g.moveTo(ldx, ldy - ds);
  g.lineTo(ldx + ds, ldy);
  g.lineTo(ldx, ldy + ds);
  g.lineTo(ldx - ds, ldy);
  g.lineTo(ldx, ldy - ds);
  g.fill(GOLD);

  // ── Right side ornament: mirror of left ──────────────────────────────────
  const rightOrnX = x + w - 22;

  // Horizontal accent line (right wing)
  g.moveTo(rightOrnX, midY);
  g.lineTo(rightOrnX - SIDE_ORNAMENT_W + 24, midY);
  g.stroke({ width: 1, color: GOLD, alpha: 0.55 });

  // Upper thin accent line
  g.moveTo(rightOrnX - 8, midY - 6);
  g.lineTo(rightOrnX - SIDE_ORNAMENT_W + 24, midY - 6);
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.4 });

  // Lower thin accent line
  g.moveTo(rightOrnX - 8, midY + 6);
  g.lineTo(rightOrnX - SIDE_ORNAMENT_W + 24, midY + 6);
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.4 });

  // Diamond ornament (right)
  const rdx = rightOrnX - 4;
  const rdy = midY;
  g.moveTo(rdx, rdy - ds);
  g.lineTo(rdx + ds, rdy);
  g.lineTo(rdx, rdy + ds);
  g.lineTo(rdx - ds, rdy);
  g.lineTo(rdx, rdy - ds);
  g.fill(GOLD);

  // ── Vertical separator ticks at text zone edges ──────────────────────────
  const leftTick = x + SIDE_ORNAMENT_W + 8;
  const rightTick = x + w - SIDE_ORNAMENT_W - 8;

  for (const tx of [leftTick, rightTick]) {
    g.moveTo(tx, y + 6);
    g.lineTo(tx, y + h - 6);
    g.stroke({ width: 0.5, color: GOLD, alpha: 0.3 });
  }
}

// ============================================================================
// ANIMATED SCAN LINE
// ============================================================================

/** No-op draw satisfies the required `draw` prop type while useTick takes over */
const noop = () => {};

function ScanLine(): ReactNode {
  const gRef = useRef<Graphics>(null);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const g = gRef.current;
    if (!g) return;

    g.clear();

    // Scan line cycles across banner width every ~3 seconds
    const period = 3; // seconds per sweep
    const progress = (timeRef.current % period) / period;
    const scanX = BANNER_X + 6 + progress * (BANNER_W - 12);

    // Soft vertical bar: overlapping thin rects simulate a blur glow
    // Outermost (widest, most transparent)
    g.rect(scanX - 4, BANNER_Y + 4, 8, BANNER_H - 8);
    g.fill(blueAlpha(0.03));

    // Mid
    g.rect(scanX - 2, BANNER_Y + 4, 4, BANNER_H - 8);
    g.fill(blueAlpha(0.05));

    // Core (crisp 1px line)
    g.rect(scanX - 0.5, BANNER_Y + 4, 1, BANNER_H - 8);
    g.fill(blueAlpha(0.12));
  });

  return <pixiGraphics ref={gRef} draw={noop} />;
}

// ============================================================================
// TEXT STYLES
// ============================================================================

const TITLE_STYLE = new TextStyle({
  fontFamily: '"Courier New", Courier, monospace',
  fontSize: 22,
  fontWeight: "bold",
  fill: GOLD,
  dropShadow: {
    color: GOLD,
    blur: 6,
    alpha: 0.45,
    distance: 0,
  },
  letterSpacing: 4,
});

const SUBTITLE_STYLE = new TextStyle({
  fontFamily: '"Courier New", Courier, monospace',
  fontSize: 8,
  fontWeight: "bold",
  fill: BLUE,
  dropShadow: {
    color: BLUE,
    blur: 3,
    alpha: 0.5,
    distance: 0,
  },
  letterSpacing: 3,
});

// ============================================================================
// COMPONENT
// ============================================================================

export function TitleBanner(): ReactNode {
  const drawPanel = useCallback((g: Graphics) => {
    drawStaticPanel(g);
  }, []);

  // Banner center for text anchoring
  const centerX = BANNER_X + BANNER_W / 2;
  const centerY = BANNER_Y + BANNER_H / 2;

  return (
    <pixiContainer>
      {/* Static panel: background, borders, brackets, ornaments */}
      <pixiGraphics draw={drawPanel} />

      {/* Main title text — centered */}
      <pixiText
        text={"MR.D  AI  OFFICE"}
        x={centerX}
        y={centerY - 7}
        anchor={{ x: 0.5, y: 0.5 }}
        style={TITLE_STYLE}
      />

      {/* Subtitle text — below title */}
      <pixiText
        text={"COMMAND BRIDGE"}
        x={centerX}
        y={centerY + 12}
        anchor={{ x: 0.5, y: 0.5 }}
        style={SUBTITLE_STYLE}
      />

      {/* Animated scan line — rendered last so it sits above static layers */}
      <ScanLine />
    </pixiContainer>
  );
}
