"""Dept persona display profiles for Claude Office hooks.

Mirror of ``scripts/commander_bridge_public/app/personas.py`` in the
Commander Bridge repo. Kept in sync manually — if you add a new dept
there, copy the PersonaProfile entry here too.

Why a copy (not an import):
- hooks ship as a standalone uv project and run inside ``claude-office``
  worktrees that don't have ``commander_bridge_public`` on the
  PYTHONPATH.
- Sync cost is trivial (14 dept entries), and decoupling prevents the
  hooks binary from breaking when the Bridge dir moves.

Used by ``event_mapper.py`` to attach Japanese display labels to
subagent events so the Claude Office canvas shows "🔬 リサーチ事業部 リョウ"
instead of "research_division".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaProfile:
    emoji: str
    division_jp: str
    character: str
    color: int  # 0xRRGGBB — matches Discord embed color so the canvas
    # stays visually consistent with the bot's messages.


UNKNOWN_PROFILE = PersonaProfile(
    emoji="❓",
    division_jp="未設定部門",
    character="名無し",
    color=0x95A5A6,
)


# Primary map: dept_id → PersonaProfile.
DEPT_PROFILES: dict[str, PersonaProfile] = {
    "commander": PersonaProfile(
        emoji="🧠", division_jp="司令塔", character="フィル", color=0xF1C40F
    ),
    "research": PersonaProfile(
        emoji="🔬", division_jp="リサーチ事業部", character="リョウ", color=0x3498DB
    ),
    "content": PersonaProfile(
        emoji="🎬", division_jp="コンテンツ事業部", character="ハル", color=0xE74C3C
    ),
    "writing": PersonaProfile(
        emoji="✍️", division_jp="ライティング事業部", character="カイ", color=0xF39C12
    ),
    "design": PersonaProfile(
        emoji="🎨", division_jp="デザイン事業部", character="リック", color=0x9B59B6
    ),
    "sales": PersonaProfile(
        emoji="💼", division_jp="営業事業部", character="レイ", color=0x2ECC71
    ),
    "advertising": PersonaProfile(
        emoji="📣", division_jp="広告部", character="エナ", color=0xE67E22
    ),
    "new_biz": PersonaProfile(
        emoji="🚀", division_jp="新規事業開発部", character="タダシ", color=0x1ABC9C
    ),
    "phil": PersonaProfile(
        emoji="🎓", division_jp="フィルコンサル事業部", character="フィル", color=0xF1C40F
    ),
    "ai_inv": PersonaProfile(
        emoji="💰", division_jp="AI投資エージェント部", character="アキ", color=0x27AE60
    ),
    "real_estate": PersonaProfile(
        emoji="🏠", division_jp="不動産事業部", character="アイリ", color=0x16A085
    ),
    "origin": PersonaProfile(
        emoji="🪞", division_jp="コピーロボット部", character="エム吉", color=0xBDC3C7
    ),
    "security": PersonaProfile(
        emoji="🔒", division_jp="セキュリティ体制整備", character="モード", color=0x34495E
    ),
    "doradora_sns": PersonaProfile(
        emoji="📱", division_jp="どらどらSNS運用", character="ソラ", color=0xE91E63
    ),
}


# Alias map: other names Claude Code might use for the same dept.
# Kept separate so the primary map stays a 1:1 truth source.
# Reason: Claude Code subagent_type strings come from three sources —
# (1) our dept_ids, (2) legacy ``*_division`` names, (3) ad-hoc English
# labels like "research-analyst". The aliases resolve all three back to
# a single dept_id without polluting DEPT_PROFILES.
DEPT_ALIASES: dict[str, str] = {
    # Legacy division names (used by older dept registry entries)
    "commander_hq": "commander",
    "research_division": "research",
    "content_division": "content",
    "writing_division": "writing",
    "design_division": "design",
    "sales_division": "sales",
    "advertising_division": "advertising",
    "ad_division": "advertising",
    "new_biz_division": "new_biz",
    "phil_consulting": "phil",
    "ai_investment": "ai_inv",
    "ai_inv_division": "ai_inv",
    "real_estate_division": "real_estate",
    "origin_story": "origin",
    "copy_robot": "origin",
    "security_division": "security",
    # English-ish labels
    "research-analyst": "research",
    "content-creator": "content",
    "writer": "writing",
    "designer": "design",
    "salesperson": "sales",
    # Built-in Claude Code subagent types that clearly map to a dept
    "researcher": "research",
    "copy-writer": "writing",
    "visual-designer": "design",
    "creative-strategist": "advertising",
}


def canonical_dept_id(dept_id: str) -> str | None:
    """Resolve alias → canonical dept_id, or None if unknown.

    Separate from profile_for so callers that only need the canonical
    name (e.g. for logging, grouping, downstream routing) don't pay the
    cost of a PersonaProfile lookup.
    """
    if not dept_id:
        return None
    canonical = DEPT_ALIASES.get(dept_id, dept_id)
    if canonical in DEPT_PROFILES:
        return canonical
    return None


def profile_for(dept_id: str) -> PersonaProfile:
    """Resolve any known dept_id / alias → PersonaProfile.

    Unknown ids fall back to UNKNOWN_PROFILE so the canvas never blanks
    out — better to show "❓ 未設定部門 名無し" than to crash.
    """
    canonical = canonical_dept_id(dept_id)
    if canonical is None:
        return UNKNOWN_PROFILE
    return DEPT_PROFILES[canonical]


def display_label(dept_id: str) -> str:
    """Canvas label: '🔬 リサーチ事業部 リョウ'."""
    p = profile_for(dept_id)
    return f"{p.emoji} {p.division_jp} {p.character}"


def short_label(dept_id: str) -> str:
    """Compact sprite label: '🔬 リョウ' — fits under the 48px agent body."""
    p = profile_for(dept_id)
    return f"{p.emoji} {p.character}"


def is_known_dept(dept_id: str) -> bool:
    """True when the id resolves to a registered dept (not fallback)."""
    return canonical_dept_id(dept_id) is not None
