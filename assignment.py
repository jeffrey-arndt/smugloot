"""Pre-assignment engine (Layer 3).

Combines item priority sheets (Layer 2) with player composite scores (Layer 1)
to generate per-item, per-player rankings before raids happen.

Priority rank is a scoring weight (not a hard gate). A Rank 1 player gets a
priority boost, but a Rank 2 player with much better attendance/performance
can still outrank them.

Pure logic — no Discord, no database, no async.
"""

from dataclasses import dataclass, field

import config
from loot_priority import SPEC_ALIAS_MAP


# ── Data structures ─────────────────────────────────────────────────────

@dataclass
class RosterPlayer:
    """A player in the roster with their composite score."""
    player_id: int
    name: str
    player_class: str
    spec: str
    role: str
    # Composite score components
    attendance_weeks: int = 0
    avg_parse_pct: float = 0.0
    avg_utility: float = 0.0
    loot_count: int = 0          # Total LC items received in window (for display)
    loot_penalty: float = 0.0    # Decayed penalty total (for scoring)
    composite_score: float = 0.0  # Base score (no priority)
    # Per-week attendance detail: {week_str: "raid"/"pug"/"manual"}
    weekly_attendance: dict[str, str] = field(default_factory=dict)


@dataclass
class AssignedPlayer:
    """A player slotted into an item's priority list."""
    player: RosterPlayer
    priority_rank: int | None    # From the priority chain (None if not in chain)
    spec_alias: str              # Which alias they matched
    priority_score: float = 0.0  # Priority component (0–1)
    adjusted_score: float = 0.0  # Full composite including priority


@dataclass
class ItemAssignment:
    """Pre-assignment result for a single item."""
    item_name: str
    boss_name: str
    phase: str
    raid: str
    priority_type: str           # "chain", "void", "ms_os"
    chain_display: str           # Human-readable priority chain
    assigned: list[AssignedPlayer] = field(default_factory=list)
    notes: str = ""


# ── Scoring ─────────────────────────────────────────────────────────────

def priority_score_for_rank(rank: int | None) -> float:
    """Convert a priority rank to a 0–1 score.

    Rank 1 = 1.0, each subsequent rank drops by PRIORITY_RANK_DROP.
    None (not in chain) = 0.
    """
    if rank is None:
        return 0.0
    return max(0.0, 1.0 - (rank - 1) * config.PRIORITY_RANK_DROP)


def compute_loot_penalty(award_ages_days: list[float]) -> float:
    """Compute total decayed loot penalty from award ages.

    Each award starts at LOOT_PENALTY_PER_ITEM and linearly decays
    to 0 over LOOT_PENALTY_DECAY_WEEKS.
    """
    decay_days = config.LOOT_PENALTY_DECAY_WEEKS * 7
    total = 0.0
    for age in award_ages_days:
        decay_factor = max(0.0, 1.0 - age / decay_days)
        total += config.LOOT_PENALTY_PER_ITEM * decay_factor
    return round(total, 2)


def compute_composite(player: RosterPlayer, priority_score: float = 0.0) -> float:
    """Compute the weighted composite score for a player.

    Without priority_score, returns base score (max 9.0 of 10).
    With priority_score, returns full adjusted score (max 10.0).
    Loot penalty decays linearly over LOOT_PENALTY_DECAY_WEEKS.

    Tanks are exempt from parse scoring — their performance weight is
    redistributed to attendance and utility so they aren't penalized
    for inherently low DPS parses.
    """
    from scoring import parse_bracket

    att_norm = player.attendance_weeks / config.ATTENDANCE_MAX if config.ATTENDANCE_MAX else 0
    util_norm = player.avg_utility / config.UTILITY_MAX if config.UTILITY_MAX else 0
    prio_norm = priority_score / config.PRIORITY_MAX if config.PRIORITY_MAX else 0

    if player.role == "tank":
        # Redistribute performance weight: 60% to attendance, 40% to utility
        w_att = config.WEIGHT_ATTENDANCE + config.WEIGHT_PERFORMANCE * 0.60
        w_perf = 0.0
        w_util = config.WEIGHT_UTILITY + config.WEIGHT_PERFORMANCE * 0.40
    else:
        w_att = config.WEIGHT_ATTENDANCE
        w_perf = config.WEIGHT_PERFORMANCE
        w_util = config.WEIGHT_UTILITY

    perf_pts, _ = parse_bracket(player.avg_parse_pct)
    perf_norm = perf_pts / config.PERFORMANCE_MAX if config.PERFORMANCE_MAX else 0

    raw = config.SCORE_MAX * (
        w_att * att_norm
        + w_perf * perf_norm
        + w_util * util_norm
        + config.WEIGHT_PRIORITY * prio_norm
    )

    return round(max(0, raw - player.loot_penalty), 1)


