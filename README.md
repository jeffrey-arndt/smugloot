# SmugLoot

Loot council automation bot for **\<smug\>** — WoW Classic Fresh TBC.

SmugLoot tracks raider performance across WarcraftLogs reports and generates data-driven loot priority rankings. Every LC item decision is backed by attendance, parse performance, consumable usage, and utility contribution — averaged over a 4-week rolling window.

---

## For Raiders: How You Are Scored

> **IMPORTANT:** SmugLoot is a **tool to assist** the Loot Council — it does not make loot decisions. The scores and rankings below are one input into the LC discussion, not the final word. The Loot Council reserves the right to make the final decision on all loot awards, taking into account factors that may not be captured by any automated system (raid composition needs, exceptional circumstances, long-term guild health, etc.). If you have questions about a specific loot decision, talk to an officer.

Your loot priority score is on a **0–10 scale**, calculated from four categories:

| Category | Weight | Max Points | What It Measures |
|---|---|---|---|
| **Attendance** | 40% | 4 | How many of the last 4 weeks you attended |
| **Performance** | 35% | 7 | Your WarcraftLogs parse % (averaged over 4 weeks) |
| **Utility** | 15% | 4 | Consumables, class utility, interrupts, potions |
| **Item Priority** | 10% | 1 | Your spec's rank on the item's priority chain |

### Attendance (40%)

You earn 1 point per week attended, up to 4. This is the single biggest factor in your score.

- Show up to raid = 1 point for that week
- Attendance is tracked per raid group (Sunday, Tuesday, etc.) — no cross-group credit

**Pug Credit:** If you have a legitimate reason you can't make a scheduled raid (work, travel, etc.), you can request pug credit by running the content on your own and submitting the WarcraftLogs link via `/pug-credit`. **You must get prior approval from an officer before the raid you'll miss.** This is meant for occasional, unavoidable absences — not a way to skip guild raids and maintain attendance. Abuse of pug credit will result in it being revoked.

**How to maximize:** Show up every week. If you know you'll miss a raid, communicate with an officer ahead of time and pug the content to maintain your attendance score.

### Performance (35%)

Your average parse % across all boss kills over the last 4 weeks, mapped to WCL color brackets:

| Parse % | Bracket | Points |
|---|---|---|
| 100 | Gold | 7 |
| 99+ | Pink | 6 |
| 95+ | Orange | 5 |
| 75+ | Purple | 4 |
| 50+ | Blue | 3 |
| 25+ | Green | 2 |
| 0+ | Gray | 1 |

Your parse is averaged **per boss** across weeks, so one bad fight doesn't tank your entire score. The 4-week window means consistency matters more than a single great night.

**Tanks** are exempt from parse scoring — tank parses don't reflect performance the same way. Tanks are scored on attendance, utility, and consumables only.

**Mechanic duty** — We recognize that certain raid assignments hurt your parse through no fault of your own (e.g. Mag clickers, warlock tanking Leo, mage tanking Krosh). Officers can flag your parse on a specific boss as mechanic duty using `/mechanic-override`. When flagged, your parse for that fight gets replaced with your average on other bosses + a small bonus. If you're assigned to the same mechanic job every week, the system uses your performance on the other bosses as the baseline — you will never be penalized for doing what the raid needs you to do. If you believe a mechanic assignment affected your parse and hasn't been accounted for, let an officer know.

**How to maximize:** Play well consistently. One bad week won't ruin you, but don't coast either.

### Utility (15%)

Four sub-checks, each worth 1 point (max 4):

**1. Consumables** — You must have ALL THREE:
- Flask OR (Battle Elixir + Guardian Elixir)
- Food buff
- Weapon buff (oil, weightstone, sharpening stone, etc.)

**2. Class Utility** — Did you use your class's expected abilities?
- Warriors: Sunder Armor, Demo Shout, or Thunder Clap
- Warlocks: Curse of Elements, Curse of Recklessness, Curse of Doom, or Banish
- Druids: Faerie Fire or Innervate
- Priests: Vampiric Touch or Power Infusion
- Paladins: Judgement/Seal of Light or Wisdom, BoP, or BoF
- Hunters: Misdirection
- Rogues: Expose Armor
- Mages/Shamans: Auto-pass (shared duties, not individually trackable)

