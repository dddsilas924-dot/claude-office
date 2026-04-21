/**
 * Elevator Component — Space Station Airlock/Lift
 *
 * Renders a procedural dark-metal airlock with blue/gold accents.
 * Three layers:
 * 1. Airlock frame (background)
 * 2. Agents inside (middle)
 * 3. Sliding blast doors + status light (foreground)
 */

import { type ReactNode, useState, useEffect, useRef, useCallback } from "react";
import { Texture } from "pixi.js";
import { ELEVATOR_POSITION } from "@/systems/queuePositions";
import { AgentSprite } from "./AgentSprite";
import type { AgentAnimationState } from "@/stores/gameStore";
import { GOLD, BLUE, GREEN, RED } from "@/constants/spaceTheme";

interface ElevatorProps {
  /** Whether the elevator doors are open */
  isOpen: boolean;
  /** Map of all agents to filter those inside elevator */
  agents: Map<string, AgentAnimationState>;
  /** Elevator frame texture (ignored — procedural rendering) */
  frameTexture: Texture | null;
  /** Elevator door texture (ignored — procedural rendering) */
  doorTexture: Texture | null;
  /** Headset texture for agents */
  headsetTexture: Texture | null;
  /** Sunglasses texture for agents */
  sunglassesTexture: Texture | null;
}

/**
 * Check if an agent is inside the elevator bounds
 */
function isInsideElevator(
  agentX: number,
  agentY: number,
  elevatorX: number,
  elevatorY: number,
): boolean {
  const dx = Math.abs(agentX - elevatorX);
  const dy = Math.abs(agentY - elevatorY);
  return dx < 50 && dy < 80;
}

/**
 * Hook for animated door scale
 */
function useDoorAnimation(isOpen: boolean): number {
  const [doorScale, setDoorScale] = useState(0.09);
  const doorScaleRef = useRef(0.09);

  useEffect(() => {
    const targetScale = isOpen ? 0 : 0.09;
    const duration = 500; // 0.5 seconds
    const startScale = doorScaleRef.current;
    const startTime = performance.now();

    const animate = (currentTime: number) => {
      const elapsed = currentTime - startTime;
      const progress = Math.min(elapsed / duration, 1);
      // Ease out cubic for smooth deceleration
      const eased = 1 - Math.pow(1 - progress, 3);
      const newScale = startScale + (targetScale - startScale) * eased;

      doorScaleRef.current = newScale;
      setDoorScale(newScale);

      if (progress < 1) {
        requestAnimationFrame(animate);
      }
    };

    requestAnimationFrame(animate);
  }, [isOpen]);

  return doorScale;
}

// ── Airlock frame dimensions (matching original sprite footprint) ──
const FRAME_W = 100; // total width
const FRAME_H = 152; // total height
const DOOR_PANEL_W = 50; // each door panel at full scale
const DOOR_H = 100; // door panel height
const DOOR_Y_OFFSET = 9; // vertical offset matching original

