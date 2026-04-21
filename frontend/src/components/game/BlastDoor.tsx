"use client";

/**
 * BlastDoor Component
 *
 * Animated blast door for the left wall of the space station themed office.
 * Features:
 * - Dark recess frame with gold border
 * - Two door panels (top/bottom) with breathing gap animation
 * - Center gap glow, horizontal ridges, hazard stripes, corner bolts
 * - Central locking mechanism with rotating inner ring
 * - Status LEDs (LOCKED, SEALED, PRESS.) with green pulsing
 * - "MR.D" identity label and bottom authority labels
 */

import type React from "react";
import { type ReactNode, useCallback, useRef } from "react";
import { Container, Graphics } from "pixi.js";
import { useTick } from "@pixi/react";
import {
  DOOR_X,
  DOOR_Y,
  DOOR_W,
  DOOR_H,
  GOLD,
  BLUE,
  GREEN,
  goldAlpha,
  greenAlpha,
  blackAlpha,
  whiteAlpha,
} from "@/constants/spaceTheme";

// No-op draw used for imperative Graphics nodes managed via useTick
function noop(_g: Graphics): void {};

// ============================================================================
// LAYOUT CONSTANTS (all relative to DOOR_X / DOOR_Y)
// ============================================================================

const FRAME_INSET = 6; // inset from door bounds for the recess
const PANEL_INSET = 10; // inset from recess for door panels
const PANEL_W = DOOR_W - PANEL_INSET * 2;

// Lock mechanism center (relative to DOOR_X / DOOR_Y)
const LOCK_CX = DOOR_W / 2;
const LOCK_CY = DOOR_H / 2;

// LED strip x offset from door left edge
const LED_X = PANEL_INSET + 4;
const LED_START_Y = DOOR_H * 0.55;
const LED_GAP = 18;

// ============================================================================
// STATIC FRAME (drawn once via useCallback)
// ============================================================================

function drawFrame(g: Graphics): void {
  g.clear();

  // Dark recess behind door panels
  g.roundRect(FRAME_INSET, FRAME_INSET, DOOR_W - FRAME_INSET * 2, DOOR_H - FRAME_INSET * 2, 3);
  g.fill(blackAlpha(0.85));

  // Gold outer frame border (very subtle)
  g.roundRect(2, 2, DOOR_W - 4, DOOR_H - 4, 4);
  g.stroke({ width: 3, color: GOLD, alpha: 0.25 });

  // Inner frame edge (darker)
  g.roundRect(FRAME_INSET, FRAME_INSET, DOOR_W - FRAME_INSET * 2, DOOR_H - FRAME_INSET * 2, 2);
  g.stroke({ width: 1, color: GOLD, alpha: 0.1 });

  // Side bolt columns — left side
  const boltXL = FRAME_INSET - 2;
  const boltXR = DOOR_W - FRAME_INSET + 2;
  for (let boltY = FRAME_INSET + 12; boltY < DOOR_H - FRAME_INSET; boltY += 22) {
    // Left side bolt
    g.circle(boltXL, boltY, 3);
    g.fill(goldAlpha(0.18));
    g.circle(boltXL, boltY, 1.5);
    g.fill(blackAlpha(0.6));
    g.circle(boltXL, boltY, 3.5);
    g.stroke({ width: 0.4, color: GOLD, alpha: 0.12 });

    // Right side bolt
    g.circle(boltXR, boltY, 3);
    g.fill(goldAlpha(0.18));
    g.circle(boltXR, boltY, 1.5);
    g.fill(blackAlpha(0.6));
    g.circle(boltXR, boltY, 3.5);
    g.stroke({ width: 0.4, color: GOLD, alpha: 0.12 });
  }

  // Heavy corner bolts (4 corners of the full door bounds)
  const corners: [number, number][] = [
    [8, 8],
    [DOOR_W - 8, 8],
    [8, DOOR_H - 8],
    [DOOR_W - 8, DOOR_H - 8],
  ];
  for (const [cx, cy] of corners) {
    // Outer ring
    g.circle(cx, cy, 6);
    g.fill(goldAlpha(0.22));
    // Dark center
    g.circle(cx, cy, 3.5);
    g.fill({ color: 0x010408, alpha: 1 });
    // Inner gold dot
    g.circle(cx, cy, 1.5);
    g.fill(goldAlpha(0.45));
    // Outer stroke
    g.circle(cx, cy, 6.5);
    g.stroke({ width: 0.5, color: GOLD, alpha: 0.18 });
  }

  // Hazard stripes — top edge triangles
  const stripeCount = 6;
  const stripeW = (DOOR_W - PANEL_INSET * 2) / stripeCount;
  for (let si = 0; si < stripeCount; si++) {
    const sx = PANEL_INSET + si * stripeW;
    g.moveTo(sx, FRAME_INSET + 1);
    g.lineTo(sx + stripeW * 0.5, FRAME_INSET + 6);
    g.lineTo(sx + stripeW, FRAME_INSET + 1);
    g.fill(goldAlpha(0.06));
  }

  // Hazard stripes — bottom edge triangles
  for (let si = 0; si < stripeCount; si++) {
    const sx = PANEL_INSET + si * stripeW;
    g.moveTo(sx, DOOR_H - FRAME_INSET - 1);
    g.lineTo(sx + stripeW * 0.5, DOOR_H - FRAME_INSET - 6);
    g.lineTo(sx + stripeW, DOOR_H - FRAME_INSET - 1);
    g.fill(goldAlpha(0.06));
  }
}