# ── Matching ────────────────────────────────────────────────────────────

def _normalize_spec(spec: str) -> str:
    """Normalize spec names for comparison (WCL uses 'BeastMastery', sheets use 'Beast Mastery')."""
    return spec.lower().replace(" ", "")


# WCL hybrid specs → which priority sheet specs they should match.
# These are WCL-invented specializations for ranking fairness:
#   Gladiator  = Prot Warrior doing DPS (off-tank)
#   Champion   = Fury Warrior tanking (Fury Prot)
#   Smite      = Disc Priest doing DPS (not Shadow)
_WCL_SPEC_ALIASES: dict[tuple[str, str], set[str]] = {
    ("Warrior", "gladiator"):  {"fury", "arms", "protection"},
    ("Warrior", "champion"):   {"fury", "arms", "protection"},
    ("Priest", "smite"):       {"holy", "discipline"},
}


def player_matches_entry(
    player_class: str,
    player_spec: str,
    class_options: list[tuple[str, str]],
) -> bool:
    """Check if a player's class/spec matches any of an entry's class_options."""
    norm_player = _normalize_spec(player_spec)

    # Check if this is a WCL hybrid spec with expanded matching
    expanded = _WCL_SPEC_ALIASES.get((player_class, norm_player), None)

    for opt_class, opt_spec in class_options:
        if opt_class != player_class:
            continue
        # Empty spec = any spec of that class
        if not opt_spec:
            return True
        norm_opt = _normalize_spec(opt_spec)
        # Direct match
        if norm_opt == norm_player:
            return True
        # Expanded match for WCL hybrid specs
        if expanded and norm_opt in expanded:
            return True
    return False


def _build_chain_display(entries: list[dict]) -> str:
    """Build a human-readable priority chain string."""
    if not entries:
        return ""
    parts = []
    current_rank = None
    for e in entries:
        alias = e["spec_alias"]
        rank = e["priority_rank"]
        if current_rank is None:
            parts.append(alias)
        elif rank == current_rank:
            parts.append(f"= {alias}")
        else:
            parts.append(f"> {alias}")
        current_rank = rank
    return " ".join(parts)


# ── Assignment engine ───────────────────────────────────────────────────

def generate_assignments(
    items: list[dict],
    roster: list[RosterPlayer],
    received_items: dict[str, set[int]] | None = None,
) -> list[ItemAssignment]:
    """Generate pre-assignments for all items.

    Priority rank is a scoring weight, not a hard gate. Players are ranked
    by their full composite score (attendance + performance + utility + priority).
    A high-performing Rank 2 player can outrank a low-performing Rank 1 player.

    received_items: {item_name: {player_ids}} — players who already own an item
    are excluded from that item's list entirely (can only get it once).
    """
    if received_items is None:
        received_items = {}

    results = []

    for item in items:
        ptype = item["priority_type"]
        item_name = item["item_name"]
        already_have = received_items.get(item_name, set())

        assignment = ItemAssignment(
            item_name=item_name,
            boss_name=item["boss_name"],
            phase=item["phase"],
            raid=item["raid"],
            priority_type=ptype,
            chain_display=_build_chain_display(item.get("entries", [])),
            notes=item.get("notes", ""),
        )

        if ptype == "void":
            results.append(assignment)
            continue

        # Filter roster: exclude players who already have this item
        eligible_roster = [p for p in roster if p.player_id not in already_have]

        # Build player -> (rank, alias) map from priority chain
        entries = item.get("entries", [])
        player_ranks: dict[int, tuple[int, str]] = {}
        has_ms_os = False
        ms_os_rank = 99

        for entry in entries:
            alias = entry["spec_alias"]
            rank = entry["priority_rank"]

            if alias == "MS > OS":
                has_ms_os = True
                ms_os_rank = rank
                continue

            class_options = SPEC_ALIAS_MAP.get(alias, [])
            if not class_options:
                continue

            for p in eligible_roster:
                if p.player_id in player_ranks:
                    continue  # Already matched at a higher priority
                if player_matches_entry(p.player_class, p.spec, class_options):
                    player_ranks[p.player_id] = (rank, alias)

        # MS > OS items: remaining players are eligible at lowest rank
        if has_ms_os:
            for p in eligible_roster:
                if p.player_id not in player_ranks:
                    player_ranks[p.player_id] = (ms_os_rank, "MS > OS")

        # ms_os type (no chain at all): everyone eligible, no priority boost
        if ptype == "ms_os":
            for p in eligible_roster:
                player_ranks[p.player_id] = (ms_os_rank, "MS > OS")

        # Compute adjusted scores and build ranked list
        assigned: list[AssignedPlayer] = []
        for p in eligible_roster:
            if p.player_id not in player_ranks:
                continue
            rank, alias = player_ranks[p.player_id]
            prio = priority_score_for_rank(rank)
            adj = compute_composite(p, priority_score=prio)
            assigned.append(AssignedPlayer(
                player=p,
                priority_rank=rank,
                spec_alias=alias,
                priority_score=prio,
                adjusted_score=adj,
            ))

        # Sort by adjusted score (desc), then fewer loot wins first
        assigned.sort(key=lambda ap: (-ap.adjusted_score, ap.player.loot_count))
        assignment.assigned = assigned
        results.append(assignment)

    return results


