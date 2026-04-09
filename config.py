import os

from dotenv import load_dotenv

load_dotenv()

# Discord
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))
PUBLISH_CHANNEL_ID = int(os.environ.get("PUBLISH_CHANNEL_ID", "0"))

# WarcraftLogs
WCL_CLIENT_ID = os.environ.get("WCL_CLIENT_ID", "")
WCL_CLIENT_SECRET = os.environ.get("WCL_CLIENT_SECRET", "")
WCL_API_URL = "https://fresh.warcraftlogs.com/api/v2/client"
WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"

# Database
DB_PATH = os.path.join(os.path.dirname(__file__), "smug_loot.db")

# ─── Scoring weights (must sum to 1.0) ────────────────────────────────────
WEIGHT_ATTENDANCE = 0.40
WEIGHT_PERFORMANCE = 0.35
WEIGHT_UTILITY = 0.15
WEIGHT_PRIORITY = 0.10   # Item-specific, applied per-item in assignment engine

# ─── Priority rank scoring ───────────────────────────────────────────────
PRIORITY_MAX = 1          # Max priority points (rank 1 = full)
PRIORITY_RANK_DROP = 0.25 # Score drop per rank tier (R1=1.0, R2=0.75, R3=0.5, R4=0.25)

# ─── Loot penalty ────────────────────────────────────────────────────────
LOOT_PENALTY_PER_ITEM = 1.5   # Max score reduction per LC item received
LOOT_PENALTY_DECAY_WEEKS = 4  # Penalty decays linearly to 0 over this many weeks

# ─── Score scale ──────────────────────────────────────────────────────────
SCORE_MAX = 10.0
ATTENDANCE_MAX = 4       # Max attendance points (1 per week)
PERFORMANCE_MAX = 7      # Gold parse bracket
UTILITY_MAX = 4          # Consumables + class utility + interrupts + potions

# ─── Attendance ───────────────────────────────────────────────────────────
ATTENDANCE_WEEKS = 4     # Look-back window in calendar weeks

# ─── Rolling averages ────────────────────────────────────────────────────
ROLLING_AVG_WEEKS = 4    # Look-back window for parse/utility rolling averages

# ─── Mechanic override ───────────────────────────────────────────────────
MECHANIC_DUTY_BONUS_PCT = 5  # Bonus parse % added when substituting for mechanic duty

# ─── Performance: WCL parse color brackets ────────────────────────────────
# (min_parse_pct, points, bracket_name)
PARSE_BRACKETS = [
    (100, 7, "Gold"),
    (99,  6, "Pink"),
    (95,  5, "Orange"),
    (75,  4, "Purple"),
    (50,  3, "Blue"),
    (25,  2, "Green"),
    (0,   1, "Gray"),
]

# ─── Utility: interrupts ─────────────────────────────────────────────────
INTERRUPT_THRESHOLD = 1  # Min interrupts per raid to earn the point
INTERRUPT_CLASSES = {"Warrior", "Rogue", "Shaman", "Mage"}

# ─── Utility: consumable spell IDs (from CombatantInfo auras) ────────────
# These are BUFF aura IDs that WCL reports in CombatantInfo events.
# Run /parse-log on a real log and check the "unknown aura" debug output
# to verify / add missing IDs for your raid.

FLASK_IDS = {
    28520,  # Flask of Relentless Assault
    28540,  # Flask of Pure Death
    28521,  # Flask of Blinding Light
    28519,  # Flask of Mighty Restoration
    28518,  # Flask of Fortification
    42735,  # Flask of Chromatic Wonder
    17627,  # Distilled Wisdom (Classic)
    17626,  # Flask of the Titans (Classic)
}

BATTLE_ELIXIR_IDS = {
    28497,  # Elixir of Major Agility
    38954,  # Fel Strength Elixir
    28503,  # Elixir of Major Shadow Power
    28501,  # Elixir of Major Firepower
    28493,  # Elixir of Major Frost Power
    28491,  # Elixir of Healing Power
    33726,  # Adept's Elixir
    33720,  # Onslaught Elixir
    28490,  # Elixir of Major Strength
    33721,  # Elixir of Mastery
    17539,  # Greater Arcane Elixir (Classic, +35 spell damage)
    11334,  # Elixir of Greater Agility (Classic)
    11406,  # Elixir of Demonslaying (Classic, +265 AP vs demons)
}