// ============================================================================
// STATIC PANELS (drawn once via useCallback — ridges + panel surfaces)
// ============================================================================

function drawPanels(g: Graphics, topH: number, bottomY: number, bottomH: number): void {
  g.clear();

  // ── Top panel surface ────────────────────────────────────────────────
  g.roundRect(PANEL_INSET, FRAME_INSET + 2, PANEL_W, topH, 2);
  g.fill({ color: 0x0a1824, alpha: 1 });
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.06 });

  // Horizontal ridges on top panel (every 14px)
  for (let ry = FRAME_INSET + 2 + 14; ry < FRAME_INSET + 2 + topH - 4; ry += 14) {
    g.moveTo(PANEL_INSET + 4, ry);
    g.lineTo(PANEL_INSET + PANEL_W - 4, ry);
    g.stroke({ width: 0.5, color: BLUE, alpha: 0.045 });
  }

  // ── Bottom panel surface ─────────────────────────────────────────────
  g.roundRect(PANEL_INSET, bottomY, PANEL_W, bottomH, 2);
  g.fill({ color: 0x0a1824, alpha: 1 });
  g.stroke({ width: 0.5, color: BLUE, alpha: 0.06 });

  // Horizontal ridges on bottom panel (every 14px)
  for (let ry = bottomY + 14; ry < bottomY + bottomH - 4; ry += 14) {
    g.moveTo(PANEL_INSET + 4, ry);
    g.lineTo(PANEL_INSET + PANEL_W - 4, ry);
    g.stroke({ width: 0.5, color: BLUE, alpha: 0.045 });
  }

  // ── Lock ring base (static outer rings) ──────────────────────────────
  // Outermost ring
  g.circle(LOCK_CX, LOCK_CY, 20);
  g.fill({ color: 0x060c14, alpha: 1 });
  g.stroke({ width: 1.5, color: GOLD, alpha: 0.22 });

  // Second ring
  g.circle(LOCK_CX, LOCK_CY, 15);
  g.fill({ color: 0x040810, alpha: 1 });
  g.stroke({ width: 1, color: GOLD, alpha: 0.14 });

  // Inner socket
  g.circle(LOCK_CX, LOCK_CY, 9);
  g.fill({ color: 0x020508, alpha: 1 });
  g.stroke({ width: 0.8, color: BLUE, alpha: 0.2 });

  // Locking notch marks at 12/3/6/9 o'clock on outer ring
  const notchAngles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
  for (const na of notchAngles) {
    const nx = LOCK_CX + Math.sin(na) * 20;
    const ny = LOCK_CY - Math.cos(na) * 20;
    g.circle(nx, ny, 2);
    g.fill(goldAlpha(0.25));
  }
}

// ============================================================================
// ANIMATED LAYER — gap glow, rotating ring, LED pulsing, center dot
// ============================================================================

