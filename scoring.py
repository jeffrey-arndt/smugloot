"""Loot priority scoring engine.

All tunable constants live in config.py. This module is pure logic —
no Discord, no database, no async. Easy to test standalone.
"""

from dataclasses import dataclass, field

import config


# ── Parse bracket helpers ─────────────────────────────────────────────────

def parse_bracket(parse_pct: float) -> tuple[int, str]:
    """Map an average parse % to (points, bracket_name).

    Returns (1, "Gray") for the lowest bracket, up to (7, "Gold").
    """
    for min_pct, points, name in config.PARSE_BRACKETS:
        if parse_pct >= min_pct:
            return points, name
    return 1, "Gray"


# ── Consumable detection ─────────────────────────────────────────────────

@dataclass
class ConsumableResult:
    has_flask_or_elixirs: bool = False
    has_food: bool = False
    has_weapon: bool = False

    @property
    def passed(self) -> bool:
        return self.has_flask_or_elixirs and self.has_food and self.has_weapon

    @property
    def score(self) -> int:
        return 1 if self.passed else 0

    @property
    def detail(self) -> str:
        parts = []
        parts.append("Flask" if self.has_flask_or_elixirs else "xFlask")
        parts.append("Food" if self.has_food else "xFood")
        parts.append("Oil" if self.has_weapon else "xOil")
        return "+".join(parts)


def check_consumables(aura_ids: set[int], has_weapon_enchant: bool = False) -> ConsumableResult:
    """Check a player's CombatantInfo auras for consumable buffs.

    has_weapon_enchant comes from gear[slot].temporaryEnchant in CombatantInfo,
    NOT from the auras array (weapon oils/stones are reported separately).
    """
    has_flask = bool(aura_ids & config.FLASK_IDS)
    has_both_elixirs = (
        bool(aura_ids & config.BATTLE_ELIXIR_IDS)
        and bool(aura_ids & config.GUARDIAN_ELIXIR_IDS)
    )
    return ConsumableResult(
        has_flask_or_elixirs=has_flask or has_both_elixirs,
        has_food=bool(aura_ids & config.FOOD_BUFF_IDS),
        has_weapon=has_weapon_enchant,
    )


# ── Class utility detection ──────────────────────────────────────────────

def check_class_utility(cast_ids: set[int], player_class: str) -> tuple[int, str]:
    """Check if a player cast their expected utility spells.

    Returns (score, detail_str). Classes with no tracked spells auto-pass.
    """
    expected = config.CLASS_UTILITY_SPELLS.get(player_class, set())
    if not expected:
        return 1, "N/A"
    matched = cast_ids & expected
    if matched:
        return 1, "Yes"
    return 0, "No"


# ── Interrupt scoring ────────────────────────────────────────────────────

def check_interrupts(count: int, player_class: str) -> tuple[int, str]:
    """Score interrupts. Non-interrupt classes auto-pass.

    Returns (score, detail_str).
    """
    if player_class not in config.INTERRUPT_CLASSES:
        return 1, "N/A"
    if count >= config.INTERRUPT_THRESHOLD:
        return 1, str(count)
    return 0, str(count)


# ── Potion scoring ────────────────────────────────────────────────────────

def expected_potions(fight_durations_ms: list[int]) -> int:
    """Calculate expected potion uses: 1 per boss kill."""
    return len(fight_durations_ms)


def check_potions(potion_count: int, fight_durations_ms: list[int]) -> tuple[int, str]:
    """Score potion usage: pass if at least 1 potion per boss kill.

    Returns (score, detail_str).
    """
    expected = expected_potions(fight_durations_ms)
    if expected <= 0:
        return 0, "0"
    if potion_count >= expected:
        return 1, str(potion_count)
    return 0, str(potion_count)


# ── Combined player score ────────────────────────────────────────────────

@dataclass
class PlayerScore:
    name: str
    player_class: str

    # Raw inputs
    attendance_weeks: int = 0
    parse_pct: float = 0.0
    interrupt_count: int = 0

    # Sub-scores (0 or 1 each)
    consumable_score: int = 0
    class_utility_score: int = 0
    interrupt_score: int = 0
    potion_score: int = 0

    # Detail strings for display
    consumable_detail: str = ""
    class_utility_detail: str = ""
    interrupt_detail: str = ""
    potion_detail: str = ""
    parse_bracket_name: str = "Gray"

    # Computed points per category
    attendance_pts: int = 0      # 0-4
    performance_pts: int = 0     # 1-7
    utility_pts: int = 0         # 0-3

    # Final weighted score
    total_score: float = 0.0


def score_player(
    name: str,
    player_class: str,
    parse_pct: float,
    interrupt_count: int,
    aura_ids: set[int],
    cast_ids: set[int],
    attendance_weeks: int = 1,
    has_weapon_enchant: bool = False,
    potion_count: int = 0,
    fight_durations_ms: list[int] | None = None,
) -> PlayerScore:
    """Calculate the full loot priority score for one player.

    attendance_weeks: how many of the last ATTENDANCE_WEEKS weeks they attended.
    """
    # Performance
    perf_pts, bracket_name = parse_bracket(parse_pct)

    # Utility sub-scores
    cons = check_consumables(aura_ids, has_weapon_enchant)
    class_util_score, class_util_detail = check_class_utility(cast_ids, player_class)
    int_score, int_detail = check_interrupts(interrupt_count, player_class)
    pot_score, pot_detail = check_potions(potion_count, fight_durations_ms or [])
    util_pts = cons.score + class_util_score + int_score + pot_score

    # Weighted total (0 – SCORE_MAX)
    att_norm = attendance_weeks / config.ATTENDANCE_MAX if config.ATTENDANCE_MAX else 0
    perf_norm = perf_pts / config.PERFORMANCE_MAX if config.PERFORMANCE_MAX else 0
    util_norm = util_pts / config.UTILITY_MAX if config.UTILITY_MAX else 0

    total = config.SCORE_MAX * (
        config.WEIGHT_ATTENDANCE * att_norm
        + config.WEIGHT_PERFORMANCE * perf_norm
        + config.WEIGHT_UTILITY * util_norm
    )

    return PlayerScore(
        name=name,
        player_class=player_class,
        attendance_weeks=attendance_weeks,
        parse_pct=parse_pct,
        interrupt_count=interrupt_count,
        consumable_score=cons.score,
        class_utility_score=class_util_score,
        interrupt_score=int_score,
        potion_score=pot_score,
        consumable_detail=cons.detail,
        class_utility_detail=class_util_detail,
        interrupt_detail=int_detail,
        potion_detail=pot_detail,
        parse_bracket_name=bracket_name,
        attendance_pts=attendance_weeks,
        performance_pts=perf_pts,
        utility_pts=util_pts,
        total_score=round(total, 1),
    )
