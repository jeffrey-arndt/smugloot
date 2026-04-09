"""Discord commands for loot council workflow.

/scores   — current player standings
/assign   — who's in line for an item
/award    — record a loot drop
/loot-history — what has a player received
/pug-credit — self-report a pug log for attendance credit
/attendance-credit — officer grants manual attendance credit
"""

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
from wcl_client import extract_report_code
from assignment import (
    RosterPlayer, compute_composite, compute_loot_penalty,
    priority_score_for_rank, generate_assignments,
    format_player_scores, format_assignments,
)
from loot_priority import SPEC_ALIAS_MAP


async def _build_roster(db, group_id):
    """Build full roster with composite scores from DB data."""
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
            weekly_attendance=detail_att.get(rid, {}),  # per-week detail
        )
        p.composite_score = compute_composite(p)
        roster.append(p)

    return roster


async def raid_group_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Shared autocomplete for the raid_group parameter."""
    groups = await interaction.client.db.get_raid_groups()
    choices = [
        app_commands.Choice(name=g["name"], value=g["name"])
        for g in groups
        if current.lower() in g["name"].lower()
    ]
    return choices[:25]


class LootCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── /scores ─────────────────────────────────────────────────────────

    @app_commands.command(name="scores", description="Show current loot priority scores")
    @app_commands.describe(raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)")
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def scores(self, interaction: discord.Interaction, raid_group: str):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        roster = await _build_roster(self.bot.db, group["id"])
        if not roster:
            await interaction.followup.send("No player data yet. Import a raid log first.", ephemeral=True)
            return

        text = format_player_scores(roster)
        raid_count = await self.bot.db.get_raid_count(group["id"])

        embed = discord.Embed(
            title=f"Loot Priority Scores — {raid_group} ({raid_count} raid(s))",
            description=f"```\n{text}\n```",
            color=discord.Color.purple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /assign ─────────────────────────────────────────────────────────

    @app_commands.command(name="assign", description="Show who's in line for an item")
    @app_commands.describe(
        raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)",
        item_name="Item name (e.g. Dragonspine Trophy)",
    )
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def assign(self, interaction: discord.Interaction, raid_group: str, item_name: str):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        # Look up item in DB
        item = await self.bot.db.get_item_priority(item_name)
        if not item:
            await interaction.followup.send(
                f"Item not found: **{item_name}**\n"
                f"Make sure the name matches the priority sheet exactly.",
                ephemeral=True,
            )
            return

        roster = await _build_roster(self.bot.db, group["id"])
        if not roster:
            await interaction.followup.send("No player data yet.", ephemeral=True)
            return

        received_items = await self.bot.db.get_received_items(group["id"])
        assignments = generate_assignments([item], roster, received_items)
        if not assignments or not assignments[0].assigned:
            await interaction.followup.send(
                f"No eligible players for **{item_name}**.", ephemeral=True,
            )
            return

        a = assignments[0]
        week_keys = await self.bot.db.get_recent_weeks(group["id"], weeks=config.ATTENDANCE_WEEKS)
        week_keys = sorted(week_keys)  # oldest first
        text = format_assignments(assignments, week_keys=week_keys, max_players=5)
        raid_count = await self.bot.db.get_raid_count(group["id"])

        embed = discord.Embed(
            title=f"{a.item_name} — Assignment ({raid_group})",
            description=f"```\n{text}\n```",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Based on {raid_count} raid(s) | Priority: {a.chain_display}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @assign.autocomplete("item_name")
    async def assign_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete item names from priority sheets."""
        if len(current) < 2:
            return []
        cursor = await self.bot.db.db.execute(
            """
            SELECT DISTINCT item_name FROM item_priorities
            WHERE item_name LIKE ? AND priority_type = 'chain'
            ORDER BY item_name LIMIT 25
            """,
            (f"%{current}%",),
        )
        rows = await cursor.fetchall()
        return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]

    # ── /award ──────────────────────────────────────────────────────────

    @app_commands.command(name="award", description="Record a loot council item award")
    @app_commands.describe(
        raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)",
        player_name="Player who received the item",
        item_name="Item name (e.g. Dragonspine Trophy)",
        raid_date="Which raid this dropped in (defaults to most recent)",
    )
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def award(
        self, interaction: discord.Interaction,
        raid_group: str, player_name: str, item_name: str,
        raid_date: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        # Find player
        player = await self.bot.db.get_player_by_name(player_name)
        if not player:
            await interaction.followup.send(
                f"Player not found: **{player_name}**", ephemeral=True,
            )
            return

        # Find the raid
        if raid_date:
            cursor = await self.bot.db.db.execute(
                "SELECT * FROM raids WHERE group_id = ? AND raid_date = ?",
                (group["id"], raid_date),
            )
            raid_row = await cursor.fetchone()
            if not raid_row:
                await interaction.followup.send(
                    f"No raid found for **{raid_group}** on **{raid_date}**.",
                    ephemeral=True,
                )
                return
            raid = dict(raid_row)
        else:
            recent_raids = await self.bot.db.get_recent_raids(limit=1, group_id=group["id"])
            if not recent_raids:
                await interaction.followup.send("No raids found for this group.", ephemeral=True)
                return
            raid = recent_raids[0]

        await self.bot.db.insert_loot_history(
            player_id=player["id"],
            item_name=item_name,
            group_id=group["id"],
            raid_id=raid["id"],
            notes=f"Awarded by {interaction.user.display_name}",
            awarded_at=raid["raid_date"],
        )

        # Show updated position
        roster = await _build_roster(self.bot.db, group["id"])
        target = next((p for p in roster if p.player_id == player["id"]), None)
        penalty_str = f" (loot penalty: -{target.loot_penalty:.1f})" if target and target.loot_penalty > 0 else ""

        await interaction.followup.send(
            f"Recorded: **{player['name']}** received **{item_name}**{penalty_str} "
            f"({raid_group}, raid {raid['raid_date']})",
            ephemeral=True,
        )

        # Post to public channel
        channel = self.bot.get_channel(config.PUBLISH_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="Loot Awarded",
                description=f"**{player['name']}** received **{item_name}**",
                color=config.CLASS_COLORS.get(player["class"], 0x808080),
            )
            embed.set_footer(text=f"Awarded by {interaction.user.display_name} | {raid_group} | {raid['raid_date']}")
            await channel.send(embed=embed)

    @award.autocomplete("player_name")
    async def award_player_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cursor = await self.bot.db.db.execute(
            "SELECT name FROM players WHERE name LIKE ? COLLATE NOCASE ORDER BY name LIMIT 25",
            (f"%{current}%",),
        )
        rows = await cursor.fetchall()
        return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]

    @award.autocomplete("item_name")
    async def award_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if len(current) < 2:
            return []
        cursor = await self.bot.db.db.execute(
            """
            SELECT DISTINCT item_name FROM item_priorities
            WHERE item_name LIKE ? AND priority_type = 'chain'
            ORDER BY item_name LIMIT 25
            """,
            (f"%{current}%",),
        )
        rows = await cursor.fetchall()
        return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]

    @award.autocomplete("raid_date")
    async def award_raid_date_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cursor = await self.bot.db.db.execute(
            """
            SELECT raid_date, title FROM raids
            WHERE raid_date LIKE ? COLLATE NOCASE
            ORDER BY raid_date DESC LIMIT 10
            """,
            (f"%{current}%",),
        )
        rows = await cursor.fetchall()
        return [
            app_commands.Choice(name=f"{row[0]} — {row[1]}", value=row[0])
            for row in rows
        ]

    # ── /loot-history ───────────────────────────────────────────────────

    @app_commands.command(name="loot-history", description="Show a player's loot history")
    @app_commands.describe(
        raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)",
        player_name="Player name",
    )
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def loot_history(
        self, interaction: discord.Interaction, raid_group: str, player_name: str,
    ):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        player = await self.bot.db.get_player_by_name(player_name)
        if not player:
            await interaction.followup.send(
                f"Player not found: **{player_name}**", ephemeral=True,
            )
            return

        history = await self.bot.db.get_player_loot_history(player["id"], group["id"])
        if not history:
            await interaction.followup.send(
                f"**{player['name']}** has no loot history for {raid_group}.", ephemeral=True,
            )
            return

        # Compute current penalty
        age_map = await self.bot.db.get_loot_award_ages(group["id"])
        ages = age_map.get(player["id"], [])
        total_penalty = compute_loot_penalty(ages)
        decay_days = config.LOOT_PENALTY_DECAY_WEEKS * 7

        lines = []
        for h in history:
            date_str = h.get("raid_date") or h["awarded_at"][:10]
            age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(
                h["awarded_at"].replace("Z", "+00:00") if "Z" in str(h["awarded_at"])
                else h["awarded_at"]
            ).replace(tzinfo=timezone.utc)).days
            if age_days < decay_days:
                remaining = config.LOOT_PENALTY_PER_ITEM * max(0, 1 - age_days / decay_days)
                decay_str = f" (-{remaining:.1f} penalty, {decay_days - age_days}d until clear)"
            else:
                decay_str = " (decayed)"
            lines.append(f"• **{h['item_name']}** — {date_str}{decay_str}")

        embed = discord.Embed(
            title=f"Loot History — {player['name']} ({raid_group})",
            description="\n".join(lines),
            color=config.CLASS_COLORS.get(player["class"], 0x808080),
        )
        if total_penalty > 0:
            embed.set_footer(text=f"Active loot penalty: -{total_penalty:.1f}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @loot_history.autocomplete("player_name")
    async def history_player_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cursor = await self.bot.db.db.execute(
            "SELECT name FROM players WHERE name LIKE ? COLLATE NOCASE ORDER BY name LIMIT 25",
            (f"%{current}%",),
        )
        rows = await cursor.fetchall()
        return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]


    # ── /pug-credit ──────────────────────────────────────────────────────

    @app_commands.command(
        name="pug-credit",
        description="Submit a pug WCL log to get attendance credit for the week",
    )
    @app_commands.describe(
        raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)",
        wcl_url="The WarcraftLogs report URL from your pug",
    )
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def pug_credit(self, interaction: discord.Interaction, raid_group: str, wcl_url: str):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        report_code = extract_report_code(wcl_url)
        if not report_code:
            await interaction.followup.send(
                "Invalid WarcraftLogs URL.", ephemeral=True,
            )
            return

        # Lightweight fetch — just roster and date
        try:
            pug_names, start_time = await self.bot.wcl.fetch_report_roster(report_code)
        except Exception as e:
            await interaction.followup.send(
                f"Error fetching report: {e}", ephemeral=True,
            )
            return

        # Determine the week from the raid date
        raid_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
        week = raid_dt.strftime("%Y-%W")

        # Match pug roster against known guild players
        credited = []
        already_had = []
        pug_names_lower = {n.lower() for n in pug_names}

        # Get all known players
        cursor = await self.bot.db.db.execute("SELECT id, name FROM players")
        all_players = await cursor.fetchall()

        for player in all_players:
            if player[1].lower() in pug_names_lower:
                inserted = await self.bot.db.insert_attendance_credit(
                    player_id=player[0],
                    group_id=group["id"],
                    week=week,
                    credit_type="pug",
                    source=report_code,
                )
                if inserted:
                    credited.append(player[1])
                else:
                    already_had.append(player[1])

        if not credited and not already_had:
            await interaction.followup.send(
                f"No known guild members found in that log.\n"
                f"Players need to be imported via `/parse-log` first.",
                ephemeral=True,
            )
            return

        lines = []
        if credited:
            lines.append(f"**Attendance credit granted** (week of {raid_dt.strftime('%b %d')}, {raid_group}):")
            lines.append(", ".join(f"**{n}**" for n in sorted(credited)))
        if already_had:
            lines.append(f"\nAlready had credit: {', '.join(sorted(already_had))}")

        embed = discord.Embed(
            title=f"Pug Credit — {raid_group}",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Report: {report_code}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /attendance-credit ──────────────────────────────────────────────

    @app_commands.command(
        name="attendance-credit",
        description="Grant a player attendance credit (defaults to most recent raid week)",
    )
    @app_commands.describe(
        raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)",
        player_name="Player to credit",
        raid_date="Credit for this raid's week (defaults to most recent raid)",
    )
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def attendance_credit(
        self, interaction: discord.Interaction, raid_group: str, player_name: str,
        raid_date: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        player = await self.bot.db.get_player_by_name(player_name)
        if not player:
            await interaction.followup.send(
                f"Player not found: **{player_name}**", ephemeral=True,
            )
            return

        if raid_date:
            # Derive the week from the provided date
            try:
                dt = datetime.strptime(raid_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                await interaction.followup.send(
                    f"Invalid date format: **{raid_date}**. Use YYYY-MM-DD.", ephemeral=True,
                )
                return
            week = dt.strftime("%Y-%W")
            week_display = dt.strftime("%b %d, %Y")
        else:
            # Default to most recent raid's week
            recent_raids = await self.bot.db.get_recent_raids(limit=1, group_id=group["id"])
            if recent_raids:
                dt = datetime.strptime(recent_raids[0]["raid_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                week = dt.strftime("%Y-%W")
                week_display = dt.strftime("%b %d, %Y")
            else:
                week = datetime.now(timezone.utc).strftime("%Y-%W")
                week_display = "this week"

        inserted = await self.bot.db.insert_attendance_credit(
            player_id=player["id"],
            group_id=group["id"],
            week=week,
            credit_type="manual",
            source=f"Granted by {interaction.user.display_name}",
        )

        if inserted:
            await interaction.followup.send(
                f"**{player['name']}** granted attendance credit for week of {week_display} ({raid_group}).",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"**{player['name']}** already has attendance credit for week of {week_display} ({raid_group}).",
                ephemeral=True,
            )

    @attendance_credit.autocomplete("player_name")
    async def att_credit_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cursor = await self.bot.db.db.execute(
            "SELECT name FROM players WHERE name LIKE ? COLLATE NOCASE ORDER BY name LIMIT 25",
            (f"%{current}%",),
        )
        rows = await cursor.fetchall()
        return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]

    @attendance_credit.autocomplete("raid_date")
    async def att_credit_date_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cursor = await self.bot.db.db.execute(
            """
            SELECT raid_date, title FROM raids
            WHERE raid_date LIKE ? COLLATE NOCASE
            ORDER BY raid_date DESC LIMIT 10
            """,
            (f"%{current}%",),
        )
        rows = await cursor.fetchall()
        return [
            app_commands.Choice(name=f"{row[0]} — {row[1]}", value=row[0])
            for row in rows
        ]


    # ── /mechanic-override ─────────────────────────────────────────────

    @app_commands.command(
        name="mechanic-override",
        description="Flag players' parses on a boss as mechanic duty (comma-separated names)",
    )
    @app_commands.describe(
        raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)",
        players="Player names, comma-separated (e.g. Player1,Player2,Player3)",
        boss_name="Boss name (e.g. Magtheridon, High King Maulgar)",
        raid_date="Raid date YYYY-MM-DD (defaults to most recent raid)",
    )
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def mechanic_override(
        self, interaction: discord.Interaction,
        raid_group: str, players: str, boss_name: str,
        raid_date: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        # Find the target raid
        if raid_date:
            cursor = await self.bot.db.db.execute(
                "SELECT * FROM raids WHERE group_id = ? AND raid_date = ?",
                (group["id"], raid_date),
            )
            raid_row = await cursor.fetchone()
            if not raid_row:
                await interaction.followup.send(
                    f"No raid found for **{raid_group}** on **{raid_date}**.",
                    ephemeral=True,
                )
                return
            raid = dict(raid_row)
        else:
            recent_raids = await self.bot.db.get_recent_raids(limit=1, group_id=group["id"])
            if not recent_raids:
                await interaction.followup.send("No raids found for this group.", ephemeral=True)
                return
            raid = recent_raids[0]

        # Resolve the exact boss name from DB
        cursor = await self.bot.db.db.execute(
            "SELECT DISTINCT boss_name FROM boss_performance WHERE raid_id = ? AND boss_name = ? COLLATE NOCASE",
            (raid["id"], boss_name),
        )
        boss_row = await cursor.fetchone()
        if not boss_row:
            cursor = await self.bot.db.db.execute(
                "SELECT DISTINCT boss_name FROM boss_performance WHERE raid_id = ?",
                (raid["id"],),
            )
            available = [r[0] for r in await cursor.fetchall()]
            await interaction.followup.send(
                f"Boss **{boss_name}** not found in raid {raid['raid_date']}.\n"
                f"Available bosses: {', '.join(available) or 'none'}",
                ephemeral=True,
            )
            return
        exact_boss = boss_row[0]

        # Process each player
        player_names = [n.strip() for n in players.split(",") if n.strip()]
        results = []
        for pname in player_names:
            player = await self.bot.db.get_player_by_name(pname)
            if not player:
                results.append(f"**{pname}** — not found")
                continue

            # Check they have data for this boss
            cursor = await self.bot.db.db.execute(
                "SELECT parse_pct FROM boss_performance WHERE raid_id = ? AND player_id = ? AND boss_name = ?",
                (raid["id"], player["id"], exact_boss),
            )
            bp_row = await cursor.fetchone()
            if not bp_row:
                results.append(f"**{player['name']}** — no data for {exact_boss}")
                continue

            inserted = await self.bot.db.insert_mechanic_override(
                raid_id=raid["id"],
                player_id=player["id"],
                boss_name=exact_boss,
                created_by=interaction.user.id,
            )
            if inserted:
                results.append(f"**{player['name']}** — override set (was {bp_row[0]:.0f}%)")
            else:
                results.append(f"**{player['name']}** — already overridden")

        await interaction.followup.send(
            f"Mechanic override — **{exact_boss}** ({raid['raid_date']}, {raid_group})\n"
            + "\n".join(results)
            + f"\n\nOverridden parses will be replaced with best-of-remaining avg "
            + f"+ {config.MECHANIC_DUTY_BONUS_PCT}% bonus.",
            ephemeral=True,
        )

    @mechanic_override.autocomplete("boss_name")
    async def mechanic_boss_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cursor = await self.bot.db.db.execute(
            """
            SELECT DISTINCT bp.boss_name
            FROM boss_performance bp
            JOIN raids r ON r.id = bp.raid_id
            WHERE bp.boss_name LIKE ? COLLATE NOCASE
            ORDER BY r.raid_date DESC
            LIMIT 25
            """,
            (f"%{current}%",),
        )
        return [app_commands.Choice(name=row[0], value=row[0]) for row in await cursor.fetchall()]

    @mechanic_override.autocomplete("raid_date")
    async def mechanic_raid_date_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cursor = await self.bot.db.db.execute(
            """
            SELECT raid_date, title FROM raids
            WHERE raid_date LIKE ? COLLATE NOCASE
            ORDER BY raid_date DESC LIMIT 10
            """,
            (f"%{current}%",),
        )
        rows = await cursor.fetchall()
        return [
            app_commands.Choice(name=f"{row[0]} — {row[1]}", value=row[0])
            for row in rows
        ]

    # ── /attendance ──────────────────────────────────────────────────────

    @app_commands.command(name="attendance", description="Show raid attendance for all raiders over the last 4 weeks")
    @app_commands.describe(raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)")
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def attendance(self, interaction: discord.Interaction, raid_group: str):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        detail_att = await self.bot.db.get_detailed_weekly_attendance(
            group["id"], weeks=config.ATTENDANCE_WEEKS
        )
        week_keys = await self.bot.db.get_recent_weeks(
            group["id"], weeks=config.ATTENDANCE_WEEKS
        )
        week_keys = sorted(week_keys)  # oldest first

        if not detail_att:
            await interaction.followup.send("No attendance data yet.", ephemeral=True)
            return

        # Resolve raider names
        raider_names = {}
        for rid in detail_att:
            cursor = await self.bot.db.db.execute(
                "SELECT name FROM players WHERE id = ?", (rid,)
            )
            row = await cursor.fetchone()
            if row:
                raider_names[rid] = row[0]

        # Build week column headers (short: last 2 digits of week num)
        week_headers = []
        for w in week_keys:
            parts = w.split("-")
            week_headers.append(f"W{parts[1]}" if len(parts) == 2 else w[-2:])

        symbols = {"raid": "✓", "pug": "P", "manual": "M"}

        lines = []
        col_w = "  ".join(f"{h:>3}" for h in week_headers)
        header = f"{'Name':<16} {col_w}  {'Total':>5}"
        lines.append(header)
        lines.append("-" * len(header))

        # Sort by total attendance desc, then name
        sorted_raiders = sorted(
            detail_att.items(),
            key=lambda x: (-len(x[1]), raider_names.get(x[0], "")),
        )

        for rid, weeks_data in sorted_raiders:
            name = raider_names.get(rid, f"#{rid}")
            cells = []
            total = 0
            for w in week_keys:
                src = weeks_data.get(w, "")
                cells.append(f"{symbols.get(src, '-'):>3}")
                if src:
                    total += 1
            row_str = "  ".join(cells)
            lines.append(f"{name:<16} {row_str}  {total:>3}/{len(week_keys)}")

        text = "\n".join(lines)
        embed = discord.Embed(
            title=f"Raid Attendance — {raid_group} (last {config.ATTENDANCE_WEEKS} weeks)",
            description=f"```\n{text}\n```",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="✓ = attended  P = pug credit  M = manual credit  - = missed")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /compare ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="compare",
        description="Week-over-week breakdown for one or more raiders (comma-separated)",
    )
    @app_commands.describe(
        raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)",
        players="Player names, comma-separated (e.g. Player1,Player2)",
        item_name="Optional: show assignment score breakdown for this item",
    )
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def compare(
        self, interaction: discord.Interaction,
        raid_group: str, players: str, item_name: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        player_names = [n.strip() for n in players.split(",") if n.strip()]
        if not player_names:
            await interaction.followup.send("No player names provided.", ephemeral=True)
            return

        week_keys = sorted(await self.bot.db.get_recent_weeks(
            group["id"], weeks=config.ATTENDANCE_WEEKS
        ))
        week_headers = []
        for w in week_keys:
            parts = w.split("-")
            week_headers.append(f"W{parts[1]}" if len(parts) == 2 else w[-2:])

        # Build roster for assignment score lookup
        roster = await _build_roster(self.bot.db, group["id"])
        roster_map = {p.name.lower(): p for p in roster}

        # Get detailed attendance
        detail_att = await self.bot.db.get_detailed_weekly_attendance(
            group["id"], weeks=config.ATTENDANCE_WEEKS
        )

        att_symbols = {"raid": "✓", "pug": "P", "manual": "M"}

        output_sections = []

        for pname in player_names:
            player = await self.bot.db.get_player_by_name(pname)
            if not player:
                output_sections.append(f"{pname} — not found\n")
                continue

            # Resolve to char_id used in roster
            roster_player = roster_map.get(player["name"].lower())
            if not roster_player:
                output_sections.append(f"{player['name']} — no raid data\n")
                continue

            pid = roster_player.player_id
            raider_id = player.get("main_id") or player["id"]

            # Per-week boss parses
            boss_parses = await self.bot.db.get_player_weekly_boss_parses(
                pid, group["id"], weeks=config.ROLLING_AVG_WEEKS
            )
            # Per-week utility
            weekly_util = await self.bot.db.get_player_weekly_utility(
                pid, group["id"], weeks=config.ROLLING_AVG_WEEKS
            )
            # Per-week overall parse
            weekly_parse = await self.bot.db.get_player_weekly_parse_avg(
                pid, group["id"], weeks=config.ROLLING_AVG_WEEKS
            )

            # Organize by week
            parse_by_week: dict[str, float] = {}
            for row in weekly_parse:
                parse_by_week[row["week"]] = row["parse_pct"]

            boss_by_week: dict[str, dict[str, float]] = {}
            all_bosses: list[str] = []
            for row in boss_parses:
                wk = row["week"]
                boss_by_week.setdefault(wk, {})[row["boss_name"]] = row["parse_pct"]
                if row["boss_name"] not in all_bosses:
                    all_bosses.append(row["boss_name"])

            util_by_week: dict[str, dict] = {}
            for row in weekly_util:
                util_by_week[row["week"]] = row

            # Attendance for this raider
            raider_att = detail_att.get(raider_id, {})

            # Build the table
            lines = []
            col_width = 6
            week_cols = "".join(f"{h:>{col_width}}" for h in week_headers)
            lines.append(
                f"{roster_player.name} ({roster_player.spec} {roster_player.player_class}) "
                f"— {roster_player.attendance_weeks}/{config.ATTENDANCE_MAX} attendance"
            )
            lines.append("─" * (14 + len(week_headers) * col_width + 14))

            header = f"{'':>14}{week_cols}{'Avg':>{col_width}}{'Trend':>{col_width+2}}"
            lines.append(header)

            # Parse row (overall)
            parse_vals = []
            parse_cells = []
            for w in week_keys:
                val = parse_by_week.get(w)
                if val is not None and val > 0:
                    parse_cells.append(f"{val:>{col_width}.0f}")
                    parse_vals.append(val)
                else:
                    parse_cells.append(f"{'-':>{col_width}}")

            if roster_player.role == "tank":
                avg_str = f"{'exempt':>{col_width}}"
                trend_str = f"{'':>{col_width+2}}"
            else:
                avg_str = f"{roster_player.avg_parse_pct:>{col_width}.0f}" if parse_vals else f"{'-':>{col_width}}"
                if len(parse_vals) >= 2:
                    trend_val = parse_vals[-1] - parse_vals[0]
                    arrow = "▲" if trend_val > 0 else "▼" if trend_val < 0 else "◆"
                    trend_str = f"  {arrow}{trend_val:+.0f}"
                else:
                    trend_str = f"{'':>{col_width+2}}"

            lines.append(f"{'Parse':>14}{''.join(parse_cells)}{avg_str}{trend_str}")

            # Per-boss parse rows (indented)
            from embeds import _short_boss_name
            for boss in all_bosses:
                boss_cells = []
                for w in week_keys:
                    bval = boss_by_week.get(w, {}).get(boss)
                    if bval is not None and bval > 0:
                        boss_cells.append(f"{bval:>{col_width}.0f}")
                    else:
                        boss_cells.append(f"{'-':>{col_width}}")
                short = _short_boss_name(boss)
                lines.append(f"{'  ' + short:>14}{''.join(boss_cells)}")

            # Consumables row
            cons_cells = []
            for w in week_keys:
                u = util_by_week.get(w)
                if u:
                    has_all = u["has_flask_or_elixirs"] and u["has_food_buff"] and u["has_weapon_buff"]
                    cons_cells.append(f"{'✓':>{col_width}}" if has_all else f"{'✗':>{col_width}}")
                else:
                    cons_cells.append(f"{'-':>{col_width}}")
            cons_total = sum(1 for w in week_keys if util_by_week.get(w) and
                           util_by_week[w]["has_flask_or_elixirs"] and
                           util_by_week[w]["has_food_buff"] and
                           util_by_week[w]["has_weapon_buff"])
            cons_weeks = sum(1 for w in week_keys if w in util_by_week)
            cons_avg = f"{cons_total}/{cons_weeks}" if cons_weeks else "-"
            lines.append(f"{'Cons':>14}{''.join(cons_cells)}{cons_avg:>{col_width}}")

            # Class utility row
            util_cells = []
            for w in week_keys:
                u = util_by_week.get(w)
                if u:
                    util_cells.append(f"{'✓':>{col_width}}" if u["has_class_utility"] else f"{'✗':>{col_width}}")
                else:
                    util_cells.append(f"{'-':>{col_width}}")
            lines.append(f"{'Utility':>14}{''.join(util_cells)}")

            # Interrupts row
            int_cells = []
            for w in week_keys:
                u = util_by_week.get(w)
                if u:
                    int_cells.append(f"{u['interrupts']:>{col_width}}")
                else:
                    int_cells.append(f"{'-':>{col_width}}")
            lines.append(f"{'Ints':>14}{''.join(int_cells)}")

            # Potions row
            pot_cells = []
            for w in week_keys:
                u = util_by_week.get(w)
                if u:
                    pot_cells.append(f"{'✓':>{col_width}}" if u["potion_score"] else f"{'✗':>{col_width}}")
                else:
                    pot_cells.append(f"{'-':>{col_width}}")
            lines.append(f"{'Pots':>14}{''.join(pot_cells)}")

            # Attendance row
            att_cells = []
            for w in week_keys:
                src = raider_att.get(w, "")
                sym = att_symbols.get(src, "-")
                att_cells.append(f"{sym:>{col_width}}")
            lines.append(f"{'Attendance':>14}{''.join(att_cells)}")

            # Loot history
            loot = await self.bot.db.get_player_loot_history(pid, group["id"])
            if loot:
                loot_items = ", ".join(f"{h['item_name']} ({(h.get('raid_date') or h['awarded_at'][:10])})" for h in loot)
                lines.append(f"Loot: {loot_items}")
            else:
                lines.append("Loot: none")

            output_sections.append("\n".join(lines))

        # If item_name provided, append assignment comparison
        if item_name and roster:
            item = await self.bot.db.get_item_priority(item_name)
            if item:
                received_items = await self.bot.db.get_received_items(group["id"])
                assignments = generate_assignments([item], roster, received_items)
                if assignments and assignments[0].assigned:
                    a = assignments[0]
                    compare_lines = [f"\n{item_name} — Score Comparison:"]
                    # Filter to just the requested players
                    requested = {n.strip().lower() for n in player_names}
                    for ap in a.assigned:
                        if ap.player.name.lower() in requested:
                            rank_tag = f"R{ap.priority_rank}" if ap.priority_rank and ap.priority_rank < 99 else "--"
                            parse_str = f"Parse {ap.player.avg_parse_pct:.0f}%" if ap.player.role != "tank" else "Parse exempt"
                            loot_str = f", -{ap.player.loot_penalty:.1f}L" if ap.player.loot_penalty > 0 else ""
                            compare_lines.append(
                                f"  {ap.player.name:<16} {ap.adjusted_score:>4.1f} "
                                f"(Att {ap.player.attendance_weeks}/{config.ATTENDANCE_MAX}, "
                                f"{parse_str}, "
                                f"Util {ap.player.avg_utility:.0f}/{config.UTILITY_MAX}, "
                                f"{rank_tag}{loot_str})"
                            )
                    output_sections.append("\n".join(compare_lines))

        full_text = "\n\n".join(output_sections)

        embed = discord.Embed(
            title=f"Player Comparison — {raid_group}",
            description=f"```\n{full_text}\n```",
            color=discord.Color.teal(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @compare.autocomplete("item_name")
    async def compare_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if len(current) < 2:
            return []
        cursor = await self.bot.db.db.execute(
            """
            SELECT DISTINCT item_name FROM item_priorities
            WHERE item_name LIKE ? AND priority_type = 'chain'
            ORDER BY item_name LIMIT 25
            """,
            (f"%{current}%",),
        )
        rows = await cursor.fetchall()
        return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]

    # ── /link ────────────────────────────────────────────────────────────

    @app_commands.command(name="link", description="Link an alt character to a main")
    @app_commands.describe(
        main_name="Main character name",
        alt_name="Alt character name to link",
    )
    async def link(self, interaction: discord.Interaction, main_name: str, alt_name: str):
        await interaction.response.defer(ephemeral=True)

        main = await self.bot.db.get_player_by_name(main_name)
        if not main:
            await interaction.followup.send(f"Main not found: **{main_name}**", ephemeral=True)
            return

        alt = await self.bot.db.get_player_by_name(alt_name)
        if not alt:
            await interaction.followup.send(f"Alt not found: **{alt_name}**", ephemeral=True)
            return

        if main["id"] == alt["id"]:
            await interaction.followup.send("Can't link a character to itself.", ephemeral=True)
            return

        # Don't allow linking a main that is itself an alt
        if main.get("main_id"):
            await interaction.followup.send(
                f"**{main['name']}** is already linked as an alt of another character. "
                f"Unlink it first or link to the actual main.",
                ephemeral=True,
            )
            return

        await self.bot.db.link_characters(main["id"], alt["id"])

        # Show all linked characters
        alts = await self.bot.db.get_linked_characters(main["id"])
        alt_names = ", ".join(f"**{a['name']}** ({a['class']})" for a in alts)

        await interaction.followup.send(
            f"Linked **{alt['name']}** as alt of **{main['name']}**\n"
            f"All alts: {alt_names}",
            ephemeral=True,
        )

    @link.autocomplete("main_name")
    async def link_main_autocomplete(self, interaction: discord.Interaction, current: str):
        cursor = await self.bot.db.db.execute(
            "SELECT name FROM players WHERE name LIKE ? COLLATE NOCASE AND main_id IS NULL ORDER BY name LIMIT 25",
            (f"%{current}%",),
        )
        return [app_commands.Choice(name=row[0], value=row[0]) for row in await cursor.fetchall()]

    @link.autocomplete("alt_name")
    async def link_alt_autocomplete(self, interaction: discord.Interaction, current: str):
        cursor = await self.bot.db.db.execute(
            "SELECT name FROM players WHERE name LIKE ? COLLATE NOCASE ORDER BY name LIMIT 25",
            (f"%{current}%",),
        )
        return [app_commands.Choice(name=row[0], value=row[0]) for row in await cursor.fetchall()]

    # ── /unlink ─────────────────────────────────────────────────────────

    @app_commands.command(name="unlink", description="Unlink an alt character from its main")
    @app_commands.describe(character_name="Character to unlink")
    async def unlink(self, interaction: discord.Interaction, character_name: str):
        await interaction.response.defer(ephemeral=True)

        player = await self.bot.db.get_player_by_name(character_name)
        if not player:
            await interaction.followup.send(f"Character not found: **{character_name}**", ephemeral=True)
            return

        if not player.get("main_id"):
            await interaction.followup.send(f"**{player['name']}** is not linked to any main.", ephemeral=True)
            return

        main = await self.bot.db.db.execute("SELECT name FROM players WHERE id = ?", (player["main_id"],))
        main_row = await main.fetchone()
        main_name = main_row[0] if main_row else "Unknown"

        await self.bot.db.unlink_character(player["id"])
        await interaction.followup.send(
            f"Unlinked **{player['name']}** from **{main_name}**.",
            ephemeral=True,
        )

    @unlink.autocomplete("character_name")
    async def unlink_autocomplete(self, interaction: discord.Interaction, current: str):
        cursor = await self.bot.db.db.execute(
            "SELECT name FROM players WHERE name LIKE ? COLLATE NOCASE AND main_id IS NOT NULL ORDER BY name LIMIT 25",
            (f"%{current}%",),
        )
        return [app_commands.Choice(name=row[0], value=row[0]) for row in await cursor.fetchall()]

    # ── /characters ─────────────────────────────────────────────────────

    @app_commands.command(name="characters", description="Show a player's linked characters")
    @app_commands.describe(player_name="Player name")
    async def characters(self, interaction: discord.Interaction, player_name: str):
        await interaction.response.defer(ephemeral=True)

        player = await self.bot.db.get_player_by_name(player_name)
        if not player:
            await interaction.followup.send(f"Player not found: **{player_name}**", ephemeral=True)
            return

        # Resolve to main
        main_id = player.get("main_id") or player["id"]
        main_cursor = await self.bot.db.db.execute("SELECT * FROM players WHERE id = ?", (main_id,))
        main_row = await main_cursor.fetchone()
        main = dict(main_row) if main_row else player

        alts = await self.bot.db.get_linked_characters(main_id)

        if not alts:
            await interaction.followup.send(
                f"**{main['name']}** ({main['class']}) — no linked alts.",
                ephemeral=True,
            )
            return

        alt_lines = "\n".join(f"  • **{a['name']}** ({a['class']})" for a in alts)
        await interaction.followup.send(
            f"**{main['name']}** ({main['class']}) — Main\n{alt_lines}",
            ephemeral=True,
        )

    @characters.autocomplete("player_name")
    async def chars_autocomplete(self, interaction: discord.Interaction, current: str):
        cursor = await self.bot.db.db.execute(
            "SELECT name FROM players WHERE name LIKE ? COLLATE NOCASE ORDER BY name LIMIT 25",
            (f"%{current}%",),
        )
        return [app_commands.Choice(name=row[0], value=row[0]) for row in await cursor.fetchall()]


async def setup(bot):
    await bot.add_cog(LootCog(bot))