**3. Interrupts** — If your class can interrupt (Warrior, Rogue, Shaman, Mage), you need at least 1 interrupt per raid. Other classes auto-pass.

**4. Potions** — Use at least 1 potion per boss kill. 3 boss kills = 3 potions minimum. Any combat potion counts (Haste, Destruction, Ironshield, Mana, Healing, Fel Mana).

**How to maximize:** Flask up, eat food, oil your weapon, use your class utility spells, interrupt when you can, and pot every boss. This is the easiest category to get 4/4 on — it's entirely within your control.

### Item Priority (10%)

Each LC item has a priority chain based on spec value. For example:

> Dragonspine Trophy: Rogue = Fury Warrior > Hunter > Enhancement > Retribution = Feral

- Rank 1 specs get +1.0 priority points
- Rank 2 gets +0.75
- Rank 3 gets +0.5
- Rank 4 gets +0.25

Priority is a **weight, not a gate**. A Rank 2 player with much better attendance and performance can still outrank a Rank 1 player.

### Loot Penalty

Each LC item you receive applies a **-1.5 point penalty** to your score. This penalty decays linearly to 0 over 4 weeks. If you received an item 2 weeks ago, the remaining penalty is about -0.75. This ensures loot gets spread around without hard caps.

### Your Data

The bot tracks detailed week-over-week data for every raider including per-boss parses, consumable usage, attendance history, and score trends. Tools like `/compare` and `/loot-history` exist to provide full transparency into how scores are calculated. We are still deciding how much of this data will be directly accessible to raiders vs. available through an LC member, but the data is there and we are happy to walk through your numbers with you if you have questions about a loot decision.

---

## For Loot Council: Bot Commands

### During Raid

| Command | Purpose |
|---|---|
| `/parse-log` | Import a WCL report. Shows a confirmation (date, bosses, player count) with an Import button. |
| `/assign <item>` | Pull up the top 5 candidates for an item. Shows per-week attendance, parse avg, utility, loot penalty, and priority rank — everything you need for a quick LC call. |
| `/award <player> <item>` | Record a loot award. Applies the -1.5 loot penalty and posts to the public channel. |
| `/mechanic-override <players> <boss>` | Flag mechanic duty for one or more players (comma-separated). Their parse on that boss gets replaced with their average + bonus. Defaults to most recent raid, or specify `raid_date:YYYY-MM-DD`. |

### Audit / Fact-Checking

| Command | Purpose |
|---|---|
| `/compare <players>` | Week-over-week breakdown for 1+ raiders (comma-separated). Shows per-boss parses, consumables, utility, interrupts, potions, attendance, loot history, and trend. Add `item_name:` to see assignment score comparison. |
| `/scores` | Full roster ranked by base score (no item priority applied). |
| `/attendance` | Per-week attendance grid for all raiders. Shows which weeks each player attended, with pug (P) and manual (M) credit marked. |
| `/loot-history <player>` | What a player has received and their active loot penalty with decay timers. |

### Administration

| Command | Purpose |
|---|---|
| `/pug-credit <wcl_url>` | Grant attendance credit to guild members found in a pug log. |
| `/attendance-credit <player>` | Officer-granted manual attendance credit for the current week. |
| `/link <main> <alt>` | Link an alt character to a main. Attendance merges across linked characters. |
| `/unlink <character>` | Remove an alt link. |
| `/characters <player>` | Show all characters linked to a player. |

### Example Workflow

1. After raid, run `/parse-log` with the WCL link and click Import
2. If anyone had mechanic duty, run `/mechanic-override players:Name1,Name2 boss:Magtheridon`
3. When an LC item drops, run `/assign item_name:Dragonspine Trophy` to see the top 5
4. If someone questions the ranking, run `/compare players:Player1,Player2 item_name:Dragonspine Trophy` to show the full breakdown
5. After the LC decision, run `/award player_name:Winner item_name:Dragonspine Trophy`