function AnimatedBlastDoor(): ReactNode {
  const gRef = useRef<Graphics>(null);
  const rotRingRef = useRef<Graphics>(null);
  const rotContainerRef = useRef<Container>(null);
  const timeRef = useRef(0);

  // Rotating inner ring: draw tick marks + arc on a Graphics, let the
  // pixiContainer's rotation prop spin it each frame.
  const drawRotatingRing = useCallback((g: Graphics) => {
    g.clear();

    // Ring arc
    g.circle(0, 0, 12);
    g.fill({ color: 0x020508, alpha: 1 });
    g.stroke({ width: 1, color: GOLD, alpha: 0.3 });

    // 8 tick marks evenly spaced
    for (let i = 0; i < 8; i++) {
      const angle = (i * Math.PI * 2) / 8;
      const ix = Math.sin(angle) * 9;
      const iy = -Math.cos(angle) * 9;
      const ox = Math.sin(angle) * 12;
      const oy = -Math.cos(angle) * 12;
      g.moveTo(ix, iy);
      g.lineTo(ox, oy);
      g.stroke({ width: 0.8, color: GOLD, alpha: i % 2 === 0 ? 0.45 : 0.2 });
    }

    // Three notch tabs (asymmetric — so rotation is visible)
    for (let i = 0; i < 3; i++) {
      const angle = (i * Math.PI * 2) / 3 + 0.2;
      const tx = Math.sin(angle) * 11;
      const ty = -Math.cos(angle) * 11;
      g.circle(tx, ty, 1.5);
      g.fill(goldAlpha(0.5));
    }
  }, []);

  // Main animated redraw: gap breathing + center gap glow + LED pulsing + green dot
  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    const g = gRef.current;
    if (!g) return;

    const t = timeRef.current;

    g.clear();

    // ── Gap calculation ────────────────────────────────────────────────
    const gap = 2 + Math.sin(t * 0.5) * 0.5; // 1.5 – 2.5 px breathing
    const halfH = (DOOR_H - FRAME_INSET * 2 - 4) / 2;
    const midY = FRAME_INSET + 2 + halfH;

    // Top panel bottom edge (animated y based on gap)
    const topPanelBottom = midY - gap / 2;

    // ── Center gap glow ────────────────────────────────────────────────
    const glowAlpha = 0.3 + Math.sin(t) * 0.15;

    // Glow band (slightly wider than gap for bloom effect)
    g.rect(PANEL_INSET + 2, topPanelBottom - 1, PANEL_W - 4, gap + 2);
    g.fill(goldAlpha(glowAlpha * 0.35));

    // Tight center line
    g.rect(PANEL_INSET + 2, topPanelBottom, PANEL_W - 4, gap);
    g.fill(goldAlpha(glowAlpha));

    // Bright center sliver
    g.rect(PANEL_INSET + 4, topPanelBottom + gap * 0.3, PANEL_W - 8, gap * 0.4);
    g.fill({ color: 0xfffce0, alpha: glowAlpha * 0.4 });

    // ── Panel gap mask (re-paint panel overlap area if gap changed) ────
    // Cover the overlap area between panels (in case they shift)
    g.rect(PANEL_INSET, FRAME_INSET + 2, PANEL_W, topPanelBottom - FRAME_INSET - 2);
    g.fill({ color: 0, alpha: 0 }); // transparent — just to clear stale gap

    // ── Center pulsing green dot ───────────────────────────────────────
    const dotAlpha = 0.7 + Math.sin(t * 2.1) * 0.3;
    const dotR = 3.5 + Math.sin(t * 1.8) * 0.5;

    // Outer glow halo
    g.circle(LOCK_CX, LOCK_CY, dotR + 3);
    g.fill(greenAlpha(dotAlpha * 0.25));
    // Core dot
    g.circle(LOCK_CX, LOCK_CY, dotR);
    g.fill(greenAlpha(dotAlpha));
    // Bright center
    g.circle(LOCK_CX, LOCK_CY, 1.5);
    g.fill(whiteAlpha(dotAlpha * 0.6));

    // ── Status LEDs ────────────────────────────────────────────────────
    for (let li = 0; li < 3; li++) {
      const ly = LED_START_Y + li * LED_GAP;

      // Individual pulse offset per LED
      const ledPhase = t * 1.4 + li * 0.9;
      const ledAlpha = 0.5 + Math.sin(ledPhase) * 0.35;
      const ledR = 2.5 + Math.sin(ledPhase * 0.8) * 0.5;

      // Outer glow
      g.circle(LED_X + 4, ly, ledR + 2.5);
      g.fill(greenAlpha(ledAlpha * 0.2));
      // LED dot
      g.circle(LED_X + 4, ly, ledR);
      g.fill(greenAlpha(ledAlpha));
      // Bright center
      g.circle(LED_X + 4, ly, 1);
      g.fill(whiteAlpha(ledAlpha * 0.5));

      // LED label text — rendered as graphics characters (tiny line art)
      // (actual text is handled by pixiText siblings below)
    }
  });

  // Rotate the inner ring container each tick
  useTick((ticker) => {
    const container = rotContainerRef.current;
    if (!container) return;
    container.rotation += ticker.deltaTime * 0.016 * 0.4;
  });

  // Label y for "MR.D" — positioned in the top panel area
  const topLabelY = FRAME_INSET + (DOOR_H / 2 - FRAME_INSET * 2) * 0.28;

  return (
    <>
      {/* Animated gap glow + LED + center dot */}
      <pixiGraphics ref={gRef} draw={noop} />

      {/* Rotating lock ring container — container rotation spins everything inside */}
      <pixiContainer
        x={LOCK_CX}
        y={LOCK_CY}
        ref={rotContainerRef as React.Ref<Container>}
      >
        <pixiGraphics draw={drawRotatingRing} ref={rotRingRef} />
      </pixiContainer>

      {/* LED labels (static text positioned relative to door container) */}
      {(["LOCKED", "SEALED", "PRESS."] as const).map((label, li) => (
        <pixiText
          key={label}
          text={label}
          x={LED_X + 12}
          y={LED_START_Y + li * LED_GAP}
          anchor={{ x: 0, y: 0.5 }}
          style={{
            fontFamily: "monospace",
            fontSize: 6,
            fontWeight: "bold",
            fill: GREEN,
          }}
          resolution={2}
        />
      ))}

      {/* "MR.D" identity label on top panel */}
      <pixiText
        text="MR.D"
        x={LOCK_CX}
        y={topLabelY}
        anchor={0.5}
        style={{
          fontFamily: "monospace",
          fontSize: 14,
          fontWeight: "bold",
          fill: GOLD,
        }}
        alpha={0.2}
        resolution={2}
      />

      {/* "AI OFFICE" label at bottom */}
      <pixiText
        text="AI OFFICE"
        x={LOCK_CX}
        y={DOOR_H - 20}
        anchor={0.5}
        style={{
          fontFamily: "monospace",
          fontSize: 6,
          fontWeight: "bold",
          fill: GOLD,
        }}
        alpha={0.35}
        resolution={2}
      />

      {/* "AUTHORIZED ONLY" label at very bottom */}
      <pixiText
        text="AUTHORIZED ONLY"
        x={LOCK_CX}
        y={DOOR_H - 11}
        anchor={0.5}
        style={{
          fontFamily: "monospace",
          fontSize: 5,
          fill: BLUE,
        }}
        alpha={0.45}
        resolution={2}
      />
    </>
  );
}

