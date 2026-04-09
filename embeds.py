import discord

import config
from scoring import PlayerScore
from wcl_client import RaidData, PlayerData


def _trend_arrow(delta: float) -> str:
    """Return a trend indicator based on percentage point change."""
    if delta > config.TREND_LARGE:
        return "▲▲"
    elif delta > config.TREND_MODERATE:
        return "▲"
    elif delta < -config.TREND_LARGE:
        return "▼▼"
    elif delta < -config.TREND_MODERATE:
        return "▼"
    return "◆"


def _format_duration(ms: int) -> str:
    """Format milliseconds to Xm Ys."""
    total_secs = ms // 1000
    minutes = total_secs // 60
    seconds = total_secs % 60
    return f"{minutes}m {seconds}s"


def _class_emoji(player_class: str) -> str:
    """Return a simple text indicator for class."""
    short = {
        "Warrior": "WAR", "Paladin": "PAL", "Hunter": "HNT",
        "Rogue": "ROG", "Priest": "PRI", "Shaman": "SHA",
        "Mage": "MAG", "Warlock": "WLK", "Druid": "DRU",
    }
    return short.get(player_class, "???")


def build_raid_summary_embed(raid: RaidData) -> discord.Embed:
    """Build the raid summary embed."""
    kills = [b for b in raid.bosses if b.kill]
    total_fight_ms = sum(b.duration_ms for b in kills)

    embed = discord.Embed(
        title=raid.title,
        color=discord.Color.gold(),
        description=(
            f"**{len(kills)} boss{'es' if len(kills) != 1 else ''} killed** | "
            f"{raid.wipe_count} wipe{'s' if raid.wipe_count != 1 else ''} | "
            f"{len(raid.players)} raiders | "
            f"{_format_duration(total_fight_ms)} total fight time"
        ),
    )

    # Boss list
    boss_lines = []
    for b in kills:
        boss_lines.append(f"✓ {b.name} ({_format_duration(b.duration_ms)})")
    if boss_lines:
        embed.add_field(
            name="Bosses Killed",
            value="\n".join(boss_lines),
            inline=False,
        )

    # Role breakdown
    tanks = [p for p in raid.players if p.role == "tank"]
    healers = [p for p in raid.players if p.role == "healer"]
    dps = [p for p in raid.players if p.role == "dps"]

    if tanks:
        embed.add_field(
            name=f"Tanks ({len(tanks)})",
            value=", ".join(f"{p.name} ({p.player_class})" for p in tanks),
            inline=False,
        )
    if healers:
        embed.add_field(
            name=f"Healers ({len(healers)})",
            value=", ".join(f"{p.name} ({p.player_class})" for p in healers),
            inline=False,
        )
    if dps:
        embed.add_field(
            name=f"DPS ({len(dps)})",
            value=", ".join(f"{p.name}" for p in sorted(dps, key=lambda p: p.dps, reverse=True)),
            inline=False,
        )

    embed.set_footer(text=f"Report: {raid.report_code}")
    return embed


def _short_boss_name(name: str, max_len: int = 5) -> str:
    """Shorten boss name for column headers."""
    shorts = {
        "High King Maulgar": "HKM",
        "Gruul the Dragonkiller": "Gruul",
        "Magtheridon": "Mag",
        "Hydross the Unstable": "Hydro",
        "The Lurker Below": "Lurk",
        "Leotheras the Blind": "Leo",
        "Fathom-Lord Karathress": "Kara",
        "Morogrim Tidewalker": "Moro",
        "Lady Vashj": "Vashj",
        "Void Reaver": "VR",
        "High Astromancer Solarian": "Solar",
        "Al'ar": "Alar",
        "Kael'thas Sunstrider": "KT",
    }
    return shorts.get(name, name[:max_len])


def build_dps_rankings_embed(
    raid: RaidData,
    trends: dict[str, float] | None = None,
) -> discord.Embed:
    """Build DPS rankings embed, sorted by avg parse %."""
    dps_players = sorted(
        [p for p in raid.players if p.role == "dps"],
        key=lambda p: p.parse_pct,
        reverse=True,
    )

    # Get boss kill names for per-boss parse columns
    boss_names = [b.name for b in raid.bosses if b.kill]
    boss_shorts = [_short_boss_name(b) for b in boss_names]

    lines = []
    boss_cols = "".join(f" {b:>5}" for b in boss_shorts)
    header = f"{'#':<3} {'Name':<14} {'DPS':>7} {'Avg':>4}{boss_cols}"
    lines.append(header)
    lines.append("─" * len(header))

    for i, p in enumerate(dps_players, 1):
        avg_str = f"{p.parse_pct:.0f}" if p.parse_pct > 0 else "-"
        boss_parse_strs = ""
        for boss in boss_names:
            bp = p.boss_parses.get(boss)
            boss_parse_strs += f" {bp:>5}" if bp is not None else f" {'—':>5}"
        line = f"{i:<3} {p.name:<14} {p.dps:>7,.0f} {avg_str:>4}{boss_parse_strs}"
        lines.append(line)

    embed = discord.Embed(
        title="DPS Rankings (Boss Kills)",
        description=f"```\n{chr(10).join(lines)}\n```",
        color=discord.Color.red(),
    )
    embed.set_footer(text="Parse % per boss — performance relative to spec globally")
    return embed