export function Elevator({
  isOpen,
  agents,
  headsetTexture,
  sunglassesTexture,
}: ElevatorProps): ReactNode {
  const doorScale = useDoorAnimation(isOpen);

  // Filter agents that are inside the elevator
  const agentsInside = Array.from(agents.values()).filter((agent) =>
    isInsideElevator(
      agent.currentPosition.x,
      agent.currentPosition.y,
      ELEVATOR_POSITION.x,
      ELEVATOR_POSITION.y,
    ),
  );

  // Current open width of each door panel (0 = fully open)
  const panelW = (doorScale / 0.09) * DOOR_PANEL_W;

  // ── Static airlock frame ──
  const drawFrame = useCallback((g: { clear(): void; roundRect(x: number, y: number, w: number, h: number, r: number): void; fill(color: number | { color: number; alpha: number }): void; stroke(opts: { color: number; alpha: number; width: number }): void; circle(x: number, y: number, r: number): void }) => {
    g.clear();
    const hw = FRAME_W / 2;
    const hh = FRAME_H / 2;

    // Main body — dark metal
    g.roundRect(-hw, -hh, FRAME_W, FRAME_H, 6);
    g.fill({ color: 0x081420, alpha: 1 });
    g.stroke({ color: GOLD, alpha: 0.3, width: 1.5 });

    // Inner cavity (lighter) to show the shaft
    g.roundRect(-hw + 4, -hh + 14, FRAME_W - 8, FRAME_H - 18, 3);
    g.fill({ color: 0x040c18, alpha: 1 });

    // Top header bar (label background)
    g.roundRect(-hw, -hh, FRAME_W, 14, 4);
    g.fill({ color: 0x0c1e30, alpha: 1 });

    // Gold trim strips — top and bottom
    g.roundRect(-hw, -hh + 13, FRAME_W, 2, 0);
    g.fill({ color: GOLD, alpha: 0.5 });
    g.roundRect(-hw, hh - 3, FRAME_W, 2, 0);
    g.fill({ color: GOLD, alpha: 0.5 });

    // Blue accent lines on sides (vertical trim)
    g.roundRect(-hw + 1, -hh + 16, 2, FRAME_H - 22, 1);
    g.fill({ color: BLUE, alpha: 0.4 });
    g.roundRect(hw - 3, -hh + 16, 2, FRAME_H - 22, 1);
    g.fill({ color: BLUE, alpha: 0.4 });

    // Corner bolts (4 corners of main frame)
    const boltPositions: [number, number][] = [
      [-hw + 5, -hh + 5],
      [hw - 5, -hh + 5],
      [-hw + 5, hh - 5],
      [hw - 5, hh - 5],
    ];
    for (const [bx, by] of boltPositions) {
      g.circle(bx, by, 2.5);
      g.fill({ color: 0x1a3050, alpha: 1 });
      g.stroke({ color: GOLD, alpha: 0.6, width: 0.8 });
    }
  }, []);

  // ── Animated blast doors ──
  const drawDoors = useCallback((g: { clear(): void; rect(x: number, y: number, w: number, h: number): void; fill(color: number | { color: number; alpha: number }): void; stroke(opts: { color: number; alpha: number; width: number }): void; circle(x: number, y: number, r: number): void }) => {
    g.clear();
    if (panelW <= 0) return;

    const doorTop = DOOR_Y_OFFSET - DOOR_H / 2;

    // ── Left door panel (anchored at left wall, expands rightward) ──
    const lx = -DOOR_PANEL_W;
    g.rect(lx, doorTop, panelW, DOOR_H);
    g.fill({ color: 0x0a1520, alpha: 1 });
    g.stroke({ color: BLUE, alpha: 0.5, width: 1 });

    // Left door horizontal ridges
    const ridgeCount = 5;
    for (let i = 1; i <= ridgeCount; i++) {
      const ry = doorTop + (i / (ridgeCount + 1)) * DOOR_H;
      g.rect(lx + 2, ry - 0.5, panelW - 4, 1);
      g.fill({ color: BLUE, alpha: 0.25 });
    }

    // Left door blue trim (right edge)
    g.rect(lx + panelW - 2, doorTop + 2, 2, DOOR_H - 4);
    g.fill({ color: BLUE, alpha: 0.6 });

    // ── Right door panel (anchored at right wall, expands leftward) ──
    const rx = DOOR_PANEL_W - panelW;
    g.rect(rx, doorTop, panelW, DOOR_H);
    g.fill({ color: 0x0a1520, alpha: 1 });
    g.stroke({ color: BLUE, alpha: 0.5, width: 1 });

    // Right door horizontal ridges
    for (let i = 1; i <= ridgeCount; i++) {
      const ry = doorTop + (i / (ridgeCount + 1)) * DOOR_H;
      g.rect(rx + 2, ry - 0.5, panelW - 4, 1);
      g.fill({ color: BLUE, alpha: 0.25 });
    }

    // Right door blue trim (left edge)
    g.rect(rx, doorTop + 2, 2, DOOR_H - 4);
    g.fill({ color: BLUE, alpha: 0.6 });

    // Status indicator light (above frame)
    const lightColor = isOpen ? GREEN : RED;
    g.circle(0, -FRAME_H / 2 + 7, 3.5);
    g.fill({ color: lightColor, alpha: 0.9 });
  }, [panelW, isOpen]);

  return (
    <>
      {/* Airlock frame (background) */}
      <pixiContainer x={ELEVATOR_POSITION.x} y={ELEVATOR_POSITION.y}>
        <pixiGraphics draw={drawFrame} />
        {/* "AIRLOCK" label */}
        <pixiText
          text="AIRLOCK"
          x={0}
          y={-FRAME_H / 2 + 7}
          anchor={0.5}
          style={{ fontFamily: "monospace", fontSize: 8, fill: GOLD }}
        />
      </pixiContainer>

      {/* Agents inside elevator (behind doors) */}
      {agentsInside.map((agent) => (
        <AgentSprite
          key={agent.id}
          id={agent.id}
          name={agent.name}
          color={agent.color}
          number={agent.number}
          position={agent.currentPosition}
          phase={agent.phase}
          bubble={agent.bubble.content}
          headsetTexture={headsetTexture}
          sunglassesTexture={sunglassesTexture}
          renderBubble={false}
          isTyping={agent.isTyping}
        />
      ))}

      {/* Blast doors + status light (in front of agents) */}
      <pixiContainer x={ELEVATOR_POSITION.x} y={ELEVATOR_POSITION.y}>
        <pixiGraphics draw={drawDoors} />
      </pixiContainer>
    </>
  );
}

/**
 * Check if an agent is inside the elevator (for external use)
 */
export function isAgentInElevator(agentX: number, agentY: number): boolean {
  return isInsideElevator(
    agentX,
    agentY,
    ELEVATOR_POSITION.x,
    ELEVATOR_POSITION.y,
  );
}