// ============================================================================
// MAIN EXPORT
// ============================================================================

export function BlastDoor(): ReactNode {
  // Static frame draw (never changes)
  const drawFrameCb = useCallback((g: Graphics) => {
    drawFrame(g);
  }, []);

  // Static panels draw (geometry fixed at initial gap=2)
  const gap0 = 2;
  const halfH0 = (DOOR_H - FRAME_INSET * 2 - 4) / 2;
  const midY0 = FRAME_INSET + 2 + halfH0;
  const topH = halfH0 - gap0 / 2 - 1;
  const bottomY = midY0 + gap0 / 2;
  const bottomH = DOOR_H - FRAME_INSET - 2 - bottomY;

  const drawPanelsCb = useCallback(
    (g: Graphics) => {
      drawPanels(g, topH, bottomY, bottomH);
    },
    [topH, bottomY, bottomH],
  );

  return (
    <pixiContainer x={DOOR_X} y={DOOR_Y}>
      {/* 1. Static dark recess frame + corner bolts + side bolts + hazard stripes */}
      <pixiGraphics draw={drawFrameCb} />

      {/* 2. Static door panel surfaces + ridges + lock outer rings */}
      <pixiGraphics draw={drawPanelsCb} />

      {/* 3. All animated elements: gap glow, rotating ring, LEDs, center dot */}
      <AnimatedBlastDoor />
    </pixiContainer>
  );
}
