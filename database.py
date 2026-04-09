import aiosqlite


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: aiosqlite.Connection | None = None

    async def setup(self):
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS raid_groups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS raids (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                report_code     TEXT NOT NULL UNIQUE,
                title           TEXT NOT NULL,
                raid_date       TEXT NOT NULL,
                zone            TEXT NOT NULL DEFAULT '',
                boss_kills      INTEGER NOT NULL DEFAULT 0,
                wipe_count      INTEGER NOT NULL DEFAULT 0,
                duration_ms     INTEGER NOT NULL DEFAULT 0,
                group_id        INTEGER NOT NULL REFERENCES raid_groups(id),
                imported_by     INTEGER NOT NULL,
                imported_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                server          TEXT NOT NULL DEFAULT '',
                class           TEXT NOT NULL,
                main_id         INTEGER REFERENCES players(id),
                UNIQUE(name, server)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_id         INTEGER NOT NULL REFERENCES raids(id),
                player_id       INTEGER NOT NULL REFERENCES players(id),
                role            TEXT NOT NULL DEFAULT 'dps',
                spec            TEXT NOT NULL DEFAULT '',
                item_level      REAL NOT NULL DEFAULT 0,
                benched         INTEGER NOT NULL DEFAULT 0,
                UNIQUE(raid_id, player_id)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS boss_performance (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_id         INTEGER NOT NULL REFERENCES raids(id),
                player_id       INTEGER NOT NULL REFERENCES players(id),
                boss_name       TEXT NOT NULL,
                damage_done     INTEGER NOT NULL DEFAULT 0,
                healing_done    INTEGER NOT NULL DEFAULT 0,
                active_time_ms  INTEGER NOT NULL DEFAULT 0,
                deaths          INTEGER NOT NULL DEFAULT 0,
                parse_pct       REAL NOT NULL DEFAULT 0,
                UNIQUE(raid_id, player_id, boss_name)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS raid_performance (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_id         INTEGER NOT NULL REFERENCES raids(id),
                player_id       INTEGER NOT NULL REFERENCES players(id),
                total_damage    INTEGER NOT NULL DEFAULT 0,
                total_healing   INTEGER NOT NULL DEFAULT 0,
                total_active_ms INTEGER NOT NULL DEFAULT 0,
                total_deaths    INTEGER NOT NULL DEFAULT 0,
                dps             REAL NOT NULL DEFAULT 0,
                hps             REAL NOT NULL DEFAULT 0,
                parse_pct       REAL NOT NULL DEFAULT 0,
                UNIQUE(raid_id, player_id)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS utility_performance (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_id             INTEGER NOT NULL REFERENCES raids(id),
                player_id           INTEGER NOT NULL REFERENCES players(id),
                interrupts          INTEGER NOT NULL DEFAULT 0,
                dispels             INTEGER NOT NULL DEFAULT 0,
                has_flask_or_elixirs INTEGER NOT NULL DEFAULT 0,
                has_food_buff       INTEGER NOT NULL DEFAULT 0,
                has_weapon_buff     INTEGER NOT NULL DEFAULT 0,
                has_class_utility   INTEGER NOT NULL DEFAULT 0,
                potion_count    INTEGER NOT NULL DEFAULT 0,
                potion_score    INTEGER NOT NULL DEFAULT 0,
                utility_total   INTEGER NOT NULL DEFAULT 0,
                UNIQUE(raid_id, player_id)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS gear_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_id         INTEGER NOT NULL REFERENCES raids(id),
                player_id       INTEGER NOT NULL REFERENCES players(id),
                slot            INTEGER NOT NULL,
                item_id         INTEGER NOT NULL,
                item_name       TEXT NOT NULL DEFAULT '',
                item_level      INTEGER NOT NULL DEFAULT 0,
                quality         INTEGER NOT NULL DEFAULT 0,
                UNIQUE(raid_id, player_id, slot)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS item_priorities (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name       TEXT NOT NULL,
                boss_name       TEXT NOT NULL DEFAULT '',
                phase           TEXT NOT NULL,
                raid            TEXT NOT NULL DEFAULT '',
                priority_type   TEXT NOT NULL DEFAULT 'chain',
                skew            TEXT NOT NULL DEFAULT '',
                notes           TEXT NOT NULL DEFAULT '',
                UNIQUE(item_name, phase, skew)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS item_priority_entries (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                item_priority_id    INTEGER NOT NULL REFERENCES item_priorities(id) ON DELETE CASCADE,
                priority_rank       INTEGER NOT NULL,
                spec_alias          TEXT NOT NULL
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS loot_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id       INTEGER NOT NULL REFERENCES players(id),
                item_name       TEXT NOT NULL,
                group_id        INTEGER NOT NULL REFERENCES raid_groups(id),
                raid_id         INTEGER REFERENCES raids(id),
                awarded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes           TEXT NOT NULL DEFAULT ''
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS attendance_credit (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id       INTEGER NOT NULL REFERENCES players(id),
                group_id        INTEGER NOT NULL REFERENCES raid_groups(id),
                week            TEXT NOT NULL,
                credit_type     TEXT NOT NULL DEFAULT 'manual',
                source          TEXT NOT NULL DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(player_id, group_id, week)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS mechanic_overrides (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_id         INTEGER NOT NULL REFERENCES raids(id),
                player_id       INTEGER NOT NULL REFERENCES players(id),
                boss_name       TEXT NOT NULL,
                created_by      INTEGER NOT NULL,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(raid_id, player_id, boss_name)
            )
        """)

        # Migration: add parse_pct to boss_performance if missing
        try:
            await self.db.execute("ALTER TABLE boss_performance ADD COLUMN parse_pct REAL NOT NULL DEFAULT 0")
            await self.db.commit()
        except Exception:
            pass  # Column already exists

        await self.db.commit()

    # --- Raid Groups ---

    async def get_raid_groups(self) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM raid_groups ORDER BY id"
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_raid_group_by_name(self, name: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM raid_groups WHERE name = ? COLLATE NOCASE", (name,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_raid_group(self, name: str) -> int:
        cursor = await self.db.execute(
            "INSERT OR IGNORE INTO raid_groups (name) VALUES (?)", (name,)
        )
        await self.db.commit()
        if cursor.lastrowid:
            return cursor.lastrowid
        row = await (await self.db.execute(
            "SELECT id FROM raid_groups WHERE name = ?", (name,)
        )).fetchone()
        return row["id"]

    # --- Raids ---

    async def get_raid_by_code(self, report_code: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM raids WHERE report_code = ?", (report_code,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def insert_raid(
        self, report_code: str, title: str, raid_date: str, zone: str,
        boss_kills: int, wipe_count: int, duration_ms: int,
        group_id: int, imported_by: int,
    ) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO raids (report_code, title, raid_date, zone, boss_kills,
                               wipe_count, duration_ms, group_id, imported_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (report_code, title, raid_date, zone, boss_kills, wipe_count,
             duration_ms, group_id, imported_by),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def get_recent_raids(self, limit: int = 8, group_id: int | None = None) -> list[dict]:
        if group_id is not None:
            cursor = await self.db.execute(
                "SELECT * FROM raids WHERE group_id = ? ORDER BY raid_date DESC LIMIT ?",
                (group_id, limit),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM raids ORDER BY raid_date DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_raid_count(self, group_id: int | None = None) -> int:
        if group_id is not None:
            cursor = await self.db.execute(
                "SELECT COUNT(*) as cnt FROM raids WHERE group_id = ?", (group_id,)
            )
        else:
            cursor = await self.db.execute("SELECT COUNT(*) as cnt FROM raids")
        return (await cursor.fetchone())["cnt"]

    # --- Players ---

    async def upsert_player(self, name: str, server: str, player_class: str) -> int:
        cursor = await self.db.execute(
            "SELECT id FROM players WHERE name = ? AND server = ?", (name, server),
        )
        row = await cursor.fetchone()
        if row:
            return row["id"]
        cursor = await self.db.execute(
            "INSERT INTO players (name, server, class) VALUES (?, ?, ?)",
            (name, server, player_class),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def get_player_by_name(self, name: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM players WHERE name = ? COLLATE NOCASE", (name,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    # --- Character Linking ---

    async def link_characters(self, main_id: int, alt_id: int):
        """Link an alt to a main character."""
        await self.db.execute(
            "UPDATE players SET main_id = ? WHERE id = ?", (main_id, alt_id),
        )
        await self.db.commit()

    async def unlink_character(self, player_id: int):
        """Remove a character's main link."""
        await self.db.execute(
            "UPDATE players SET main_id = NULL WHERE id = ?", (player_id,),
        )
        await self.db.commit()

    async def get_linked_characters(self, main_id: int) -> list[dict]:
        """Get all alts linked to a main character."""
        cursor = await self.db.execute(
            "SELECT * FROM players WHERE main_id = ?", (main_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_all_links(self) -> list[dict]:
        """Get all linked characters with their main's name."""
        cursor = await self.db.execute(
            """
            SELECT alt.id as alt_id, alt.name as alt_name, alt.class as alt_class,
                   main.id as main_id, main.name as main_name, main.class as main_class
            FROM players alt
            JOIN players main ON alt.main_id = main.id
            ORDER BY main.name, alt.name
            """
        )
        return [dict(r) for r in await cursor.fetchall()]

    # --- Attendance ---

    async def insert_attendance(
        self, raid_id: int, player_id: int, role: str, spec: str,
        item_level: float, benched: bool = False,
    ):
        await self.db.execute(
            """
            INSERT OR IGNORE INTO attendance (raid_id, player_id, role, spec, item_level, benched)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (raid_id, player_id, role, spec, item_level, int(benched)),
        )
        await self.db.commit()

    async def set_benched(self, raid_id: int, player_id: int, benched: bool):
        await self.db.execute(
            "UPDATE attendance SET benched = ? WHERE raid_id = ? AND player_id = ?",
            (int(benched), raid_id, player_id),
        )
        await self.db.commit()

    async def get_player_attendance(self, player_id: int, limit: int = 8) -> list[dict]:
        cursor = await self.db.execute(
            """
            SELECT a.*, r.raid_date, r.title, r.report_code
            FROM attendance a JOIN raids r ON r.id = a.raid_id
            WHERE a.player_id = ?
            ORDER BY r.raid_date DESC LIMIT ?
            """,
            (player_id, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]

    # --- Performance ---

    async def insert_raid_performance(
        self, raid_id: int, player_id: int,
        total_damage: int, total_healing: int, total_active_ms: int,
        total_deaths: int, dps: float, hps: float, parse_pct: float = 0.0,
    ):
        await self.db.execute(
            """
            INSERT OR IGNORE INTO raid_performance
                (raid_id, player_id, total_damage, total_healing,
                 total_active_ms, total_deaths, dps, hps, parse_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (raid_id, player_id, total_damage, total_healing,
             total_active_ms, total_deaths, dps, hps, parse_pct),
        )
        await self.db.commit()

    async def insert_boss_performance(
        self, raid_id: int, player_id: int, boss_name: str,
        damage_done: int, healing_done: int, active_time_ms: int, deaths: int,
        parse_pct: float = 0.0,
    ):
        await self.db.execute(
            """
            INSERT OR IGNORE INTO boss_performance
                (raid_id, player_id, boss_name, damage_done, healing_done,
                 active_time_ms, deaths, parse_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (raid_id, player_id, boss_name, damage_done, healing_done,
             active_time_ms, deaths, parse_pct),
        )
        await self.db.commit()

    async def get_player_performance_history(self, player_id: int, limit: int = 8) -> list[dict]:
        cursor = await self.db.execute(
            """
            SELECT rp.*, r.raid_date, r.title
            FROM raid_performance rp JOIN raids r ON r.id = rp.raid_id
            WHERE rp.player_id = ?
            ORDER BY r.raid_date DESC LIMIT ?
            """,
            (player_id, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]

    # --- Utility ---

    async def insert_utility_performance(
        self, raid_id: int, player_id: int, interrupts: int, dispels: int,
        has_flask_or_elixirs: bool = False, has_food_buff: bool = False,
        has_weapon_buff: bool = False, has_class_utility: bool = False,
        potion_count: int = 0, potion_score: int = 0, utility_total: int = 0,
    ):
        await self.db.execute(
            """
            INSERT OR IGNORE INTO utility_performance
                (raid_id, player_id, interrupts, dispels,
                 has_flask_or_elixirs, has_food_buff, has_weapon_buff,
                 has_class_utility, potion_count, potion_score, utility_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (raid_id, player_id, interrupts, dispels,
             int(has_flask_or_elixirs), int(has_food_buff),
             int(has_weapon_buff), int(has_class_utility),
             potion_count, potion_score, utility_total),
        )
        await self.db.commit()

    async def get_player_utility_history(self, player_id: int, limit: int = 8) -> list[dict]:
        cursor = await self.db.execute(
            """
            SELECT up.*, r.raid_date, r.title
            FROM utility_performance up JOIN raids r ON r.id = up.raid_id
            WHERE up.player_id = ?
            ORDER BY r.raid_date DESC LIMIT ?
            """,
            (player_id, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]

    # --- Item Priorities (global, not group-scoped) ---

    async def import_priority_data(self, items: list) -> int:
        cleared_phases: set[str] = set()
        count = 0
        for item in items:
            phase_key = f"{item.phase}|{item.raid}"
            if phase_key not in cleared_phases:
                await self.clear_phase_priorities(item.phase, item.raid)
                cleared_phases.add(phase_key)
            prio_id = await self._insert_item_priority(item)
            for entry in item.entries:
                await self._insert_priority_entry(prio_id, entry)
            count += 1
        await self.db.commit()
        return count

    async def _insert_item_priority(self, item) -> int:
        cursor = await self.db.execute(
            """
            INSERT OR REPLACE INTO item_priorities
                (item_name, boss_name, phase, raid, priority_type, skew, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (item.item_name, item.boss_name, item.phase, item.raid,
             item.priority_type, item.skew, item.notes),
        )
        return cursor.lastrowid

    async def _insert_priority_entry(self, item_priority_id: int, entry) -> None:
        await self.db.execute(
            "INSERT INTO item_priority_entries (item_priority_id, priority_rank, spec_alias) VALUES (?, ?, ?)",
            (item_priority_id, entry.rank, entry.spec_alias),
        )

    async def get_item_priority(self, item_name: str, phase: str = "", skew: str = "") -> dict | None:
        conditions = ["item_name = ? COLLATE NOCASE"]
        params: list = [item_name]
        if phase:
            conditions.append("phase = ?")
            params.append(phase)
        if skew:
            conditions.append("skew = ?")
            params.append(skew)
        else:
            conditions.append("skew = ''")
        where = " AND ".join(conditions)
        cursor = await self.db.execute(f"SELECT * FROM item_priorities WHERE {where}", params)
        row = await cursor.fetchone()
        if not row:
            return None
        prio = dict(row)
        cursor = await self.db.execute(
            "SELECT priority_rank, spec_alias FROM item_priority_entries WHERE item_priority_id = ? ORDER BY priority_rank, id",
            (prio["id"],),
        )
        prio["entries"] = [dict(r) for r in await cursor.fetchall()]
        return prio

    async def get_phase_loot_table(self, phase: str) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM item_priorities WHERE phase = ? ORDER BY id", (phase,),
        )
        items = [dict(r) for r in await cursor.fetchall()]
        for item in items:
            cur = await self.db.execute(
                "SELECT priority_rank, spec_alias FROM item_priority_entries WHERE item_priority_id = ? ORDER BY priority_rank, id",
                (item["id"],),
            )
            item["entries"] = [dict(r) for r in await cur.fetchall()]
        return items

    async def clear_phase_priorities(self, phase: str, raid: str = "") -> None:
        if raid:
            await self.db.execute(
                "DELETE FROM item_priority_entries WHERE item_priority_id IN (SELECT id FROM item_priorities WHERE phase = ? AND raid = ?)",
                (phase, raid),
            )
            await self.db.execute("DELETE FROM item_priorities WHERE phase = ? AND raid = ?", (phase, raid))
        else:
            await self.db.execute(
                "DELETE FROM item_priority_entries WHERE item_priority_id IN (SELECT id FROM item_priorities WHERE phase = ?)",
                (phase,),
            )
            await self.db.execute("DELETE FROM item_priorities WHERE phase = ?", (phase,))
        await self.db.commit()

    # --- Attendance by week (group-scoped) ---

    async def get_weekly_attendance(self, player_id: int, group_id: int, weeks: int = 4) -> int:
        """Count distinct weeks a raider attended within the last N weeks for a group.

        Resolves through main_id — counts any linked character's attendance.
        """
        # Resolve to raider_id
        cursor = await self.db.execute(
            "SELECT COALESCE(main_id, id) as raider_id FROM players WHERE id = ?",
            (player_id,),
        )
        row = await cursor.fetchone()
        raider_id = row["raider_id"] if row else player_id

        # Get all character IDs for this raider
        cursor = await self.db.execute(
            "SELECT id FROM players WHERE id = ? OR main_id = ?",
            (raider_id, raider_id),
        )
        char_ids = [r["id"] for r in await cursor.fetchall()]
        placeholders = ",".join("?" * len(char_ids))

        cursor = await self.db.execute(
            f"""
            SELECT COUNT(DISTINCT week) as weeks_present FROM (
                SELECT strftime('%Y-%W', r.raid_date) as week
                FROM attendance a
                JOIN raids r ON r.id = a.raid_id
                WHERE a.player_id IN ({placeholders}) AND r.group_id = ?
                  AND r.raid_date >= date('now', ? || ' days')
                UNION
                SELECT ac.week
                FROM attendance_credit ac
                WHERE ac.player_id IN ({placeholders}) AND ac.group_id = ?
                  AND ac.created_at >= date('now', ? || ' days')
            )
            """,
            (*char_ids, group_id, -weeks * 7, *char_ids, group_id, -weeks * 7),
        )
        row = await cursor.fetchone()
        return min(row["weeks_present"], weeks) if row else 0

    async def get_all_weekly_attendance(self, group_id: int, weeks: int = 4) -> dict[int, int]:
        """Get weekly attendance counts for ALL raiders in a group.

        Merges alts into mains — if any linked character attended, the raider gets credit.
        Returns {raider_id: weeks_present}.
        """
        cursor = await self.db.execute(
            """
            SELECT raider_id, COUNT(DISTINCT week) as weeks_present FROM (
                SELECT COALESCE(p.main_id, p.id) as raider_id,
                       strftime('%Y-%W', r.raid_date) as week
                FROM attendance a
                JOIN players p ON p.id = a.player_id
                JOIN raids r ON r.id = a.raid_id
                WHERE r.group_id = ? AND r.raid_date >= date('now', ? || ' days')
                UNION
                SELECT COALESCE(p.main_id, p.id) as raider_id, ac.week
                FROM attendance_credit ac
                JOIN players p ON p.id = ac.player_id
                WHERE ac.group_id = ? AND ac.created_at >= date('now', ? || ' days')
            )
            GROUP BY raider_id
            """,
            (group_id, -weeks * 7, group_id, -weeks * 7),
        )
        return {row["raider_id"]: min(row["weeks_present"], weeks) for row in await cursor.fetchall()}

    async def get_detailed_weekly_attendance(
        self, group_id: int, weeks: int = 4,
    ) -> dict[int, dict[str, str]]:
        """Get per-week attendance detail for all raiders in a group.

        Returns {raider_id: {week_str: source_type}} where source_type is:
        - "raid" (attended the actual raid)
        - "pug" (pug credit)
        - "manual" (officer-granted credit)

        week_str is ISO format e.g. "2025-10".
        """
        # Raid attendance
        cursor = await self.db.execute(
            """
            SELECT COALESCE(p.main_id, p.id) as raider_id,
                   strftime('%Y-%W', r.raid_date) as week
            FROM attendance a
            JOIN players p ON p.id = a.player_id
            JOIN raids r ON r.id = a.raid_id
            WHERE r.group_id = ? AND r.raid_date >= date('now', ? || ' days')
            """,
            (group_id, -weeks * 7),
        )
        result: dict[int, dict[str, str]] = {}
        for row in await cursor.fetchall():
            result.setdefault(row["raider_id"], {})[row["week"]] = "raid"

        # Attendance credits (pug/manual) — only if not already marked as raid
        cursor = await self.db.execute(
            """
            SELECT COALESCE(p.main_id, p.id) as raider_id, ac.week, ac.credit_type
            FROM attendance_credit ac
            JOIN players p ON p.id = ac.player_id
            WHERE ac.group_id = ? AND ac.created_at >= date('now', ? || ' days')
            """,
            (group_id, -weeks * 7),
        )
        for row in await cursor.fetchall():
            raider_weeks = result.setdefault(row["raider_id"], {})
            if row["week"] not in raider_weeks:
                raider_weeks[row["week"]] = row["credit_type"]

        return result

    async def get_recent_weeks(self, group_id: int, weeks: int = 4) -> list[str]:
        """Get the distinct week strings for the last N weeks of raids."""
        cursor = await self.db.execute(
            """
            SELECT DISTINCT strftime('%Y-%W', r.raid_date) as week
            FROM raids r
            WHERE r.group_id = ? AND r.raid_date >= date('now', ? || ' days')
            ORDER BY week DESC
            """,
            (group_id, -weeks * 7),
        )
        weeks_list = [row["week"] for row in await cursor.fetchall()]
        # If we have fewer raid weeks than the window, pad with calendar weeks
        if len(weeks_list) < weeks:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            for i in range(weeks):
                dt = now - timedelta(weeks=i)
                wk = dt.strftime("%Y-%W")
                if wk not in weeks_list:
                    weeks_list.append(wk)
            weeks_list = sorted(set(weeks_list), reverse=True)[:weeks]
        return weeks_list

    # --- Composite score queries (group-scoped) ---

    async def get_rolling_parse_averages(self, group_id: int, weeks: int = 4) -> dict[int, float]:
        """Returns {player_id: avg_parse_pct} computed from per-boss parses.

        For each player, averages all per-boss parse_pct values across raids in
        the window. Mechanic overrides substitute the flagged boss parse with the
        player's best-of-remaining parses for that boss + bonus.

        Per-character, NOT merged across alts.
        """
        import config as cfg

        # Get all per-boss parses in the window
        cursor = await self.db.execute(
            """
            SELECT bp.player_id, bp.boss_name, bp.parse_pct, bp.raid_id
            FROM boss_performance bp
            JOIN raids r ON r.id = bp.raid_id
            WHERE r.group_id = ? AND r.raid_date >= date('now', ? || ' days')
              AND bp.parse_pct > 0
            ORDER BY bp.player_id, bp.boss_name, r.raid_date DESC
            """,
            (group_id, -weeks * 7),
        )
        rows = [dict(r) for r in await cursor.fetchall()]

        # Get mechanic overrides in the window
        cursor = await self.db.execute(
            """
            SELECT mo.player_id, mo.boss_name, mo.raid_id
            FROM mechanic_overrides mo
            JOIN raids r ON r.id = mo.raid_id
            WHERE r.group_id = ? AND r.raid_date >= date('now', ? || ' days')
            """,
            (group_id, -weeks * 7),
        )
        overrides = {(r["player_id"], r["boss_name"], r["raid_id"]) for r in await cursor.fetchall()}

        # Group parses by player
        from collections import defaultdict
        player_boss_parses: dict[int, list[tuple[str, float, int]]] = defaultdict(list)
        for row in rows:
            player_boss_parses[row["player_id"]].append(
                (row["boss_name"], row["parse_pct"], row["raid_id"])
            )

        result = {}
        for pid, entries in player_boss_parses.items():
            # Separate overridden vs normal parses
            normal = []                                      # All non-overridden parse values
            override_count = 0                               # How many overridden entries need substitution
            boss_non_overridden: dict[str, list[float]] = defaultdict(list)  # boss -> non-overridden parses

            for boss_name, parse_pct, raid_id in entries:
                if (pid, boss_name, raid_id) in overrides:
                    override_count += 1
                else:
                    normal.append(parse_pct)
                    boss_non_overridden[boss_name].append(parse_pct)

            # Compute substitution for each override
            for boss_name, parse_pct, raid_id in entries:
                if (pid, boss_name, raid_id) not in overrides:
                    continue

                same_boss = boss_non_overridden.get(boss_name, [])
                if same_boss:
                    # Has non-overridden history for this boss — use best-of-remaining
                    if len(same_boss) > 1:
                        best = sorted(same_boss, reverse=True)
                        sub_val = sum(best[:-1]) / len(best[:-1])
                    else:
                        sub_val = same_boss[0]
                else:
                    # Permanent assignment (e.g. warlock tanking Leo every week) —
                    # fall back to average across ALL other bosses
                    if normal:
                        sub_val = sum(normal) / len(normal)
                    else:
                        continue  # No data at all, skip

                normal.append(min(100, sub_val + cfg.MECHANIC_DUTY_BONUS_PCT))

            if normal:
                result[pid] = round(sum(normal) / len(normal), 1)

        # Fall back to raid_performance for players without per-boss data
        cursor = await self.db.execute(
            """
            SELECT rp.player_id, AVG(rp.parse_pct) as avg_parse
            FROM raid_performance rp
            JOIN raids r ON r.id = rp.raid_id
            WHERE r.group_id = ? AND r.raid_date >= date('now', ? || ' days')
              AND rp.parse_pct > 0
              AND rp.player_id NOT IN ({})
            GROUP BY rp.player_id
            """.format(",".join("?" * len(result)) if result else "NULL"),
            (group_id, -weeks * 7, *result.keys()),
        )
        for row in await cursor.fetchall():
            result[row["player_id"]] = round(row["avg_parse"], 1)

        return result

    async def get_rolling_utility_averages(self, group_id: int, weeks: int = 4) -> dict[int, float]:
        """Returns {player_id: avg_utility}. Per-character, NOT merged across alts."""
        cursor = await self.db.execute(
            """
            SELECT up.player_id, AVG(up.utility_total) as avg_util
            FROM utility_performance up
            JOIN raids r ON r.id = up.raid_id
            WHERE r.group_id = ? AND r.raid_date >= date('now', ? || ' days')
            GROUP BY up.player_id
            """,
            (group_id, -weeks * 7),
        )
        return {row["player_id"]: round(row["avg_util"], 1) for row in await cursor.fetchall()}

    async def get_player_weekly_boss_parses(
        self, player_id: int, group_id: int, weeks: int = 4,
    ) -> list[dict]:
        """Get per-week, per-boss parse data for a player.

        Returns [{week, boss_name, parse_pct, raid_date}] ordered by week, boss.
        """
        cursor = await self.db.execute(
            """
            SELECT strftime('%Y-%W', r.raid_date) as week, r.raid_date,
                   bp.boss_name, bp.parse_pct
            FROM boss_performance bp
            JOIN raids r ON r.id = bp.raid_id
            WHERE bp.player_id = ? AND r.group_id = ?
              AND r.raid_date >= date('now', ? || ' days')
            ORDER BY r.raid_date, bp.boss_name
            """,
            (player_id, group_id, -weeks * 7),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_player_weekly_utility(
        self, player_id: int, group_id: int, weeks: int = 4,
    ) -> list[dict]:
        """Get per-week utility breakdown for a player.

        Returns [{week, raid_date, has_flask_or_elixirs, has_food_buff, has_weapon_buff,
                  has_class_utility, interrupts, utility_total}].
        """
        cursor = await self.db.execute(
            """
            SELECT strftime('%Y-%W', r.raid_date) as week, r.raid_date,
                   up.has_flask_or_elixirs, up.has_food_buff, up.has_weapon_buff,
                   up.has_class_utility, up.interrupts, up.utility_total, up.potion_score
            FROM utility_performance up
            JOIN raids r ON r.id = up.raid_id
            WHERE up.player_id = ? AND r.group_id = ?
              AND r.raid_date >= date('now', ? || ' days')
            ORDER BY r.raid_date
            """,
            (player_id, group_id, -weeks * 7),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_player_weekly_parse_avg(
        self, player_id: int, group_id: int, weeks: int = 4,
    ) -> list[dict]:
        """Get per-week overall parse average for a player.

        Returns [{week, raid_date, parse_pct}].
        """
        cursor = await self.db.execute(
            """
            SELECT strftime('%Y-%W', r.raid_date) as week, r.raid_date,
                   rp.parse_pct
            FROM raid_performance rp
            JOIN raids r ON r.id = rp.raid_id
            WHERE rp.player_id = ? AND r.group_id = ?
              AND r.raid_date >= date('now', ? || ' days')
            ORDER BY r.raid_date
            """,
            (player_id, group_id, -weeks * 7),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_roster_with_specs(self, group_id: int) -> list[dict]:
        """Get one row per raider who has attended this group's raids.

        Uses the most recently played character's class/spec/role.
        Merges alts — raider shows up under their main's name.
        Returns {id (raider_id), char_id (actual character), name, class, spec, role}.
        Performance/utility lookups should use char_id (spec-specific data).
        Attendance/loot lookups should use id (raider identity).
        """
        cursor = await self.db.execute(
            """
            SELECT raider_id as id, char_id, main_name as name,
                   latest_class as class, latest_spec as spec, latest_role as role
            FROM (
                SELECT
                    COALESCE(p.main_id, p.id) as raider_id,
                    p.id as char_id,
                    (SELECT name FROM players WHERE id = COALESCE(p.main_id, p.id)) as main_name,
                    p.class as latest_class,
                    a.spec as latest_spec,
                    a.role as latest_role,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(p.main_id, p.id)
                        ORDER BY r.raid_date DESC
                    ) as rn
                FROM attendance a
                JOIN players p ON p.id = a.player_id
                JOIN raids r ON r.id = a.raid_id
                WHERE r.group_id = ?
            ) sub
            WHERE rn = 1
            """,
            (group_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    # --- Loot History (group-scoped) ---

    async def insert_loot_history(
        self, player_id: int, item_name: str, group_id: int,
        raid_id: int | None = None, notes: str = "",
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO loot_history (player_id, item_name, group_id, raid_id, notes) VALUES (?, ?, ?, ?, ?)",
            (player_id, item_name, group_id, raid_id, notes),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def get_loot_counts(self, group_id: int, weeks: int = 4) -> dict[int, int]:
        """Returns {player_id: count}. Per-character, not merged."""
        cursor = await self.db.execute(
            "SELECT player_id, COUNT(*) as cnt FROM loot_history WHERE group_id = ? AND awarded_at >= date('now', ? || ' days') GROUP BY player_id",
            (group_id, -weeks * 7),
        )
        return {row["player_id"]: row["cnt"] for row in await cursor.fetchall()}

    async def get_loot_award_ages(self, group_id: int) -> dict[int, list[float]]:
        """Returns {player_id: [age_days, ...]}. Per-character, not merged."""
        cursor = await self.db.execute(
            "SELECT player_id, julianday('now') - julianday(awarded_at) as age_days FROM loot_history WHERE group_id = ? ORDER BY player_id",
            (group_id,),
        )
        result: dict[int, list[float]] = {}
        for row in await cursor.fetchall():
            result.setdefault(row["player_id"], []).append(row["age_days"])
        return result

    async def get_player_loot_history(self, player_id: int, group_id: int | None = None, limit: int = 20) -> list[dict]:
        if group_id is not None:
            cursor = await self.db.execute(
                """
                SELECT lh.*, r.title as raid_title, r.raid_date
                FROM loot_history lh LEFT JOIN raids r ON r.id = lh.raid_id
                WHERE lh.player_id = ? AND lh.group_id = ?
                ORDER BY lh.awarded_at DESC LIMIT ?
                """,
                (player_id, group_id, limit),
            )
        else:
            cursor = await self.db.execute(
                """
                SELECT lh.*, r.title as raid_title, r.raid_date
                FROM loot_history lh LEFT JOIN raids r ON r.id = lh.raid_id
                WHERE lh.player_id = ?
                ORDER BY lh.awarded_at DESC LIMIT ?
                """,
                (player_id, limit),
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_received_items(self, group_id: int) -> dict[str, set[int]]:
        """Get which characters have already received which items in this group.

        Per-character — items are character-bound.
        Returns {item_name: {player_id, ...}}.
        """
        cursor = await self.db.execute(
            "SELECT item_name, player_id FROM loot_history WHERE group_id = ?",
            (group_id,),
        )
        result: dict[str, set[int]] = {}
        for row in await cursor.fetchall():
            result.setdefault(row["item_name"], set()).add(row["player_id"])
        return result

    # --- Attendance Credit (group-scoped) ---

    async def insert_attendance_credit(
        self, player_id: int, group_id: int, week: str,
        credit_type: str = "manual", source: str = "",
    ) -> bool:
        try:
            await self.db.execute(
                "INSERT INTO attendance_credit (player_id, group_id, week, credit_type, source) VALUES (?, ?, ?, ?, ?)",
                (player_id, group_id, week, credit_type, source),
            )
            await self.db.commit()
            return True
        except Exception:
            return False

    # --- Mechanic Overrides ---

    async def insert_mechanic_override(
        self, raid_id: int, player_id: int, boss_name: str, created_by: int,
    ) -> bool:
        try:
            await self.db.execute(
                "INSERT INTO mechanic_overrides (raid_id, player_id, boss_name, created_by) VALUES (?, ?, ?, ?)",
                (raid_id, player_id, boss_name, created_by),
            )
            await self.db.commit()
            return True
        except Exception:
            return False

    async def remove_mechanic_override(
        self, raid_id: int, player_id: int, boss_name: str,
    ) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM mechanic_overrides WHERE raid_id = ? AND player_id = ? AND boss_name = ?",
            (raid_id, player_id, boss_name),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def get_mechanic_overrides(self, group_id: int) -> list[dict]:
        cursor = await self.db.execute(
            """
            SELECT mo.*, p.name as player_name, r.raid_date
            FROM mechanic_overrides mo
            JOIN players p ON p.id = mo.player_id
            JOIN raids r ON r.id = mo.raid_id
            WHERE r.group_id = ?
            ORDER BY r.raid_date DESC, p.name
            """,
            (group_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_player_attendance_credits(self, player_id: int, group_id: int | None = None) -> list[dict]:
        if group_id is not None:
            cursor = await self.db.execute(
                "SELECT * FROM attendance_credit WHERE player_id = ? AND group_id = ? ORDER BY week DESC",
                (player_id, group_id),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM attendance_credit WHERE player_id = ? ORDER BY week DESC",
                (player_id,),
            )
        return [dict(r) for r in await cursor.fetchall()]
