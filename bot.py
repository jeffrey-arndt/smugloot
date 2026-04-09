import discord
from discord.ext import commands

from pathlib import Path

import config
from database import Database
from loot_priority import parse_all_priority_csvs
from wcl_client import WCLClient

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.db = Database(config.DB_PATH)
bot.wcl = WCLClient(config.WCL_CLIENT_ID, config.WCL_CLIENT_SECRET)


@bot.event
async def setup_hook():
    await bot.db.setup()
    await bot.wcl.setup()
    # Seed raid groups
    for group_name in ("Sunday", "Tuesday", "Wednesday"):
        await bot.db.create_raid_group(group_name)
    # Import priority CSVs on startup (idempotent — overwrites by phase)
    items = parse_all_priority_csvs(Path(__file__).parent)
    if items:
        await bot.db.import_priority_data(items)
    await bot.load_extension("cogs.parsing")
    await bot.load_extension("cogs.loot")
    target_guild = discord.Object(id=config.GUILD_ID)
    bot.tree.copy_global_to(guild=target_guild)
    synced = await bot.tree.sync(guild=target_guild)
    print(f"Synced {len(synced)} commands")


@bot.event
async def on_ready():
    guild = bot.get_guild(config.GUILD_ID)
    if not guild:
        print(f"ERROR: Bot is not in guild {config.GUILD_ID}. Check DISCORD_GUILD_ID.")
        return
    print(f"SmugLoot is ready: {bot.user} | Guild: {guild.name}")


def main():
    if not config.BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set in .env")
        return
    if not config.WCL_CLIENT_ID:
        print("ERROR: WCL_CLIENT_ID not set in .env")
        return
    bot.run(config.BOT_TOKEN)


if __name__ == "__main__":
    main()
