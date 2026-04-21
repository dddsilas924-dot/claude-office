/**
 * OfficeGame - Main Game Canvas
 *
 * Main visualization component using:
 * - Centralized Zustand store
 * - XState state machines
 * - Single animation tick loop
 *
 * The component is purely for rendering - all state logic is in the store/machines.
 */

"use client";

import { Application, extend } from "@pixi/react";
import {
  Container,
  Text,
  Graphics,
  Sprite,
  Application as PixiApplication,
} from "pixi.js";
import { useMemo, useEffect, useRef, useState, type ReactNode } from "react";
import { Assets, Texture } from "pixi.js";
import {
  TransformWrapper,
  TransformComponent,
  type ReactZoomPanPinchRef,
} from "react-zoom-pan-pinch";
import { useShallow } from "zustand/react/shallow";
import { performFullCleanup, getHmrVersion } from "@/systems/hmrCleanup";

import {
  useGameStore,
  selectAgents,
  selectBoss,
  selectTodos,
  selectDebugMode,
  selectShowPaths,
  selectShowQueueSlots,
  selectShowPhaseLabels,
  selectShowObstacles,
  selectElevatorState,
  selectContextUtilization,
  selectIsCompacting,
  selectPrintReport,
  selectBridgeAgents,
} from "@/stores/gameStore";
import { useAnimationSystem } from "@/systems/animationSystem";
import { useCompactionAnimation } from "@/systems/compactionAnimation";
import { useOfficeTextures } from "@/hooks/useOfficeTextures";
import {
  CANVAS_WIDTH,
  CANVAS_HEIGHT,
  BACKGROUND_COLOR,
} from "@/constants/canvas";
import {
  EMPLOYEE_OF_MONTH_POSITION,
  CITY_WINDOW_POSITION,
  SAFETY_SIGN_POSITION,
  WALL_CLOCK_POSITION,
  WALL_OUTLET_POSITION,
  WHITEBOARD_POSITION,
  WATER_COOLER_POSITION,
  COFFEE_MACHINE_POSITION,
  PRINTER_STATION_POSITION,
  PLANT_POSITION,
  BOSS_RUG_POSITION,
  TRASH_CAN_OFFSET,
  bridgeAgentPosition,
} from "@/constants/positions";
import {
  AgentSprite,
  AgentArms,
  AgentHeadset,
  AgentLabel,
  Bubble as AgentBubble,
} from "./AgentSprite";
import { BossSprite, BossBubble, MobileBoss } from "./BossSprite";
import { isInElevatorZone } from "@/systems/queuePositions";
import { TrashCanSprite } from "./TrashCanSprite";
import { WallClock } from "./WallClock";
import { Whiteboard } from "./Whiteboard";
import { SafetySign } from "./SafetySign";
import { CityWindow } from "./CityWindow";
import { EmployeeOfTheMonth } from "./EmployeeOfTheMonth";
import { Elevator, isAgentInElevator } from "./Elevator";
import { PrinterStation } from "./PrinterStation";
import { DebugOverlays } from "./DebugOverlays";
import {
  DeskSurfacesBase,
  DeskSurfacesTop,
  useDeskPositions,
} from "./DeskGrid";
import { ZoomControls } from "./ZoomControls";
import { LoadingScreen } from "./LoadingScreen";
import { OfficeBackground } from "./OfficeBackground";

// Register PixiJS components
extend({ Container, Text, Graphics, Sprite });

// ============================================================================
// MAIN COMPONENT
// ============================================================================

