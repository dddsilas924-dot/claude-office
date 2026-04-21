/**
 * Space Station Theme — Color palette & layout constants
 *
 * Design: SF Mothership Bridge (Concept C v4)
 * Palette: Deep Blue × Gold × Tactical Blue
 */

// ============================================================================
// PRIMARY PALETTE
// ============================================================================

/** Deep space background */
export const SPACE_BG = 0x030810;

/** Wall base (dark navy) */
export const WALL_BASE = 0x081420;

/** Wall mid-tone */
export const WALL_MID = 0x0c1e30;

/** Wall accent (slightly lighter navy) */
export const WALL_ACCENT = 0x0a1a2c;

/** Armor plate dark */
export const PLATE_DARK = 0x0e1e30;

/** Armor plate mid */
export const PLATE_MID = 0x0a1828;

/** Floor base */
export const FLOOR_BASE = 0x0b1828;

/** Floor dark */
export const FLOOR_DARK = 0x060e18;

// ============================================================================
// ACCENT COLORS
// ============================================================================

/** Gold (primary accent) */
export const GOLD = 0xf1c40f;

/** Tactical blue (secondary accent) */
export const BLUE = 0x4a9eff;

/** Status green */
export const GREEN = 0x00ff88;

/** Warning red */
export const RED = 0xff4444;

/** Violet (design dept) */
export const VIOLET = 0x9b59b6;

// ============================================================================
// ALPHA HELPERS (for procedural glow simulation)
// ============================================================================

/** Gold with custom alpha (returns {color, alpha} for PixiJS fill) */
export function goldAlpha(a: number) {
  return { color: GOLD, alpha: a };
}

/** Blue with custom alpha */
export function blueAlpha(a: number) {
  return { color: BLUE, alpha: a };
}

/** Green with custom alpha */
export function greenAlpha(a: number) {
  return { color: GREEN, alpha: a };
}

/** Red with custom alpha */
export function redAlpha(a: number) {
  return { color: RED, alpha: a };
}

/** White with custom alpha */
export function whiteAlpha(a: number) {
  return { color: 0xffffff, alpha: a };
}

/** Black with custom alpha */
export function blackAlpha(a: number) {
  return { color: 0x000000, alpha: a };
}

// ============================================================================
// WALL LAYOUT
// ============================================================================

/** Wall height in pixels */
export const WALL_HEIGHT = 260;

/** Gold trim strip height at bottom of wall */
export const TRIM_HEIGHT = 16;

/** Armor plate grid: width × height per plate */
export const PLATE_W = 156;
export const PLATE_H = 82;
export const PLATE_GAP_X = 160;
export const PLATE_GAP_Y = 86;

// ============================================================================
// TITLE BANNER LAYOUT
// ============================================================================

export const BANNER_X = 230;
export const BANNER_Y = 4;
export const BANNER_W = 810;
export const BANNER_H = 48;

// ============================================================================
// MAIN SCREEN LAYOUT
// ============================================================================

export const SCREEN_X = 230;
export const SCREEN_Y = 56;
export const SCREEN_W = 810;
export const SCREEN_H = 180;

// ============================================================================
// BLAST DOOR LAYOUT
// ============================================================================

export const DOOR_X = 90;
export const DOOR_Y = 58;
export const DOOR_W = 130;
export const DOOR_H = 186; // WH - 74

// ============================================================================
// RIGHT PANEL (CREW + CLOCK)
// ============================================================================

export const PANEL_X = 1080;
export const PANEL_Y = 56;
export const PANEL_W = 170;
export const PANEL_H = 200;
