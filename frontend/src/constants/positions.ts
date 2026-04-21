/**
 * Position constants for office elements.
 *
 * All positions are in pixels relative to the canvas origin (top-left).
 *
 * Layout: Space Station Command Bridge (Concept C v4)
 * - Title banner at top of wall
 * - Blast door (left), Main screen (center), Crew panel (right)
 * - 4×2 desk grid below wall
 * - Hologram projector center, objects around edges
 */

import {
  BANNER_X,
  BANNER_Y,
  SCREEN_X,
  SCREEN_Y,
  DOOR_X,
  DOOR_Y,
  PANEL_X,
  PANEL_Y,
  WALL_HEIGHT,
} from "./spaceTheme";

// ============================================================================
// WALL ELEMENTS (Space Station)
// ============================================================================

/** Title banner: "MR.D AI OFFICE" */
export const TITLE_BANNER_POSITION = { x: BANNER_X, y: BANNER_Y };

/** Main screen (replaces city window) */
export const MAIN_SCREEN_POSITION = { x: SCREEN_X, y: SCREEN_Y };

/** Blast door (left wall, replaces Employee of the Month) */
export const BLAST_DOOR_POSITION = { x: DOOR_X, y: DOOR_Y };

/** Crew/clock panel (right wall, replaces safety sign + clock) */
export const CREW_PANEL_POSITION = { x: PANEL_X, y: PANEL_Y };

/** Whiteboard position — moved to floor (right side, above desk row 1) */
export const WHITEBOARD_POSITION = { x: 920, y: WALL_HEIGHT + 14 };

// ============================================================================
// LEGACY POSITIONS (kept for API compat, may be unused)
// ============================================================================

/** @deprecated Use BLAST_DOOR_POSITION */
export const EMPLOYEE_OF_MONTH_POSITION = { x: 184, y: 50 };

/** @deprecated Use MAIN_SCREEN_POSITION */
export const CITY_WINDOW_POSITION = { x: 319, y: 30 };

/** @deprecated Use CREW_PANEL_POSITION */
export const SAFETY_SIGN_POSITION = { x: 1120, y: 40 };

/** @deprecated Use CREW_PANEL_POSITION */
export const WALL_CLOCK_POSITION = { x: 581, y: 80 };

/** @deprecated Removed in space station theme */
export const WALL_OUTLET_POSITION = { x: 581, y: 209 };

/** @deprecated Removed in space station theme */
export const WATER_COOLER_POSITION = { x: 1010, y: 200 };

/** @deprecated Removed in space station theme */
export const COFFEE_MACHINE_POSITION = { x: 1081, y: 191 };

// ============================================================================
// FLOOR ELEMENTS
// ============================================================================

/** Printer station position (bottom left) */
export const PRINTER_STATION_POSITION = { x: 50, y: 945 };

/** Plant position — replaced by Bio-Pod in space station */
export const PLANT_POSITION = { x: 118, y: 970 };

// ============================================================================
// SPACE STATION FLOOR OBJECTS
// ============================================================================

/** Hologram projector (center of floor, between desk rows) */
export const HOLOGRAM_POSITION = { x: 640, y: WALL_HEIGHT + 75 };

/** Energy station (left wall, lower) */
export const ENERGY_STATION_POSITION = { x: 100, y: WALL_HEIGHT + 420 };

/** Bio-pod (right side, upper floor) */
export const BIO_POD_POSITION = { x: 1180, y: WALL_HEIGHT + 120 };

/** Shield generator (bottom left) */
export const SHIELD_GEN_POSITION = { x: 170, y: 914 };

/** Airlock (right wall) */
export const AIRLOCK_POSITION = { x: 1225, y: WALL_HEIGHT + 30 };

/** Comms array (bottom left corner) */
export const COMMS_POSITION = { x: 55, y: 949 };

// ============================================================================
// BOSS AREA
// ============================================================================

/** Boss area rug position (centered under boss desk) */
export const BOSS_RUG_POSITION = { x: 640, y: 940 };

/** Trash can offset from boss desk position */
export const TRASH_CAN_OFFSET = { x: 110, y: 65 };

// ============================================================================
// BRIDGE AGENT DESK POSITIONS (Commander Bridge ephemeral sprites)
// ============================================================================

/** Columns per desk row (matches DeskGrid ROW_SIZE). */
const BRIDGE_DESK_COLS = 4;

/** Desk grid origin X (matches DeskGrid DESK_START_X). */
const BRIDGE_DESK_START_X = 256;

/** Desk grid origin Y (matches DeskGrid DESK_START_Y). */
const BRIDGE_DESK_START_Y = 408;

/** Horizontal spacing between desk centres (matches DeskGrid DESK_SPACING_X). */
const BRIDGE_DESK_SPACING_X = 256;

/** Vertical spacing between desk rows (matches DeskGrid DESK_SPACING_Y). */
const BRIDGE_DESK_SPACING_Y = 192;

/**
 * Deterministic layout for Bridge sprites, aligned to the desk grid.
 */
export function bridgeAgentPosition(index: number): { x: number; y: number } {
  const row = Math.floor(index / BRIDGE_DESK_COLS);
  const col = index % BRIDGE_DESK_COLS;
  return {
    x: BRIDGE_DESK_START_X + col * BRIDGE_DESK_SPACING_X,
    y: BRIDGE_DESK_START_Y + row * BRIDGE_DESK_SPACING_Y,
  };
}