export function OfficeGame(): ReactNode {
  // Track PixiJS app for cleanup
  const appRef = useRef<PixiApplication | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const transformRef = useRef<ReactZoomPanPinchRef>(null);

  // HMR version for forcing remount
  const hmrVersion = getHmrVersion();

  // Load all office textures
  const { textures, loaded: spritesLoaded } = useOfficeTextures();

  // ── Bridge agent character textures (fal.ai pixel art) ──────────────
  const [charTextures, setCharTextures] = useState<Map<string, Texture>>(
    new Map(),
  );
  useEffect(() => {
    // Map dept_id → pixel art portrait (transparent PNG, 128px tall).
    // Keys include both long forms (Claude Code hooks) and short
    // forms (Commander Bridge / demo_bridge_sprites.py).
    const CHARACTER_FILES: Record<string, string> = {
      commander: "/sprites/characters/char_phil.png",
      research: "/sprites/characters/char_ryou.png",
      sales: "/sprites/characters/char_rei.png",
      design: "/sprites/characters/char_rick.png",
      content: "/sprites/characters/char_content.png",
      writing: "/sprites/characters/char_kai.png",
      ai_investment: "/sprites/characters/char_ai_invest.png",
      ai_inv: "/sprites/characters/char_ai_invest.png",
      phil_consulting: "/sprites/characters/char_phil_consul.png",
      phil: "/sprites/characters/char_phil_consul.png",
      new_biz: "/sprites/characters/char_tadashi.png",
      advertising: "/sprites/characters/char_ena.png",
      security: "/sprites/characters/char_security.png",
      bridge: "/sprites/characters/bri_kun_pigeon.png",
      doradora_sns: "/sprites/characters/char_phil.png",
    };
    let cancelled = false;
    (async () => {
      const map = new Map<string, Texture>();
      await Promise.all(
        Object.entries(CHARACTER_FILES).map(async ([deptId, path]) => {
          try {
            const tex = await Assets.load(path);
            if (!cancelled) map.set(deptId, tex);
          } catch {
            // Fallback to capsule on load error
          }
        }),
      );
      if (!cancelled) setCharTextures(map);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Start animation system
  useAnimationSystem();

  // Cleanup on unmount (HMR or navigation)
  useEffect(() => {
    return () => {
      if (appRef.current) {
        try {
          appRef.current.destroy(true, {
            children: true,
            texture: true,
            textureSource: true,
          });
        } catch {
          // Ignore cleanup errors
        }
        appRef.current = null;
      }
      performFullCleanup();
    };
  }, []);

  // Subscribe to store state
  const agents = useGameStore(useShallow(selectAgents));
  const bridgeAgents = useGameStore(useShallow(selectBridgeAgents));
  const boss = useGameStore(selectBoss);
  const todos = useGameStore(selectTodos);
  const debugMode = useGameStore(selectDebugMode);
  const showPaths = useGameStore(selectShowPaths);
  const showQueueSlots = useGameStore(selectShowQueueSlots);
  const showPhaseLabels = useGameStore(selectShowPhaseLabels);
  const showObstacles = useGameStore(selectShowObstacles);
  const elevatorState = useGameStore(selectElevatorState);
  const contextUtilization = useGameStore(selectContextUtilization);
  const isCompacting = useGameStore(selectIsCompacting);
  const printReport = useGameStore(selectPrintReport);

  // Stable list of bridge agents in insertion order so PixiJS keys stay
  // consistent frame-to-frame. Insertion order = the order the Map was
  // populated by applyBridgeEvent; see BRIDGE_ROW_* in constants/positions.ts
  // for the row-layout contract this feeds.
  const bridgeAgentList = useMemo(
    () => Array.from(bridgeAgents.values()),
    [bridgeAgents],
  );

  // Compaction animation state
  const compactionAnimation = useCompactionAnimation();

  // Use store's elevator state (controlled by state machine)
  const isElevatorOpen = elevatorState === "open";

  // Calculate occupied desks (normal agents + bridge agents)
  const occupiedDesks = useMemo(() => {
    const desks = new Set<number>();
    for (const agent of agents.values()) {
      if (agent.desk && agent.phase === "idle") {
        desks.add(agent.desk);
      }
    }
    // Bridge agents occupy desks sequentially (1-based desk numbers)
    let bridgeIdx = 0;
    for (const _bridge of bridgeAgents.values()) {
      bridgeIdx++;
      desks.add(bridgeIdx); // desk 1, 2, 3, ... (same slots as bridgeAgentPosition)
    }
    return desks;
  }, [agents, bridgeAgents]);

  // Calculate desk tasks for marquee display
  const deskTasks = useMemo(() => {
    const tasks = new Map<number, string>();
    for (const agent of agents.values()) {
      if (agent.desk && agent.phase === "idle") {
        const label = agent.currentTask ?? agent.name ?? "";
        if (label) tasks.set(agent.desk, label);
      }
    }
    // Bridge agents show their display name as desk task
    let bridgeIdx = 0;
    for (const bridge of bridgeAgents.values()) {
      bridgeIdx++;
      tasks.set(bridgeIdx, bridge.displayName);
    }
    return tasks;
  }, [agents, bridgeAgents]);

  // Desk count — includes both normal and bridge agents
  const deskCount = useMemo(() => {
    const totalNeeded = Math.max(agents.size, bridgeAgents.size);
    return Math.max(8, Math.ceil(totalNeeded / 4) * 4);
  }, [agents.size, bridgeAgents.size]);

  // Desk positions for Y-sorted rendering
  const deskPositions = useDeskPositions(deskCount, occupiedDesks);

  // Keyboard shortcuts for debug
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "d" || e.key === "D") {
        useGameStore.getState().setDebugMode(!debugMode);
      }
      if (debugMode) {
        if (e.key === "p" || e.key === "P") {
          useGameStore.getState().toggleDebugOverlay("paths");
        }
        if (e.key === "q" || e.key === "Q") {
          useGameStore.getState().toggleDebugOverlay("queueSlots");
        }
        if (e.key === "l" || e.key === "L") {
          useGameStore.getState().toggleDebugOverlay("phaseLabels");
        }
        if (e.key === "o" || e.key === "O") {
          useGameStore.getState().toggleDebugOverlay("obstacles");
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [debugMode]);

  // Reset the pan/zoom transform whenever the container is resized (e.g. sidebar
  // open/close). Without this, react-zoom-pan-pinch keeps a stale translate that
  // was calculated against the old container dimensions, which crops the scene.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(() => {
      transformRef.current?.resetTransform(0);
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={containerRef}
      className="w-full h-full flex items-center justify-center overflow-hidden relative"
    >
      <TransformWrapper
        ref={transformRef}
        initialScale={1}
        minScale={1}
        maxScale={3}
        wheel={{ step: 0.1 }}
        pinch={{ step: 5 }}
        doubleClick={{ mode: "reset" }}
      >
        <ZoomControls />
        <TransformComponent
          wrapperClass="w-full h-full"
          contentClass="w-full h-full flex items-center justify-center"
        >
          <div className="pixi-canvas-container w-full h-full flex items-center justify-center">
            <Application
              key={`pixi-app-${hmrVersion}`}
              width={CANVAS_WIDTH}
              height={CANVAS_HEIGHT}
              backgroundColor={BACKGROUND_COLOR}
              autoDensity={true}
              resolution={
                typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1
              }
              onInit={(app) => {
                appRef.current = app;
              }}
            >
              {/* Loading screen - shown while sprites are loading */}
              {!spritesLoaded && <LoadingScreen />}

              {/* Office content - hidden while loading */}
              {spritesLoaded && (
                <>
                  {/* Floor and walls */}
                  <OfficeBackground floorTileTexture={textures.floorTile} />

                  {/* Boss area rug - rendered right after floor */}
                  {textures.bossRug && (
                    <pixiSprite
                      texture={textures.bossRug}
                      anchor={0.5}
                      x={BOSS_RUG_POSITION.x}
                      y={BOSS_RUG_POSITION.y}
                      scale={0.3}
                    />
                  )}

                  {/* Wall decorations */}
                  <pixiContainer
                    x={EMPLOYEE_OF_MONTH_POSITION.x}
                    y={EMPLOYEE_OF_MONTH_POSITION.y}
                  >
                    <EmployeeOfTheMonth />
                  </pixiContainer>
                  <pixiContainer
                    x={CITY_WINDOW_POSITION.x}
                    y={CITY_WINDOW_POSITION.y}
                  >
                    <CityWindow />
                  </pixiContainer>
                  <pixiContainer
                    x={SAFETY_SIGN_POSITION.x}
                    y={SAFETY_SIGN_POSITION.y}
                  >
                    <SafetySign />
                  </pixiContainer>
                  <pixiContainer
                    x={WALL_CLOCK_POSITION.x}
                    y={WALL_CLOCK_POSITION.y}
                  >
                    <WallClock />
                  </pixiContainer>
                  {/* Wall outlet below clock */}
                  {textures.wallOutlet && (
                    <pixiSprite
                      texture={textures.wallOutlet}
                      anchor={0.5}
                      x={WALL_OUTLET_POSITION.x}
                      y={WALL_OUTLET_POSITION.y}
                      scale={0.04}
                    />
                  )}
                  <pixiContainer
                    x={WHITEBOARD_POSITION.x}
                    y={WHITEBOARD_POSITION.y}
                  >
                    <Whiteboard todos={todos} />
                  </pixiContainer>
                  {textures.waterCooler && (
                    <pixiSprite
                      texture={textures.waterCooler}
                      anchor={0.5}
                      x={WATER_COOLER_POSITION.x}
                      y={WATER_COOLER_POSITION.y}
                      scale={0.198}
                    />
                  )}
                  {/* Coffee machine - to the right of water cooler */}
                  {textures.coffeeMachine && (
                    <pixiSprite
                      texture={textures.coffeeMachine}
                      anchor={0.5}
                      x={COFFEE_MACHINE_POSITION.x}
                      y={COFFEE_MACHINE_POSITION.y}
                      scale={0.1}
                    />
                  )}

                  {/* Printer station - bottom left corner */}
                  {/* Only print after boss delivers the completion message */}
                  <PrinterStation
                    x={PRINTER_STATION_POSITION.x}
                    y={PRINTER_STATION_POSITION.y}
                    isPrinting={
                      printReport && !isCompacting && !!boss.bubble.content
                    }
                    deskTexture={textures.desk}
                    printerTexture={textures.printer}
                  />

                  {/* Plant - to the right of printer */}
                  {textures.plant && (
                    <pixiSprite
                      texture={textures.plant}
                      anchor={0.5}
                      x={PLANT_POSITION.x}
                      y={PLANT_POSITION.y}
                      scale={0.1}
                    />
                  )}

                  {/* Elevator with animated doors and agents inside */}
                  <Elevator
                    isOpen={isElevatorOpen}
                    agents={agents}
                    frameTexture={textures.elevatorFrame}
                    doorTexture={textures.elevatorDoor}
                    headsetTexture={textures.headset}
                    sunglassesTexture={textures.sunglasses}
                  />

                  {/* Y-sorted layer: chairs and agents sorted by Y position (higher Y = in front) */}
                  <pixiContainer sortableChildren={true}>
                    {/* Desk chairs - zIndex based on chair seat back */}
                    {deskPositions.map((desk, i) => {
                      const chairZIndex = desk.y + 20;
                      return (
                        <pixiContainer
                          key={`chair-${i}`}
                          x={desk.x}
                          y={desk.y}
                          zIndex={chairZIndex}
                        >
                          {textures.chair && (
                            <pixiSprite
                              texture={textures.chair}
                              anchor={0.5}
                              x={0}
                              y={30}
                              scale={0.1386}
                            />
                          )}
                        </pixiContainer>
                      );
                    })}

                    {/* Agents outside elevator - zIndex based on feet Y position */}
                    {Array.from(agents.values())
                      .filter(
                        (agent) =>
                          !isAgentInElevator(
                            agent.currentPosition.x,
                            agent.currentPosition.y,
                          ),
                      )
                      .map((agent) => (
                        <pixiContainer
                          key={agent.id}
                          zIndex={agent.currentPosition.y}
                        >
                          <AgentSprite
                            id={agent.id}
                            name={agent.name}
                            color={agent.color}
                            number={agent.number}
                            position={agent.currentPosition}
                            phase={agent.phase}
                            bubble={agent.bubble.content}
                            headsetTexture={textures.headset}
                            sunglassesTexture={textures.sunglasses}
                            renderBubble={false}
                            renderLabel={false}
                            isTyping={agent.isTyping}
                          />
                        </pixiContainer>
                      ))}
                  </pixiContainer>

                  {/* Desk surfaces and keyboards (behind agent arms) */}
                  <DeskSurfacesBase
                    deskCount={deskCount}
                    occupiedDesks={occupiedDesks}
                    deskTexture={textures.desk}
                    keyboardTexture={textures.keyboard}
                  />

                  {/* Agent arms - rendered after desk/keyboard, before headsets */}
                  {Array.from(agents.values())
                    .filter((agent) => agent.phase === "idle")
                    .map((agent) => (
                      <AgentArms
                        key={`arms-${agent.id}`}
                        position={agent.currentPosition}
                        isTyping={agent.isTyping}
                      />
                    ))}

                  {/* Agent headsets - rendered after arms so they appear on top */}
                  {textures.headset &&
                    Array.from(agents.values())
                      .filter((agent) => agent.phase === "idle")
                      .map((agent) => (
                        <AgentHeadset
                          key={`headset-${agent.id}`}
                          position={agent.currentPosition}
                          headsetTexture={textures.headset!}
                        />
                      ))}

                  {/* Monitors and decorations (in front of agent arms) */}
                  <DeskSurfacesTop
                    deskCount={deskCount}
                    occupiedDesks={occupiedDesks}
                    deskTasks={deskTasks}
                    monitorTexture={textures.monitor}
                    coffeeMugTexture={textures.coffeeMug}
                    staplerTexture={textures.stapler}
                    deskLampTexture={textures.deskLamp}
                    penHolderTexture={textures.penHolder}
                    magic8BallTexture={textures.magic8Ball}
                    rubiksCubeTexture={textures.rubiksCube}
                    rubberDuckTexture={textures.rubberDuck}
                    thermosTexture={textures.thermos}
                  />

                  {/* Boss */}
                  <BossSprite
                    position={boss.position}
                    state={boss.backendState}
                    bubble={boss.bubble.content}
                    inUseBy={boss.inUseBy}
                    currentTask={boss.currentTask}
                    chairTexture={textures.chair}
                    deskTexture={textures.desk}
                    keyboardTexture={textures.keyboard}
                    monitorTexture={textures.monitor}
                    phoneTexture={textures.phone}
                    headsetTexture={textures.headset}
                    sunglassesTexture={textures.sunglasses}
                    renderBubble={false}
                    isTyping={boss.isTyping}
                    isAway={compactionAnimation.phase !== "idle"}
                  />

                  {/* Mobile Boss (when walking to/from trash can) */}
                  {compactionAnimation.bossPosition && (
                    <MobileBoss
                      position={compactionAnimation.bossPosition}
                      jumpOffset={compactionAnimation.jumpOffset}
                      scale={compactionAnimation.bossScale}
                      sunglassesTexture={textures.sunglasses}
                      headsetTexture={textures.headset}
                    />
                  )}

                  {/* Trash Can (Context Utilization Indicator) - right of boss desk */}
                  <TrashCanSprite
                    x={boss.position.x + TRASH_CAN_OFFSET.x}
                    y={boss.position.y + TRASH_CAN_OFFSET.y}
                    contextUtilization={
                      compactionAnimation.phase !== "idle"
                        ? compactionAnimation.animatedContextUtilization
                        : contextUtilization
                    }
                    isCompacting={isCompacting}
                    isStomping={compactionAnimation.isStomping}
                  />

                  {/* Debug overlays */}
                  {debugMode && (
                    <DebugOverlays
                      showPaths={showPaths}
                      showQueueSlots={showQueueSlots}
                      showPhaseLabels={showPhaseLabels}
                      showObstacles={showObstacles}
                    />
                  )}

                  {/* Debug mode indicator */}
                  {debugMode && (
                    <pixiText
                      text="DEBUG MODE (D=toggle, P=paths, Q=queue, L=labels, O=obstacles, T=time)"
                      x={10}
                      y={10}
                      style={{
                        fontSize: 12,
                        fill: 0x00ff00,
                        fontFamily: "monospace",
                      }}
                    />
                  )}

                  {/*
                    Bridge Agent Row — ephemeral sprites for Commander Bridge
                    events.

                    Rendered in a dedicated container above the desk region so
                    they don't participate in the agents' y-sort (their
                    `dept_id`-keyed lifecycle is independent of the XState
                    agent choreography). `renderBubble` and `renderLabel` are
                    enabled so the sprite carries its own bubble + name tag —
                    the dedicated AgentLabel/Bubble top layers below are
                    scoped to the regular `agents` map on purpose.
                   */}
                  {bridgeAgentList.map((bridge, index) => {
                    const pos = bridgeAgentPosition(index);
                    // Extract leading emoji from displayName for badge
                    const emojiMatch =
                      bridge.displayName.match(
                        /^(\p{Extended_Pictographic}(?:\u200D\p{Extended_Pictographic})*\uFE0F?)\s*/u,
                      );
                    const deptEmoji = emojiMatch ? emojiMatch[1] : undefined;
                    const bubbleContent = bridge.message
                      ? {
                          type: "speech" as const,
                          text: bridge.message.slice(0, 80),
                          icon: deptEmoji,
                        }
                      : null;
                    // Model badge color
                    const modelColor =
                      bridge.model === "opus"
                        ? 0xf1c40f
                        : bridge.model === "haiku"
                          ? 0x2ecc71
                          : 0x3498db; // default sonnet blue
                    const modelLabel = bridge.model ?? "sonnet";

                    return (
                      <pixiContainer key={`bridge-${bridge.deptId}`}>
                        <AgentSprite
                          id={`bridge-${bridge.deptId}`}
                          name={bridge.displayName}
                          color={bridge.agentColor}
                          number={0}
                          position={pos}
                          phase="idle"
                          bubble={bubbleContent}
                          headsetTexture={textures.headset}
                          sunglassesTexture={textures.sunglasses}
                          characterTexture={
                            charTextures.get(bridge.deptId) ?? null
                          }
                          renderBubble={false}
                          renderLabel={true}
                          isTyping={bridge.eventKind === "ASK_STARTED"}
                        />
                        {/* Model badge below agent */}
                        <pixiContainer
                          x={pos.x}
                          y={pos.y + 14}
                          scale={0.5}
                        >
                          <pixiGraphics
                            draw={(g) => {
                              g.clear();
                              g.roundRect(-24, -8, 48, 16, 8);
                              g.fill(modelColor);
                              g.stroke({ width: 1.5, color: 0xffffff });
                            }}
                          />
                          <pixiText
                            text={modelLabel}
                            anchor={0.5}
                            style={{
                              fontFamily:
                                '"Courier New", "SF Mono", monospace',
                              fontSize: 12,
                              fill: 0xffffff,
                              fontWeight: "bold",
                            }}
                            resolution={2}
                          />
                        </pixiContainer>
                      </pixiContainer>
                    );
                  })}

                  {/* Labels Layer - rendered on top of most things */}
                  {Array.from(agents.values())
                    .filter(
                      (agent) =>
                        agent.name && !isInElevatorZone(agent.currentPosition),
                    )
                    .map((agent) => (
                      <AgentLabel
                        key={`label-${agent.id}`}
                        name={agent.name!}
                        position={agent.currentPosition}
                      />
                    ))}

                  {/* Bubbles Layer - rendered on top of everything */}
                  {Array.from(agents.values())
                    .filter(
                      (agent) =>
                        agent.bubble.content &&
                        !isInElevatorZone(agent.currentPosition),
                    )
                    .map((agent) => (
                      <pixiContainer
                        key={`bubble-${agent.id}`}
                        x={agent.currentPosition.x}
                        y={agent.currentPosition.y}
                      >
                        <AgentBubble
                          content={agent.bubble.content!}
                          yOffset={-80}
                        />
                      </pixiContainer>
                    ))}
                  {boss.bubble.content && (
                    <pixiContainer x={boss.position.x} y={boss.position.y}>
                      <BossBubble content={boss.bubble.content} yOffset={-80} />
                    </pixiContainer>
                  )}

                  {/* Bridge Agent Bubbles Layer — rendered after all other
                      bubbles so they sit on top. Columns 0-1 get right-side
                      bubbles, columns 2-3 get left-side bubbles so nothing
                      clips off the canvas edge. */}
                  {bridgeAgentList.map((bridge, index) => {
                    if (!bridge.message) return null;
                    const pos = bridgeAgentPosition(index);
                    const col = index % 4;
                    const emojiMatch = bridge.displayName.match(
                      /^(\p{Extended_Pictographic}(?:\u200D\p{Extended_Pictographic})*\uFE0F?)\s*/u,
                    );
                    const deptEmoji = emojiMatch ? emojiMatch[1] : undefined;
                    return (
                      <pixiContainer
                        key={`bridge-bubble-${bridge.deptId}`}
                        x={pos.x}
                        y={pos.y}
                      >
                        <AgentBubble
                          content={{
                            type: "speech",
                            text: bridge.message.slice(0, 80),
                            icon: deptEmoji,
                          }}
                          yOffset={-93}
                          side={col < 2 ? "right" : "left"}
                        />
                      </pixiContainer>
                    );
                  })}
                </>
              )}
            </Application>
          </div>
        </TransformComponent>
      </TransformWrapper>
    </div>
  );
}