def build_healer_rankings_embed(
    raid: RaidData,
    trends: dict[str, float] | None = None,
) -> discord.Embed:
    """Build healer rankings embed, sorted by HPS."""
    healers = sorted(
        [p for p in raid.players if p.role == "healer"],
        key=lambda p: p.hps,
        reverse=True,
    )

    boss_names = [b.name for b in raid.bosses if b.kill]
    boss_shorts = [_short_boss_name(b) for b in boss_names]

    lines = []
    boss_cols = "".join(f" {b:>5}" for b in boss_shorts)
    header = f"{'#':<3} {'Name':<14} {'HPS':>7} {'Avg':>4}{boss_cols}"
    lines.append(header)
    lines.append("─" * len(header))

    for i, p in enumerate(healers, 1):
        avg_str = f"{p.parse_pct:.0f}" if p.parse_pct > 0 else "-"
        boss_parse_strs = ""
        for boss in boss_names:
            bp = p.boss_parses.get(boss)
            boss_parse_strs += f" {bp:>5}" if bp is not None else f" {'—':>5}"
        line = f"{i:<3} {p.name:<14} {p.hps:>7,.0f} {avg_str:>4}{boss_parse_strs}"
        lines.append(line)

    embed = discord.Embed(
        title="Healer Rankings (Boss Kills)",
        description=f"```\n{chr(10).join(lines)}\n```",
        color=discord.Color.green(),
    )
    embed.set_footer(text="Parse % per boss — WCL 'All Bosses' aggregate uses a different calculation")
    return embed


def build_utility_embed(raid: RaidData) -> discord.Embed:
    """Build utility report embed (interrupts, dispels)."""
    # Only show players who did something
    util_players = [
        p for p in raid.players if p.interrupts > 0 or p.dispels > 0
    ]
    util_players.sort(key=lambda p: p.interrupts + p.dispels, reverse=True)

    lines = []
    header = f"{'Name':<16} {'Class':<6} {'Interrupts':>10} {'Dispels':>8} {'Total':>6}"
    lines.append(header)
    lines.append("─" * len(header))

    for p in util_players:
        total = p.interrupts + p.dispels
        lines.append(
            f"{p.name:<16} {_class_emoji(p.player_class):<6} "
            f"{p.interrupts:>10} {p.dispels:>8} {total:>6}"
        )

    if not util_players:
        lines.append("No interrupt or dispel data recorded.")

    embed = discord.Embed(
        title="Utility Report",
        description=f"```\n{chr(10).join(lines)}\n```",
        color=discord.Color.blue(),
    )
    embed.set_footer(text="Interrupts & dispels — doing your job matters.")
    return embed


def build_loot_priority_embed(scores: list[PlayerScore]) -> discord.Embed:
    """Build loot priority rankings from scored player data."""
    sorted_scores = sorted(scores, key=lambda s: s.total_score, reverse=True)

    lines = []
    header = f"{'#':<3} {'Name':<14} {'Score':>5}  {'Att':>3} {'Parse':>6} {'Util':>4}"
    lines.append(header)
    lines.append("─" * len(header))

    for i, s in enumerate(sorted_scores, 1):
        att_str = f"{s.attendance_pts}/{config.ATTENDANCE_MAX}"
        parse_str = s.parse_bracket_name[:6]
        util_str = f"{s.utility_pts}/{config.UTILITY_MAX}"
        line = (
            f"{i:<3} {s.name:<14} {s.total_score:>5.1f}"
            f"  {att_str:>3} {parse_str:>6} {util_str:>4}"
        )
        lines.append(line)

    embed = discord.Embed(
        title="Loot Priority Scores",
        description=f"```\n{chr(10).join(lines)}\n```",
        color=discord.Color.purple(),
    )
    embed.set_footer(
        text=(
            f"0-{config.SCORE_MAX:.0f} scale | "
            f"ATT {config.WEIGHT_ATTENDANCE:.0%}  "
            f"PERF {config.WEIGHT_PERFORMANCE:.0%}  "
            f"UTIL {config.WEIGHT_UTILITY:.0%}"
        )
    )
    return embed


def build_utility_detail_embed(scores: list[PlayerScore]) -> discord.Embed:
    """Build a detailed utility breakdown per player."""
    sorted_scores = sorted(scores, key=lambda s: s.utility_pts, reverse=True)

    lines = []
    header = f"{'Name':<14} {'Cons':>6} {'Job':>4} {'Int':>5} {'Total':>5}"
    lines.append(header)
    lines.append("─" * len(header))

    for s in sorted_scores:
        cons_str = "Yes" if s.consumable_score else "No"
        lines.append(
            f"{s.name:<14} {cons_str:>6} "
            f"{s.class_utility_detail:>4} "
            f"{s.interrupt_detail:>5} "
            f"{s.utility_pts:>4}/{config.UTILITY_MAX}"
        )

    embed = discord.Embed(
        title="Utility Breakdown",
        description=f"```\n{chr(10).join(lines)}\n```",
        color=discord.Color.blue(),
    )
    embed.set_footer(
        text="Cons = Flask+Food+Oil | Job = Class utility | Int = Interrupts"
    )
    return embed
