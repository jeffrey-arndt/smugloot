import re
import time
from dataclasses import dataclass, field

import aiohttp

import config


class WCLAPIError(Exception):
    pass


@dataclass
class PlayerData:
    id: int
    name: str
    server: str
    player_class: str
    spec: str = ""
    role: str = "dps"  # tank, healer, dps
    item_level: float = 0.0
    total_damage: int = 0
    total_healing: int = 0
    active_time_ms: int = 0
    deaths: int = 0
    dps: float = 0.0
    hps: float = 0.0
    interrupts: int = 0
    dispels: int = 0
    parse_pct: float = 0.0  # Average parse % across boss kills
    boss_parses: dict[str, float] = field(default_factory=dict)  # boss_name -> parse %
    aura_ids: set[int] = field(default_factory=set)    # CombatantInfo buff aura IDs
    cast_ids: set[int] = field(default_factory=set)    # Utility spell IDs this player cast
    has_weapon_enchant: bool = False                    # Temporary weapon enchant present
    potion_count: int = 0                              # Number of potion casts across boss kills


@dataclass
class BossData:
    fight_id: int
    name: str
    kill: bool
    duration_ms: int = 0
    encounter_id: int = 0


@dataclass
class RaidData:
    report_code: str
    title: str
    start_time: int = 0
    end_time: int = 0
    zone: str = ""
    bosses: list[BossData] = field(default_factory=list)
    players: list[PlayerData] = field(default_factory=list)
    kill_fight_ids: list[int] = field(default_factory=list)
    wipe_count: int = 0
    # Per-boss damage breakdown: {player_name: {boss_name: damage}}
    boss_damage: dict[str, dict[str, int]] = field(default_factory=dict)
    boss_healing: dict[str, dict[str, int]] = field(default_factory=dict)


def extract_report_code(url: str) -> str | None:
    """Extract the report code from a WarcraftLogs URL."""
    match = re.search(r"reports/([a-zA-Z0-9]+)", url)
    return match.group(1) if match else None


class WCLClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires_at: float = 0

    async def setup(self):
        self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session:
            await self.session.close()

    async def _ensure_token(self):
        if self._token and time.time() < self._token_expires_at - 60:
            return
        async with self.session.post(
            config.WCL_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=aiohttp.BasicAuth(self.client_id, self.client_secret),
        ) as resp:
            data = await resp.json()
            if "access_token" not in data:
                raise WCLAPIError(f"Token request failed: {data}")
            self._token = data["access_token"]
            self._token_expires_at = time.time() + data["expires_in"]

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        await self._ensure_token()
        async with self.session.post(
            config.WCL_API_URL,
            json={"query": query, "variables": variables or {}},
            headers={"Authorization": f"Bearer {self._token}"},
        ) as resp:
            result = await resp.json()
            if "errors" in result:
                raise WCLAPIError(result["errors"])
            return result["data"]

    async def fetch_raid_data(self, report_code: str) -> RaidData:
        """Fetch and process all raid data for a report."""
        # Step 1: Get fights list
        fights_data = await self._query(
            """
            query GetFights($code: String!) {
                reportData {
                    report(code: $code) {
                        title
                        startTime
                        endTime
                        fights {
                            id
                            name
                            kill
                            startTime
                            endTime
                            difficulty
                            encounterID
                        }
                        masterData {
                            actors(type: "Player") {
                                id
                                name
                                server
                                subType
                            }
                        }
                    }
                }
            }
            """,
            {"code": report_code},
        )

        report = fights_data["reportData"]["report"]
        raid = RaidData(
            report_code=report_code,
            title=report["title"],
            start_time=report["startTime"],
            end_time=report["endTime"],
        )

        # Classify fights
        bosses = []
        kill_ids = []
        wipe_count = 0
        for f in report["fights"]:
            if f["encounterID"] and f["encounterID"] > 0:
                boss = BossData(
                    fight_id=f["id"],
                    name=f["name"],
                    kill=f["kill"] or False,
                    duration_ms=f["endTime"] - f["startTime"],
                    encounter_id=f["encounterID"],
                )
                bosses.append(boss)
                if boss.kill:
                    kill_ids.append(f["id"])
                else:
                    wipe_count += 1

        raid.bosses = bosses
        raid.kill_fight_ids = kill_ids
        raid.wipe_count = wipe_count

        if not kill_ids:
            # No boss kills — still return roster data
            raid.players = self._build_roster(report["masterData"]["actors"])
            return raid

        # Step 2: Get table data for boss kills
        table_data = await self._query(
            """
            query GetRaidData($code: String!, $fightIDs: [Int]) {
                reportData {
                    report(code: $code) {
                        dmg: table(dataType: DamageDone, fightIDs: $fightIDs)
                        healing: table(dataType: Healing, fightIDs: $fightIDs)
                        playerDetails(fightIDs: $fightIDs)
                    }
                }
            }
            """,
            {"code": report_code, "fightIDs": kill_ids},
        )

        # Step 3: Get utility data (interrupts, dispels)
        utility_data = await self._query(
            """
            query GetUtility($code: String!, $fightIDs: [Int]) {
                reportData {
                    report(code: $code) {
                        interrupts: table(dataType: Interrupts, fightIDs: $fightIDs)
                        dispels: table(dataType: Dispels, fightIDs: $fightIDs)
                    }
                }
            }
            """,
            {"code": report_code, "fightIDs": kill_ids},
        )

        tbl = table_data["reportData"]["report"]
        util = utility_data["reportData"]["report"]

        # Step 4: Get rankings with correct metrics
        # Default metric (dps) for DPS/tanks, hps metric for healers
        rankings_data = await self._query(
            """
            query GetRankings($code: String!, $fightIDs: [Int]) {
                reportData {
                    report(code: $code) {
                        dps_rankings: rankings(fightIDs: $fightIDs)
                        hps_rankings: rankings(fightIDs: $fightIDs, playerMetric: hps)
                    }
                }
            }
            """,
            {"code": report_code, "fightIDs": kill_ids},
        )

        # Step 5: Get CombatantInfo (consumable buffs), utility casts, and potion casts
        all_utility_spell_ids = set()
        for ids in config.CLASS_UTILITY_SPELLS.values():
            all_utility_spell_ids |= ids
        all_cast_ids = all_utility_spell_ids | config.POTION_IDS
        spell_id_list = ", ".join(str(i) for i in sorted(all_cast_ids))
        cast_filter = f"ability.id IN ({spell_id_list})" if all_cast_ids else "false"

        consumable_data = await self._query(
            """
            query GetConsumables($code: String!, $fightIDs: [Int]) {
                reportData {
                    report(code: $code) {
                        events(dataType: CombatantInfo, fightIDs: $fightIDs, limit: 500) {
                            data
                        }
                    }
                }
            }
            """,
            {"code": report_code, "fightIDs": kill_ids},
        )

        cast_events_data = await self._query(
            """
            query GetUtilityCasts($code: String!, $fightIDs: [Int], $filter: String) {
                reportData {
                    report(code: $code) {
                        events(dataType: Casts, fightIDs: $fightIDs,
                               filterExpression: $filter, limit: 500) {
                            data
                        }
                    }
                }
            }
            """,
            {"code": report_code, "fightIDs": kill_ids, "filter": cast_filter},
        )

        # Build aura map and weapon enchant map from CombatantInfo
        aura_map: dict[int, set[int]] = {}
        weapon_enchant_map: dict[int, bool] = {}
        ci_events = (
            consumable_data.get("reportData", {})
            .get("report", {})
            .get("events", {})
            .get("data", [])
        )
        for ev in ci_events:
            src = ev.get("sourceID")
            if src is None:
                continue
            # Collect buff aura IDs
            auras = aura_map.setdefault(src, set())
            for aura in ev.get("auras", []):
                ability = aura.get("ability")
                if ability:
                    auras.add(ability)
            # Check weapon slots (15, 16) for temporary enchants
            if src not in weapon_enchant_map:
                gear = ev.get("gear", [])
                has_temp = False
                for slot_idx in (15, 16):
                    if slot_idx < len(gear):
                        te = gear[slot_idx].get("temporaryEnchant", 0)
                        if te > 0 and te not in config.IGNORED_TEMP_ENCHANTS:
                            has_temp = True
                            break
                weapon_enchant_map[src] = has_temp

        # Build cast map (utility spells) and potion count map from cast events
        cast_map: dict[int, set[int]] = {}
        potion_count_map: dict[int, int] = {}
        cast_events = (
            cast_events_data.get("reportData", {})
            .get("report", {})
            .get("events", {})
            .get("data", [])
        )
        for ev in cast_events:
            src = ev.get("sourceID")
            ability = ev.get("abilityGameID")
            if src is None or ability is None:
                continue
            if ability in config.POTION_IDS:
                potion_count_map[src] = potion_count_map.get(src, 0) + 1
            if ability in all_utility_spell_ids:
                cast_map.setdefault(src, set()).add(ability)

        dps_rnk = rankings_data["reportData"]["report"]["dps_rankings"]
        hps_rnk = rankings_data["reportData"]["report"]["hps_rankings"]

        # Build parse maps from both metrics
        from collections import defaultdict
        parse_map = {}

        # DPS/tank parses from default metric
        _dps_parses = defaultdict(list)
        for fight in dps_rnk.get("data", []):
            boss_name = fight.get("encounter", {}).get("name", "Unknown")
            for role_key in ("tanks", "dps"):
                for char in fight.get("roles", {}).get(role_key, {}).get("characters", []):
                    _dps_parses[char["name"]].append((boss_name, char.get("rankPercent", 0)))

        # Healer parses from hps metric
        _hps_parses = defaultdict(list)
        for fight in hps_rnk.get("data", []):
            boss_name = fight.get("encounter", {}).get("name", "Unknown")
            for char in fight.get("roles", {}).get("healers", {}).get("characters", []):
                _hps_parses[char["name"]].append((boss_name, char.get("rankPercent", 0)))

        # Merge into parse_map
        for name, boss_parses in _dps_parses.items():
            avg = sum(p for _, p in boss_parses) / len(boss_parses) if boss_parses else 0
            parse_map[name] = {
                "avg": round(avg, 1),
                "bosses": {b: round(p) for b, p in boss_parses},
            }
        for name, boss_parses in _hps_parses.items():
            avg = sum(p for _, p in boss_parses) / len(boss_parses) if boss_parses else 0
            parse_map[name] = {
                "avg": round(avg, 1),
                "bosses": {b: round(p) for b, p in boss_parses},
            }

        # Calculate total fight duration for DPS/HPS (matches WCL's calculation)
        total_fight_duration_ms = sum(b.duration_ms for b in bosses if b.kill)
        total_fight_duration_secs = total_fight_duration_ms / 1000 if total_fight_duration_ms > 0 else 1

        # Build player lookup from masterData
        actor_map = {
            a["id"]: a for a in report["masterData"]["actors"]
        }

        # Parse playerDetails for role/spec info
        # Structure: {data: {playerDetails: {tanks: [...], healers: [...], dps: [...]}}}
        spec_map = {}  # player_name -> {spec, role}
        details = tbl.get("playerDetails", {})
        if isinstance(details, dict) and "data" in details:
            details = details["data"]
            if isinstance(details, dict) and "playerDetails" in details:
                details = details["playerDetails"]
        if isinstance(details, dict):
            for role_key in ("tanks", "healers", "dps"):
                for p in details.get(role_key, []):
                    role = "tank" if role_key == "tanks" else (
                        "healer" if role_key == "healers" else "dps"
                    )
                    # Spec is in the specs array, pick the most common one
                    spec = ""
                    specs = p.get("specs", [])
                    if specs:
                        spec = max(specs, key=lambda s: s.get("count", 0)).get("spec", "")
                    spec_map[p["name"]] = {
                        "spec": spec,
                        "role": role,
                    }

        # Build damage map
        dmg_by_name = {}
        boss_damage = {}
        for entry in tbl["dmg"]["data"]["entries"]:
            name = entry["name"]
            dmg_by_name[name] = entry
            # Per-boss breakdown from targets
            boss_damage[name] = {}
            for target in entry.get("targets", []):
                boss_damage[name][target["name"]] = target["total"]
        raid.boss_damage = boss_damage

        # Build healing map
        healing_by_name = {}
        boss_healing = {}
        for entry in tbl["healing"]["data"]["entries"]:
            name = entry["name"]
            healing_by_name[name] = entry
            boss_healing[name] = {}
            for target in entry.get("targets", []):
                boss_healing[name][target["name"]] = target["total"]
        raid.boss_healing = boss_healing

        # Build interrupt/dispel maps
        # Structure: entries[] -> spell entries[] -> details[] (players)
        interrupts_by_name = {}
        if util.get("interrupts") and util["interrupts"].get("data"):
            for spell_group in util["interrupts"]["data"].get("entries", []):
                for spell_entry in spell_group.get("entries", []):
                    for player in spell_entry.get("details", []):
                        name = player["name"]
                        interrupts_by_name[name] = (
                            interrupts_by_name.get(name, 0) + player.get("total", 0)
                        )

        dispels_by_name = {}
        if util.get("dispels") and util["dispels"].get("data"):
            for spell_group in util["dispels"]["data"].get("entries", []):
                for spell_entry in spell_group.get("entries", []):
                    for player in spell_entry.get("details", []):
                        name = player["name"]
                        dispels_by_name[name] = (
                            dispels_by_name.get(name, 0) + player.get("total", 0)
                        )

        # Assemble player list
        players = []
        for actor in report["masterData"]["actors"]:
            name = actor["name"]
            if actor["subType"] == "Unknown":
                continue  # Skip pets/unknowns

            dmg = dmg_by_name.get(name, {})
            heal = healing_by_name.get(name, {})
            spec_info = spec_map.get(name, {"spec": "", "role": "dps"})

            total_damage = dmg.get("total", 0)
            total_healing = heal.get("total", 0)
            active_ms = dmg.get("activeTime", 0) or heal.get("activeTime", 0)

            # Use total fight duration as denominator (matches WCL website)
            dps = total_damage / total_fight_duration_secs if total_damage > 0 else 0
            hps = total_healing / total_fight_duration_secs if total_healing > 0 else 0

            actor_id = actor["id"]
            player = PlayerData(
                id=actor_id,
                name=name,
                server=actor["server"],
                player_class=actor["subType"],
                spec=spec_info["spec"],
                role=spec_info["role"],
                item_level=dmg.get("itemLevel", 0) or heal.get("itemLevel", 0) or 0,
                total_damage=total_damage,
                total_healing=total_healing,
                active_time_ms=active_ms,
                deaths=0,  # tracked via performance, not separate
                dps=round(dps, 1),
                hps=round(hps, 1),
                interrupts=interrupts_by_name.get(name, 0),
                dispels=dispels_by_name.get(name, 0),
                parse_pct=round(parse_map.get(name, {}).get("avg", 0), 1),
                boss_parses=parse_map.get(name, {}).get("bosses", {}),
                aura_ids=aura_map.get(actor_id, set()),
                cast_ids=cast_map.get(actor_id, set()),
                has_weapon_enchant=weapon_enchant_map.get(actor_id, False),
                potion_count=potion_count_map.get(actor_id, 0),
            )
            players.append(player)

        raid.players = players
        return raid

    def _build_roster(self, actors: list[dict]) -> list[PlayerData]:
        """Build a basic roster from masterData actors (no performance data)."""
        players = []
        for a in actors:
            if a["subType"] == "Unknown":
                continue
            players.append(PlayerData(
                id=a["id"],
                name=a["name"],
                server=a["server"],
                player_class=a["subType"],
            ))
        return players

    async def fetch_report_roster(self, report_code: str) -> tuple[list[str], int]:
        """Lightweight fetch: just player names and start time.

        Returns (player_names, start_time_epoch_ms).
        Used for pug credit verification without importing full data.
        """
        data = await self._query(
            """
            query GetRoster($code: String!) {
                reportData {
                    report(code: $code) {
                        startTime
                        masterData {
                            actors(type: "Player") {
                                name
                                subType
                            }
                        }
                    }
                }
            }
            """,
            {"code": report_code},
        )
        report = data["reportData"]["report"]
        names = [
            a["name"] for a in report["masterData"]["actors"]
            if a["subType"] != "Unknown"
        ]
        return names, report["startTime"]