GUARDIAN_ELIXIR_IDS = {
    28509,  # Elixir of Major Mageblood
    28514,  # Elixir of Draenic Wisdom
    28502,  # Elixir of Major Defense
    39628,  # Elixir of Ironskin
    39627,  # Earthen Elixir
    28511,  # Elixir of Major Fortitude
    39625,  # Elixir of Major Fortitude (alternate buff ID, seen in logs)
    11371,  # Gift of Arthas (Classic guardian elixir)
    11348,  # Greater Armor / Elixir of Superior Defense (Classic)
}

FOOD_BUFF_IDS = {
    33257,  # Well Fed (Agility — Warp Burger / Grilled Mudfish)
    33261,  # Well Fed (Stamina — Spicy Crawdad)
    33256,  # Well Fed (Spell Dmg — Blackened Basilisk)
    33259,  # Well Fed (Spell Dmg — Crunchy Serpent)
    33263,  # Well Fed (Strength — Roasted Clefthoof)
    33265,  # Well Fed (Healing — Golden Fish Sticks)
    33254,  # Well Fed (AP — Ravager Dog)
    43764,  # Well Fed (Hit Rating — Spicy Hot Talbuk)
    43722,  # Well Fed (Spell Crit — Skullfish Soup)
    33268,  # Well Fed (mp5 — Blackened Sporefish)
}

# Weapon buffs: detected via gear[slot].temporaryEnchant in CombatantInfo,
# NOT via aura IDs. Any non-zero temporaryEnchant = weapon buff present,
# UNLESS it's a party/totem buff (not a self-applied consumable).
IGNORED_TEMP_ENCHANTS = {
    2639,  # zzOLDWindfury Totem 5 — shaman totem buff, not a consumable
}

# ─── Utility: potion spell IDs (detected via Cast events) ────────────────
# 1 point if total potion casts >= number of boss kills (1 per boss).
POTION_IDS = {
    28507,  # Haste Potion
    28508,  # Destruction Potion
    28515,  # Ironshield Potion
    28499,  # Super Mana Potion
    28495,  # Super Healing Potion
    17531,  # Major Mana Potion (Classic)
    38929,  # Fel Mana Potion
}

# ─── Utility: class-specific spells to track via Casts ────────────────────
# Player earns the point if they cast ANY spell from their class set.
# Empty set = auto-pass (no trackable utility for that class).
CLASS_UTILITY_SPELLS = {
    "Warrior": {
        25225,  # Sunder Armor r6
        25203,  # Demoralizing Shout r7
        25264,  # Thunder Clap r7
    },
    "Warlock": {
        27228,  # Curse of the Elements r4
        27226,  # Curse of Recklessness r5
        603,    # Curse of Doom r1
        30910,  # Curse of Doom r2
        18647,  # Banish r2
        710,    # Banish r1
    },
    "Druid": {
        27011,  # Faerie Fire r5
        27013,  # Faerie Fire (Feral) r5
        29166,  # Innervate
    },
    "Priest": {
        34917,  # Vampiric Touch r3
        10060,  # Power Infusion
    },
    "Mage": set(),  # Auto-pass — Imp Scorch is fire-only, not universal
    "Shaman": set(),  # Auto-pass — BL duty shared among multiple shamans
    "Paladin": {
        27170,  # Judgement of Light r5
        27164,  # Judgement of Wisdom r4
        27160,  # Seal of Light r5
        20357,  # Seal of Wisdom r3
        27166,  # Seal of Wisdom r4
        10278,  # Blessing of Protection r3
        1044,   # Blessing of Freedom
    },
    "Hunter": {
        34477,  # Misdirection
    },
    "Rogue": {
        26866,  # Expose Armor r6
    },
}

# ─── Trend thresholds (for future use) ───────────────────────────────────
TREND_MODERATE = 2.0
TREND_LARGE = 5.0

# ─── WoW class colors for Discord embeds (hex) ───────────────────────────
CLASS_COLORS = {
    "Warrior":  0xC79C6E,
    "Paladin":  0xF58CBA,
    "Hunter":   0xABD473,
    "Rogue":    0xFFF569,
    "Priest":   0xFFFFFF,
    "Shaman":   0x0070DE,
    "Mage":     0x69CCF0,
    "Warlock":  0x9482C9,
    "Druid":    0xFF7D0A,
}

# Role classification by WCL spec name
TANK_SPECS = {
    "Protection", "Feral",
}

HEALER_SPECS = {
    "Restoration", "Holy", "Discipline",
}
