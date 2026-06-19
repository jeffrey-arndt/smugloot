"""One-off backfill: grant Holy priests their auto-pass class-utility point
for the most recent N raid weeks (the live scoring window).

Background: `check_class_utility` now auto-passes Holy priests (see
scoring.py), but that only affects newly-imported raids. Rows already in
`utility_performance` still have has_class_utility=0 and a utility_total
that is short by 1. This script corrects the rows that still matter — the
ones inside the current rolling window.

SAFE BY DESIGN:
- Dry-run by default. Prints exactly what would change. Pass --apply to commit.
- Idempotent: only touches rows where has_class_utility=0, so re-running
  (or running after some raids were re-imported) never double-counts.
- Scoped to Holy priests (class='Priest' AND that raid's attendance spec='Holy').
- utility_total is bumped by 1, capped at UTILITY_MAX.
- Runs inside a single transaction.

Usage:
    python backfill_holy_utility.py                 # dry-run, default DB
    python backfill_holy_utility.py --apply         # commit, default DB
    python backfill_holy_utility.py --db /path/to/live.db --apply
    python backfill_holy_utility.py --weeks 4 --apply
"""

import argparse
import asyncio

import config
from database import Database


async def find_targets(db: Database, group_id: int, weeks: int) -> list[dict]:
    """Holy-priest utility rows in the most recent `weeks` raid weeks that
    still have has_class_utility=0."""
    week_keys = await db._recent_raid_weeks(group_id, weeks)
    if not week_keys:
        return []
    wk_ph = ",".join("?" * len(week_keys))
    cursor = await db.db.execute(
        f"""
        SELECT up.id, p.name, a.spec, r.raid_date,
               strftime('%Y-%W', r.raid_date, '-1 day') as week,
               up.has_class_utility, up.utility_total
        FROM utility_performance up
        JOIN raids r ON r.id = up.raid_id
        JOIN attendance a ON a.raid_id = up.raid_id AND a.player_id = up.player_id
        JOIN players p ON p.id = up.player_id
        WHERE r.group_id = ?
          AND strftime('%Y-%W', r.raid_date, '-1 day') IN ({wk_ph})
          AND p.class = 'Priest' AND a.spec = 'Holy' COLLATE NOCASE
          AND up.has_class_utility = 0
        ORDER BY r.raid_date, p.name
        """,
        (group_id, *week_keys),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=config.DB_PATH, help="Path to the SQLite DB")
    ap.add_argument("--weeks", type=int, default=config.ROLLING_AVG_WEEKS,
                    help="How many most-recent raid weeks to backfill")
    ap.add_argument("--apply", action="store_true",
                    help="Commit the changes (default is dry-run preview only)")
    args = ap.parse_args()

    db = Database(args.db)
    await db.setup()

    print(f"DB: {args.db}")
    print(f"Window: most recent {args.weeks} raid weeks per group")
    print(f"Mode: {'APPLY (will commit)' if args.apply else 'DRY-RUN (no changes)'}")
    print("=" * 64)

    groups = await db.get_raid_groups()
    all_target_ids: list[int] = []
    total = 0

    for g in groups:
        targets = await find_targets(db, g["id"], args.weeks)
        if not targets:
            continue
        week_keys = await db._recent_raid_weeks(g["id"], args.weeks)
        print(f"\n[{g['name']}]  window weeks: {week_keys}")
        for t in targets:
            new_total = min(config.UTILITY_MAX, t["utility_total"] + 1)
            print(f"  {t['name']:<16} {t['raid_date']} (wk {t['week']})  "
                  f"class-util 0->1, util {t['utility_total']}->{new_total}")
            all_target_ids.append(t["id"])
            total += 1

    print("\n" + "=" * 64)
    print(f"Rows to fix: {total}")

    if not total:
        print("Nothing to backfill.")
        await db.db.close()
        return

    if not args.apply:
        print("Dry-run only. Re-run with --apply to commit.")
        await db.db.close()
        return

    # Apply: bump in one transaction, guarded again by has_class_utility=0.
    id_ph = ",".join("?" * len(all_target_ids))
    await db.db.execute(
        f"""
        UPDATE utility_performance
        SET has_class_utility = 1,
            utility_total = MIN(?, utility_total + 1)
        WHERE id IN ({id_ph}) AND has_class_utility = 0
        """,
        (config.UTILITY_MAX, *all_target_ids),
    )
    await db.db.commit()

    # Verify none remain.
    remaining = 0
    for g in groups:
        remaining += len(await find_targets(db, g["id"], args.weeks))
    print(f"Applied. Remaining Holy-priest rows with class-util=0 in window: {remaining}")
    await db.db.close()


if __name__ == "__main__":
    asyncio.run(main())
