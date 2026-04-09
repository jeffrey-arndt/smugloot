from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
from scoring import (
    check_consumables, check_class_utility,
    check_interrupts, check_potions,
)
from wcl_client import RaidData, extract_report_code


class ApprovePublishView(discord.ui.View):
    """View with Publish/Cancel buttons for officer review."""

    def __init__(self, raid_data: RaidData, embeds: list[discord.Embed], bot, group_id: int):
        super().__init__(timeout=300)
        self.raid_data = raid_data
        self.bot = bot
        self.group_id = group_id

    @discord.ui.button(label="Import", style=discord.ButtonStyle.green)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        # Save to database
        try:
            await self._save_to_db(interaction.user.id)
        except Exception as e:
            await interaction.followup.send(
                f"Error saving to database: {e}", ephemeral=True
            )
            return

        kills = [b for b in self.raid_data.bosses if b.kill]
        kill_names = ", ".join(b.name for b in kills)

        # Disable buttons and confirm
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(
            content=(
                f"Log imported successfully! "
                f"**{len(kills)}** boss kill(s) ({kill_names}), "
                f"**{len(self.raid_data.players)}** raiders saved."
            ),
            embeds=[],
            view=self,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Cancelled — nothing was published.", embeds=[], view=self
        )

    async def _save_to_db(self, imported_by: int):
        """Persist raid data to the database."""
        db = self.bot.db
        raid = self.raid_data

        # Calculate raid date from epoch
        raid_date = datetime.fromtimestamp(
            raid.start_time / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d")

        kills = [b for b in raid.bosses if b.kill]
        total_duration = sum(b.duration_ms for b in kills)
        kill_durations = [b.duration_ms for b in kills]

        raid_id = await db.insert_raid(
            report_code=raid.report_code,
            title=raid.title,
            raid_date=raid_date,
            zone=raid.zone,
            boss_kills=len(kills),
            wipe_count=raid.wipe_count,
            duration_ms=total_duration,
            group_id=self.group_id,
            imported_by=imported_by,
        )

        # Upsert players and save attendance + performance
        for player in raid.players:
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
                boss_dmg = raid.boss_damage.get(player.name, {}).get(boss.name, 0)
                boss_heal = raid.boss_healing.get(player.name, {}).get(boss.name, 0)
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


class ParsingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="parse-log", description="Parse a WarcraftLogs report and preview results")
    @app_commands.describe(
        raid_group="Raid group (e.g. Sunday, Tuesday, Wednesday)",
        wcl_url="The WarcraftLogs report URL",
    )
    @app_commands.autocomplete(raid_group=raid_group_autocomplete)
    async def parse_log(self, interaction: discord.Interaction, raid_group: str, wcl_url: str):
        await interaction.response.defer(ephemeral=True)

        group = await self.bot.db.get_raid_group_by_name(raid_group)
        if not group:
            await interaction.followup.send(
                f"Unknown raid group: **{raid_group}**", ephemeral=True,
            )
            return

        # Extract report code
        report_code = extract_report_code(wcl_url)
        if not report_code:
            await interaction.followup.send(
                "Invalid WarcraftLogs URL. Expected format: "
                "`https://fresh.warcraftlogs.com/reports/XXXXX`",
                ephemeral=True,
            )
            return

        # Check for duplicate
        existing = await self.bot.db.get_raid_by_code(report_code)
        if existing:
            await interaction.followup.send(
                f"This report has already been imported (raid date: {existing['raid_date']}). "
                f"Use a different report or contact an admin to re-import.",
                ephemeral=True,
            )
            return

        # Fetch data from WCL
        try:
            raid_data = await self.bot.wcl.fetch_raid_data(report_code)
        except Exception as e:
            await interaction.followup.send(
                f"Error fetching data from WarcraftLogs: {e}",
                ephemeral=True,
            )
            return

        if not raid_data.kill_fight_ids:
            await interaction.followup.send(
                "No boss kills found in this report.", ephemeral=True
            )
            return

        # Build a simple confirmation summary
        kills = [b for b in raid_data.bosses if b.kill]
        from datetime import datetime, timezone as tz
        raid_dt = datetime.fromtimestamp(
            raid_data.start_time / 1000, tz=tz.utc
        )
        kill_names = ", ".join(b.name for b in kills)

        summary = (
            f"**{raid_data.title}** — {raid_dt.strftime('%b %d, %Y')}\n"
            f"Zone: {raid_data.zone or 'Unknown'}\n"
            f"Boss kills: **{len(kills)}** ({kill_names})\n"
            f"Wipes: {raid_data.wipe_count}\n"
            f"Raiders: **{len(raid_data.players)}**\n\n"
            f"Click **Import** to save this log to the database."
        )

        view = ApprovePublishView(raid_data, [], self.bot, group["id"])
        await interaction.followup.send(
            content=summary,
            view=view,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(ParsingCog(bot))
