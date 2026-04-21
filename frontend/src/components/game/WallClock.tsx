"use client";

/**
 * WallClock — Compact space-station clock for top-right wall area.
 *
 * Gold HH:MM digits with blinking colon, blue seconds.
 * Dark metal panel frame matching station aesthetic.
 */

import { type ReactNode, useState, useEffect, useRef, useCallback } from "react";
import { Graphics } from "pixi.js";
import { useTick } from "@pixi/react";
import { GOLD, BLUE, goldAlpha, blueAlpha } from "@/constants/spaceTheme";

export function WallClock(): ReactNode {
  const [now, setNow] = useState<Date>(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const [colonAlpha, setColonAlpha] = useState(1);
  const timeRef = useRef(0);

  useTick((ticker) => {
    timeRef.current += ticker.deltaTime * 0.016;
    setColonAlpha(Math.sin(timeRef.current * 4) > 0 ? 1.0 : 0.3);
  });

  const hh = now.getHours().toString().padStart(2, "0");
  const mm = now.getMinutes().toString().padStart(2, "0");
  const ss = now.getSeconds().toString().padStart(2, "0");

  const drawFrame = useCallback((g: Graphics) => {
    g.clear();

    // Small recessed panel
    g.roundRect(0, 0, 80, 36, 4);
    g.fill({ color: 0x060e1a, alpha: 0.85 });
    g.stroke({ width: 1, color: GOLD, alpha: 0.2 });

    // Inner glow border
    g.roundRect(2, 2, 76, 32, 3);
    g.stroke({ width: 0.5, ...blueAlpha(0.15) });

    // Top gold trim
    g.rect(8, 0, 64, 1.5);
    g.fill(goldAlpha(0.25));
  }, []);

  const cx = 40; // center of 80px frame

  return (
    <pixiContainer>
      <pixiGraphics draw={drawFrame} />

      {/* HH */}
      <pixiText
        text={hh}
        x={cx - 16}
        y={13}
        anchor={{ x: 1, y: 0.5 }}
        style={{
          fontFamily: "monospace",
          fontSize: 14,
          fontWeight: "bold",
          fill: GOLD,
        }}
        resolution={2}
      />

      {/* Blinking colon */}
      <pixiText
        text=":"
        x={cx}
        y={13}
        anchor={0.5}
        alpha={colonAlpha}
        style={{
          fontFamily: "monospace",
          fontSize: 14,
          fontWeight: "bold",
          fill: GOLD,
        }}
        resolution={2}
      />

      {/* MM */}
      <pixiText
        text={mm}
        x={cx + 16}
        y={13}
        anchor={{ x: 0, y: 0.5 }}
        style={{
          fontFamily: "monospace",
          fontSize: 14,
          fontWeight: "bold",
          fill: GOLD,
        }}
        resolution={2}
      />

      {/* Seconds */}
      <pixiText
        text={ss}
        x={cx}
        y={27}
        anchor={0.5}
        style={{
          fontFamily: "monospace",
          fontSize: 8,
          fontWeight: "bold",
          fill: BLUE,
        }}
        resolution={2}
      />
    </pixiContainer>
  );
}
