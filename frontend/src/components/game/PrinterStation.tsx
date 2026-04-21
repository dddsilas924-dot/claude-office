/**
 * PrinterStation Component
 *
 * Renders a procedural space station comms/data terminal.
 * Replaces sprite-based desk+printer with dark-metal terminal graphics.
 *
 * - Terminal base: dark navy roundRect with blue stroke
 * - Screen/display: smaller roundRect on top
 * - Status LED: GREEN when idle, GOLD when printing
 * - Printing indicator: animated glow pulse when isPrinting
 */

import { type ReactNode, useCallback, useRef } from "react";
import { Graphics, Texture } from "pixi.js";
import { useTick } from "@pixi/react";
import { GOLD, BLUE, GREEN } from "@/constants/spaceTheme";

interface PrinterStationProps {
  /** X position of the printer station */
  x: number;
  /** Y position of the printer station */
  y: number;
  /** Whether a report is being printed */
  isPrinting: boolean;
  /** Desk texture for the printer stand (unused — procedural rendering) */
  deskTexture: Texture | null;
  /** Printer texture (unused — procedural rendering) */
  printerTexture: Texture | null;
}

/** No-op draw so pixiGraphics with ref satisfies the required `draw` prop. */
const noop = (_g: Graphics) => {};

// ============================================================================
// STATIC TERMINAL BODY
// ============================================================================

function drawTerminalBase(g: Graphics): void {
  g.clear();

  // Terminal base: 60×30, dark metal fill, blue stroke
  g.roundRect(-30, 0, 60, 30, 4);
  g.fill({ color: 0x0a1520 });
  g.stroke({ width: 1, color: BLUE, alpha: 0.3 });

  // Screen/display panel on top: narrower, darker
  g.roundRect(-22, -18, 44, 16, 3);
  g.fill({ color: 0x020810 });
  g.stroke({ width: 0.8, color: BLUE, alpha: 0.4 });

  // Decorative scan-line inside screen
  for (let i = 0; i < 3; i++) {
    g.rect(-18, -14 + i * 4, 36, 1);
    g.fill({ color: BLUE, alpha: 0.08 });
  }

  // Bottom connector nubs (two small rectangles at base)
  g.rect(-16, 30, 8, 4);
  g.fill({ color: 0x071018 });
  g.rect(8, 30, 8, 4);
  g.fill({ color: 0x071018 });
}

// ============================================================================
// ANIMATED PRINTING INDICATOR + STATUS LED
// ============================================================================

interface PrintingIndicatorProps {
  isPrinting: boolean;
}

function PrintingIndicator({ isPrinting }: PrintingIndicatorProps): ReactNode {
  const gRef = useRef<Graphics>(null);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.04;
    const g = gRef.current;
    if (!g) return;

    g.clear();

    // Status LED — top-right corner of terminal base
    const ledColor = isPrinting ? GOLD : GREEN;
    const ledAlpha = isPrinting
      ? 0.6 + Math.sin(timeRef.current * 6) * 0.4  // pulse when printing
      : 0.9;

    g.circle(20, 6, 2.5);
    g.fill({ color: ledColor, alpha: ledAlpha });

    // When printing: draw a small animated data-transfer bar on the screen
    if (isPrinting) {
      const barProgress = (Math.sin(timeRef.current * 4) * 0.5 + 0.5);
      const barW = Math.floor(barProgress * 30);

      // Background track
      g.rect(-15, -10, 30, 3);
      g.fill({ color: BLUE, alpha: 0.12 });

      // Active segment
      if (barW > 0) {
        g.rect(-15, -10, barW, 3);
        g.fill({ color: BLUE, alpha: 0.7 });
      }

      // Blinking "TX" indicator dot
      const blinkOn = Math.sin(timeRef.current * 10) > 0;
      if (blinkOn) {
        g.circle(-20, -9, 1.5);
        g.fill({ color: GOLD, alpha: 0.9 });
      }
    }
  });

  return <pixiGraphics ref={gRef} draw={noop} />;
}

// ============================================================================
// MAIN EXPORT
// ============================================================================

export function PrinterStation({
  x,
  y,
  isPrinting,
  // deskTexture and printerTexture are intentionally unused — procedural only
}: PrinterStationProps): ReactNode {
  const drawBase = useCallback((g: Graphics) => drawTerminalBase(g), []);

  return (
    <pixiContainer x={x} y={y}>
      {/* Static terminal body */}
      <pixiGraphics draw={drawBase} />

      {/* Animated LED + printing indicator (re-draws every tick) */}
      <PrintingIndicator isPrinting={isPrinting} />
    </pixiContainer>
  );
}
