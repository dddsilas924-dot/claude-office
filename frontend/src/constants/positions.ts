/**
 * Position constants for office elements.
 *
 * All positions are in pixels relative to the canvas origin (top-left).
 */

// ============================================================================
// WALL DECORATIONS
// ============================================================================

/** Employee of the Month frame position */
export const EMPLOYEE_OF_MONTH_POSITION = { x: 184, y: 50 };

/** City window position */
export const CITY_WINDOW_POSITION = { x: 319, y: 30 };

/** Safety sign position */
export const SAFETY_SIGN_POSITION = { x: 1120, y: 40 };

/** Wall clock position */
export const WALL_CLOCK_POSITION = { x: 581, y: 80 };

/** Wall outlet position (below clock) */
export const WALL_OUTLET_POSITION = { x: 581, y: 209 };

/** Whiteboard position */
export const WHITEBOARD_POSITION = { x: 641, y: 11 };

/** Water cooler position */
export const WATER_COOLER_POSITION = { x: 1010, y: 200 };

/** Coffee machine position (to the right of water cooler) */
export const COFFEE_MACHINE_POSITION = { x: 1081, y: 191 };

// ============================================================================
// FLOOR ELEMENTS
// ============================================================================

/** Printer station position (bottom left corner) */
export const PRINTER_STATION_POSITION = { x: 50, y: 945 };

/** Plant position (to the right of printer) */
export const PLANT_POSITION = { x: 118, y: 970 };

// ============================================================================
// BOSS AREA
// ============================================================================

/** Boss area rug position (centered under boss desk) */
export const BOSS_RUG_POSITION = { x: 640, y: 940 };

/** Trash can offset from boss desk position */
export const TRASH_CAN_OFFSET = { x: 110, y: 65 };

// ============================================================================
// BRIDGE AGENT ROW (Commander Bridge ephemeral sprites)
// ============================================================================
//
// Bridge sprites sit in a dedicated row above the desk area — they are
// *not* desk-bound and must stay out of the y-sort lane used by
// AgentSprite/Boss to prevent z-fighting when a bridge event fires while
// agents are in motion.
//
// Layout envelope chosen after surveying canvas.ts (1280 × 1024):
//   - y = 340 sits below the whiteboard/wall decoration zone (y ≲ 210)
//     and above the first desk bank (desks begin around y ≈ 500).
//   - x = 96 leaves breathing room from the left wall, matches the
//     spacing rhythm the existing office uses.
//   - 96px horizontal spacing keeps sprites distinct without requiring
//     label truncation for short "Dept X" labels.
//   - 120px vertical spacing accounts for bubble + label stacked above
//     the sprite (bubble extends ~80px up).
//
// Wrap at 12 sprites per row: 96 + 11 × 96 = 1152, leaving a safe 128px
// right-margin before the wall clock / water cooler column.

/** Origin of the Bridge agent row (top-left of slot 0). */
export const BRIDGE_ROW_ORIGIN = { x: 96, y: 340 };

/** Horizontal gap between adjacent Bridge sprites. */
export const BRIDGE_ROW_SPACING_X = 96;

/** Vertical gap between Bridge rows when wrapping to a second row. */
export const BRIDGE_ROW_SPACING_Y = 120;

/** Max sprites per row before wrapping — derived from canvas width above. */
export const BRIDGE_ROW_MAX_PER_ROW = 12;

/**
 * Deterministic layout for Bridge sprites.
 *
 * Given a zero-based index (natural insertion order of the
 * `bridgeAgents` Map), returns the absolute canvas position the sprite
 * should render at. Pure function so callers can memoise without
 * worrying about hidden state. Wraps into additional rows past
 * {@link BRIDGE_ROW_MAX_PER_ROW}.
 */
export function bridgeAgentPosition(index: number): { x: number; y: number } {
  const row = Math.floor(index / BRIDGE_ROW_MAX_PER_ROW);
  const col = index % BRIDGE_ROW_MAX_PER_ROW;
  return {
    x: BRIDGE_ROW_ORIGIN.x + col * BRIDGE_ROW_SPACING_X,
    y: BRIDGE_ROW_ORIGIN.y + row * BRIDGE_ROW_SPACING_Y,
  };
}
