#!/usr/bin/env python3
"""CLI pipeline: import logs, compute scores, generate item assignments.

Usage:
    python run_pipeline.py [options] <report_code> [report_code ...]

Options:
    --phase P1          Only show assignments for this phase (default: P1)
    --item "Name"       Only show a specific item (can repeat)
    --all-items         Show all prioritized items, not just LC'd ones

Example:
    python run_pipeline.py mnHGL6CqaYTXPrjQ
    python run_pipeline.py --item "Dragonspine Trophy" mnHGL6CqaYTXPrjQ abc123
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
from database import Database
from wcl_client import WCLClient
from loot_priority import parse_all_priority_csvs
from scoring import (
    score_player, check_consumables, check_class_utility,
    check_interrupts, check_potions,
)
from assignment import (
    RosterPlayer, compute_composite, compute_loot_penalty,
    generate_assignments, format_player_scores, format_assignments,
)


async def save_raid_to_db(db: Database, raid_data, group_id: int, wcl_player_map=None):
    """Persist a fetched raid to the database. Returns raid_id.

    Factored out of the Discord cog so the CLI can reuse it.
    """
    raid_date = datetime.fromtimestamp(
        raid_data.start_time / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d")

    kills = [b for b in raid_data.bosses if b.kill]
    total_duration = sum(b.duration_ms for b in kills)
    kill_durations = [b.duration_ms for b in kills]

    raid_id = await db.insert_raid(
        report_code=raid_data.report_code,
        title=raid_data.title,
        raid_date=raid_date,
        zone=raid_data.zone,
        boss_kills=len(kills),
        wipe_count=raid_data.wipe_count,
        duration_ms=total_duration,
        group_id=group_id,
        imported_by=0,
    )

    for player in raid_data.players:
        player_id = await db.upsert_player(
            name=player.name,
            server=player.server,
            player_class=player.player_class,
        )

        await db.insert_attendance(
            raid_id=raid_id,
            player_id=player_id,
            role=player.role,
            spec=player.spec,
            item_level=player.item_level,
        )

        await db.insert_raid_performance(
            raid_id=raid_id,
            player_id=player_id,
            total_damage=player.total_damage,
            total_healing=player.total_healing,
            total_active_ms=player.active_time_ms,
            total_deaths=player.deaths,
            dps=player.dps,
            hps=player.hps,
            parse_pct=player.parse_pct,
        )

        # Compute utility sub-scores
        cons = check_consumables(player.aura_ids, player.has_weapon_enchant)
        cls_util, _ = check_class_utility(player.cast_ids, player.player_class)
        int_score, _ = check_interrupts(player.interrupts, player.player_class)
        pot_score, _ = check_potions(player.potion_count, kill_durations)
        util_total = cons.score + cls_util + int_score + pot_score

        await db.insert_utility_performance(
            raid_id=raid_id,
            player_id=player_id,
            interrupts=player.interrupts,
            dispels=player.dispels,
            has_flask_or_elixirs=cons.has_flask_or_elixirs,
            has_food_buff=cons.has_food,
            has_weapon_buff=cons.has_weapon,
            has_class_utility=bool(cls_util),
            potion_count=player.potion_count,
            potion_score=pot_score,
            utility_total=util_total,
        )

        # Per-boss performance (including per-boss parse %)
        for boss in kills:
            boss_dmg = raid_data.boss_damage.get(player.name, {}).get(boss.name, 0)
            boss_heal = raid_data.boss_healing.get(player.name, {}).get(boss.name, 0)
            boss_parse = player.boss_parses.get(boss.name, 0.0)
            await db.insert_boss_performance(
                raid_id=raid_id,
                player_id=player_id,
                boss_name=boss.name,
                damage_done=boss_dmg,
                healing_done=boss_heal,
                active_time_ms=0,
                deaths=0,
                parse_pct=boss_parse,
            )

    return raid_id


async def build_roster(db: Database, group_id: int) -> list[RosterPlayer]:
    """Build the full roster with composite scores from DB data."""
    roster_rows = await db.get_roster_with_specs(group_id)
    att_map = await db.get_all_weekly_attendance(group_id, weeks=config.ATTENDANCE_WEEKS)
    parse_map = await db.get_rolling_parse_averages(group_id, weeks=config.ROLLING_AVG_WEEKS)
    util_map = await db.get_rolling_utility_averages(group_id, weeks=config.ROLLING_AVG_WEEKS)
    loot_map = await db.get_loot_counts(group_id, weeks=config.LOOT_PENALTY_DECAY_WEEKS)
    age_map = await db.get_loot_award_ages(group_id)
    detail_att = await db.get_detailed_weekly_attendance(group_id, weeks=config.ATTENDANCE_WEEKS)

    roster = []
    for row in roster_rows:
        rid = row["id"]        # raider identity (for attendance)
        cid = row.get("char_id", rid)  # actual character (for performance, utility, loot)
        p = RosterPlayer(
            player_id=cid,     # use char_id so item exclusion + loot penalty track the character
            name=row["name"],
            player_class=row["class"],
            spec=row["spec"],
            role=row["role"],
            attendance_weeks=att_map.get(rid, 0),   # merged across linked characters
            avg_parse_pct=parse_map.get(cid, 0.0),  # per-character
            avg_utility=util_map.get(cid, 0.0),      # per-character
            loot_count=loot_map.get(cid, 0),          # per-character
            loot_penalty=compute_loot_penalty(age_map.get(cid, [])),  # per-character
            weekly_attendance=detail_att.get(rid, {}),
        )
        p.composite_score = compute_composite(p)
        roster.append(p)

    return roster


# Items actively being loot-counciled.
# Accumulates across phases — old items stay as long as the raid is still run.
LC_ITEMS = [
    "Dragonspine Trophy",       # P1 Gruul
    # "Vashj's Vial Remnant",   # P2 SSC — uncomment when P2 starts
    # "Verdant Sphere",         # P2 TK
]


async def main():
    parser = argparse.ArgumentParser(description="SmugLoot pipeline")
    parser.add_argument("report_codes", nargs="*", help="WCL report codes to import")
    parser.add_argument("--phase", default="P1", help="Current phase (default: P1)")
    parser.add_argument("--item", action="append", dest="items", help="Specific item(s) to show")
    parser.add_argument("--all-items", action="store_true", help="Show all prioritized items")
    parser.add_argument("--fresh", action="store_true", help="Wipe DB and start from scratch")
    parser.add_argument("--group", default="Sunday", help="Raid group name (default: Sunday)")
    args = parser.parse_args()

    report_codes = args.report_codes

    if not report_codes and not Path(config.DB_PATH).exists():
        print("No existing DB and no report codes provided.")
        print("Usage: python run_pipeline.py <report_code> [report_code ...]")
        sys.exit(1)

    if not config.WCL_CLIENT_ID or not config.WCL_CLIENT_SECRET:
        print("ERROR: WCL_CLIENT_ID / WCL_CLIENT_SECRET not set in .env")
        sys.exit(1)

    # ── DB setup ────────────────────────────────────────────────────────
    db_path = Path(config.DB_PATH)
    if args.fresh and db_path.exists():
        os.remove(db_path)
        print("Cleared existing DB (--fresh).")

    db = Database(config.DB_PATH)
    await db.setup()

    group_id = await db.create_raid_group(args.group)
    print(f"Raid group: {args.group} (id={group_id})")

    if not args.fresh and db_path.exists():
        raid_count = await db.get_raid_count(group_id)
        if raid_count:
            print(f"Using existing DB: {raid_count} raid(s) already imported.")
    print()

    # ── Import priority CSVs ────────────────────────────────────────────
    print("=== IMPORTING PRIORITY SHEETS ===")
    project_dir = Path(__file__).parent
    all_items = parse_all_priority_csvs(project_dir)
    if all_items:
        count = await db.import_priority_data(all_items)
        # Summarize by phase/raid
        phase_counts: dict[str, int] = {}
        for item in all_items:
            key = f"{item.phase} {item.raid}".strip()
            phase_counts[key] = phase_counts.get(key, 0) + 1
        for key, cnt in sorted(phase_counts.items()):
            print(f"  {key}: {cnt} items")
        print(f"  Total: {count} items imported\n")
    else:
        print("  No priority CSVs found.\n")

    # ── Fetch WCL reports ───────────────────────────────────────────────
    if report_codes:
        print("=== FETCHING WARCRAFT LOGS ===")
        wcl = WCLClient(config.WCL_CLIENT_ID, config.WCL_CLIENT_SECRET)
        await wcl.setup()

        try:
            for i, code in enumerate(report_codes, 1):
                # Skip already-imported raids
                existing = await db.get_raid_by_code(code)
                if existing:
                    print(f"  [{i}/{len(report_codes)}] {code} — already imported ({existing['raid_date']}), skipping.")
                    continue

                print(f"  [{i}/{len(report_codes)}] Fetching {code}...", end=" ", flush=True)
                try:
                    raid_data = await wcl.fetch_raid_data(code)
                except Exception as e:
                    print(f"ERROR: {e}")
                    continue

                if not raid_data.kill_fight_ids:
                    print("no boss kills, skipping.")
                    continue

                raid_id = await save_raid_to_db(db, raid_data, group_id)
                kills = [b for b in raid_data.bosses if b.kill]
                raid_date = datetime.fromtimestamp(
                    raid_data.start_time / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d")
                print(
                    f'"{raid_data.title}" ({raid_date}) - '
                    f"{len(raid_data.players)} players, {len(kills)} kills"
                )
        finally:
            await wcl.close()

        print()

    # ── Build roster and compute scores ─────────────────────────────────
    print(f"=== PLAYER SCORES ({args.group} group) ===")
    roster = await build_roster(db, group_id)
    if not roster:
        print("  No players found. Check that WCL reports imported correctly.")
        return

    print(format_player_scores(roster))
    print()

    # ── Run pre-assignments ─────────────────────────────────────────────
    raid_count = await db.get_raid_count(group_id)
    received_items = await db.get_received_items(group_id)

    # Determine which items to show
    if args.items:
        item_filter = [n.lower() for n in args.items]
    elif args.all_items:
        item_filter = None  # Show everything for the phase
    else:
        # Default: all active LC items across all phases
        item_filter = [n.lower() for n in LC_ITEMS]

    # Gather items from all phases (LC items can span phases)
    all_chain_items = []
    for phase in ("P1", "P2", "P3"):
        phase_items = await db.get_phase_loot_table(phase)
        all_chain_items.extend(
            it for it in phase_items if it["priority_type"] == "chain"
        )

    if item_filter is not None:
        filtered = [
            it for it in all_chain_items
            if it["item_name"].lower() in item_filter
        ]
    else:
        # --all-items: scope to the specified phase
        filtered = [
            it for it in all_chain_items if it["phase"] == args.phase
        ]

    if not filtered:
        print(f"\n  No LC items found.")
        if not args.all_items and not args.items:
            print(f"  (Active LC items: {LC_ITEMS or ['none']})")
            print(f"  Use --all-items to see everything, or --item \"Name\" to pick specific items.")
    else:
        assignments = generate_assignments(filtered, roster, received_items)
        week_keys = sorted(await db.get_recent_weeks(group_id, weeks=config.ATTENDANCE_WEEKS))
        show_count = 20 if len(filtered) <= 3 else 5
        print(f"\n{'='*60}")
        print(f"  ITEM ASSIGNMENTS — {args.group} ({len(filtered)} item(s), {raid_count} raid(s))")
        print(f"{'='*60}")
        print(format_assignments(assignments, week_keys=week_keys, max_players=show_count))

    # Summary stats
    print("\n=== SUMMARY ===")
    print(f"  Raids imported: {raid_count}")
    print(f"  Players scored: {len(roster)}")
    top = sorted(roster, key=lambda p: -p.composite_score)[:5]
    print(f"  Top 5 scores: {', '.join(f'{p.name} ({p.composite_score:.1f})' for p in top)}")

    # Clean shutdown
    if db.db:
        await db.db.close()


if __name__ == "__main__":
    asyncio.run(main())