# ── Formatting ──────────────────────────────────────────────────────────

def format_player_scores(roster: list[RosterPlayer]) -> str:
    """Format the roster scores as a table (base scores, no priority)."""
    sorted_roster = sorted(roster, key=lambda p: -p.composite_score)

    lines = []
    header = f"{'#':<3} {'Name':<16} {'Class':<10} {'Spec':<14} {'Att':>3} {'Parse':>6} {'Util':>4} {'Loot':>4} {'Base':>5}"
    lines.append(header)
    lines.append("-" * len(header))

    for i, p in enumerate(sorted_roster, 1):
        lines.append(
            f"{i:<3} {p.name:<16} {p.player_class:<10} {p.spec:<14} "
            f"{p.attendance_weeks:>3} {p.avg_parse_pct:>5.1f}% "
            f"{p.avg_utility:>3.0f}/{config.UTILITY_MAX} "
            f"{p.loot_count:>4} {p.composite_score:>5.1f}"
        )

    lines.append(f"\nBase = ATT {config.WEIGHT_ATTENDANCE:.0%} + "
                 f"PERF {config.WEIGHT_PERFORMANCE:.0%} + "
                 f"UTIL {config.WEIGHT_UTILITY:.0%} "
                 f"(max {config.SCORE_MAX * (1 - config.WEIGHT_PRIORITY):.0f}) "
                 f"| +PRIO {config.WEIGHT_PRIORITY:.0%} per item (max {config.SCORE_MAX:.0f})")

    return "\n".join(lines)


def _format_attendance_bar(weekly: dict[str, str], week_keys: list[str]) -> str:
    """Format per-week attendance as a compact bar: ✓ P M -"""
    symbols = {"raid": "✓", "pug": "P", "manual": "M"}
    return " ".join(symbols.get(weekly.get(w, ""), "-") for w in week_keys)


def format_assignments(
    assignments: list[ItemAssignment],
    week_keys: list[str] | None = None,
    max_players: int = 5,
) -> str:
    """Format assignments ranked by adjusted score with full LC context.

    Shows top 5 with per-week attendance, parse avg, utility, loot penalty.
    week_keys: ordered list of week strings (oldest first) for attendance bar.
    """
    lines = []
    current_boss = None

    for a in assignments:
        if a.priority_type == "void":
            continue

        if a.boss_name != current_boss:
            current_boss = a.boss_name
            lines.append("")
            lines.append(f"--- {current_boss} ---")
            lines.append("")

        lines.append(f"  {a.item_name}")
        if a.chain_display:
            lines.append(f"    Priority: {a.chain_display}")

        if not a.assigned:
            lines.append(f"    (no eligible players)")
        else:
            for i, ap in enumerate(a.assigned[:max_players]):
                p = ap.player
                spec_str = (
                    f"{p.spec} {p.player_class}"
                    if p.spec else p.player_class
                )
                rank_tag = f"R{ap.priority_rank}" if ap.priority_rank and ap.priority_rank < 99 else "--"
                adj = ap.adjusted_score

                # Attendance bar
                if week_keys:
                    att_bar = _format_attendance_bar(p.weekly_attendance, week_keys)
                else:
                    att_bar = f"{p.attendance_weeks}/{config.ATTENDANCE_MAX}"

                # Build info line
                parse_str = f"{p.avg_parse_pct:.0f}%" if p.role != "tank" else "tank"
                util_str = f"{p.avg_utility:.0f}/{config.UTILITY_MAX}"
                loot_str = f"-{p.loot_penalty:.1f}L" if p.loot_penalty > 0 else ""

                lines.append(
                    f"    {i+1}. {p.name:<16} {spec_str:<20} "
                    f"{adj:>4.1f}  [{rank_tag}]"
                )
                lines.append(
                    f"       Att [{att_bar}] {p.attendance_weeks}/{config.ATTENDANCE_MAX}  "
                    f"Parse {parse_str}  Util {util_str}"
                    f"{'  ' + loot_str if loot_str else ''}"
                )

            remaining = len(a.assigned) - max_players
            if remaining > 0:
                lines.append(f"       ... +{remaining} more eligible")

        lines.append("")

    return "\n".join(lines)
