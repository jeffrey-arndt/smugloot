"""Loot priority sheet parser and data model.

Reads CSV files exported from the guild's loot bias spreadsheets
and produces structured item priority data for storage and lookup.

This module is pure logic — no Discord, no database, no async.
"""

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path


# ── Spec alias → (player_class, spec) mapping ───────────────────────────
#
# Each alias maps to a list of (class, spec) possibilities.
# Single-element lists are unambiguous.
# Multi-element lists are ambiguous — resolved at query time against the
# actual raid roster.
# Empty spec string means "any spec of that class."

SPEC_ALIAS_MAP: dict[str, list[tuple[str, str]]] = {
    # ── Warrior ──────────────────────────────────────────────────────────
    "Fury":             [("Warrior", "Fury")],
    "Fury Warrior":     [("Warrior", "Fury")],
    "Arms":             [("Warrior", "Arms")],
    "2H Arms":          [("Warrior", "Arms")],
    "2H Arms 4 Piece":  [("Warrior", "Arms")],
    "DPS Warrior":      [("Warrior", "Fury"), ("Warrior", "Arms")],
    "Prot Warrior":     [("Warrior", "Protection")],
    # ── Paladin ──────────────────────────────────────────────────────────
    "Ret":              [("Paladin", "Retribution")],
    "Retribution":      [("Paladin", "Retribution")],
    "Holy Paladin":     [("Paladin", "Holy")],
    "Prot Paladin":     [("Paladin", "Protection")],
    "Prot":             [("Paladin", "Protection")],
    # ── Hunter ───────────────────────────────────────────────────────────
    "BM":               [("Hunter", "Beast Mastery")],
    "Survival":         [("Hunter", "Survival")],
    "Hunter":           [("Hunter", "")],
    # ── Rogue ────────────────────────────────────────────────────────────
    "Rogue":            [("Rogue", "")],
    # ── Priest ───────────────────────────────────────────────────────────
    "Shadow":           [("Priest", "Shadow")],
    "Shadow Priest":    [("Priest", "Shadow")],
    "Holy Priest":      [("Priest", "Holy")],
    "Healing Priest":   [("Priest", "Holy")],
    # ── Druid ────────────────────────────────────────────────────────────
    "Feral":            [("Druid", "Feral")],
    "Bear":             [("Druid", "Feral")],
    "Cat":              [("Druid", "Feral")],
    "Cat/Warden":       [("Druid", "Feral")],
    "Boomkin":          [("Druid", "Balance")],
    "Balance":          [("Druid", "Balance")],
    "Resto Druid":      [("Druid", "Restoration")],
    # ── Shaman ───────────────────────────────────────────────────────────
    "Enh":              [("Shaman", "Enhancement")],
    "Enhancement":      [("Shaman", "Enhancement")],
    "Ele":              [("Shaman", "Elemental")],
    "Elemental":        [("Shaman", "Elemental")],
    "Resto Shaman":     [("Shaman", "Restoration")],
    "Kebab":            [("Shaman", "Enhancement")],
    "Kebab 4 Piece":    [("Shaman", "Enhancement")],
    "Enh 4 Piece":      [("Shaman", "Enhancement")],
    # ── Mage ─────────────────────────────────────────────────────────────
    "Arcane":           [("Mage", "Arcane")],
    "Fire":             [("Mage", "Fire")],
    "Fire Mage":        [("Mage", "Fire")],
    "Mage":             [("Mage", "")],
    "Mages":            [("Mage", "")],
    # ── Warlock ──────────────────────────────────────────────────────────
    "Destro":           [("Warlock", "Destruction")],
    "Affliction":       [("Warlock", "Affliction")],
    "Shadow Lock":      [("Warlock", "Affliction")],
    "Fire Warlock":     [("Warlock", "Destruction")],
    "Warlock":          [("Warlock", "")],
    "Warlocks":         [("Warlock", "")],
    # ── Ambiguous (multiple possible classes) ────────────────────────────
    "Holy":             [("Priest", "Holy"), ("Paladin", "Holy")],
    "Resto":            [("Druid", "Restoration"), ("Shaman", "Restoration")],
    "Restoration":      [("Druid", "Restoration"), ("Shaman", "Restoration")],
    "Restortation":     [("Druid", "Restoration"), ("Shaman", "Restoration")],
    "Protection":       [("Warrior", "Protection"), ("Paladin", "Protection")],
    # ── Multi-class / conditional groups ─────────────────────────────────
    "Arms/Ret":         [("Warrior", "Arms"), ("Paladin", "Retribution")],
    "Casters":          [("Mage", ""), ("Warlock", ""), ("Priest", "Shadow")],
    "Caster DPS":       [("Mage", ""), ("Warlock", ""), ("Priest", "Shadow")],
    "Non Sunshower":    [("Priest", "Holy"), ("Paladin", "Holy"),
                         ("Druid", "Restoration"), ("Shaman", "Restoration")],
    "Non LW Resto":     [("Druid", "Restoration"), ("Shaman", "Restoration")],
    "Armor Crafts":     [],   # Generic crafting category — no spec match
}

OPERATORS = {">", "=", ">="}

# Values to skip when scanning trailing columns for notes
_NOTE_SKIP = OPERATORS | {
    "MS > OS", "Void",
    "Main Raid", "Split 1", "Split 2", "Main", "Bias",
}


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class PriorityEntry:
    """One spec/group in an item's priority chain."""
    rank: int                                          # 1 = highest
    spec_alias: str                                    # Raw alias from CSV
    class_options: list[tuple[str, str]] = field(      # [(class, spec), ...]
        default_factory=list,
    )


@dataclass
class ItemPriority:
    """Parsed priority data for a single item drop."""
    item_name: str
    boss_name: str
    phase: str               # "P1", "P2", "P3"
    raid: str                # "", "SSC", "TK", "BT", "MH"
    priority_type: str       # "chain", "void", "ms_os"
    entries: list[PriorityEntry] = field(default_factory=list)
    notes: str = ""
    skew: str = ""           # "speedrunning", "balanced", "healer", ""


# ── Internal helpers ─────────────────────────────────────────────────────

def _normalize_alias(raw: str) -> str:
    """Strip cosmetic suffixes from a raw spec alias."""
    s = raw.strip().strip('"').rstrip("*").strip()
    # "Destro (1)" → "Destro", "Arcane (2)" → "Arcane"
    s = re.sub(r"\s*\(\d+\)\s*$", "", s)
    # "Fury 4 Piece" → "Fury", "BM 4 piece" → "BM"
    s = re.sub(r"\s+4\s+[Pp]iece\s*$", "", s)
    return s


def _detect_skew(item_name: str) -> tuple[str, str]:
    """Extract skew variant from item name.

    Returns (clean_name, skew).
    """
    m = re.search(r"\((\w[\w\s]*?)\s+Skew\)", item_name)
    if m:
        return item_name[: m.start()].strip(), m.group(1).strip().lower()
    if "Healer Prio" in item_name:
        return item_name.replace("Healer Prio", "").strip(), "healer"
    return item_name, ""


def _extract_notes(row: list[str], start: int) -> str:
    """Scan trailing columns for note text, skipping legend/header cells."""
    parts: list[str] = []
    for i in range(start, len(row)):
        cell = row[i].strip().strip("*").strip()
        if not cell:
            continue
        if cell in _NOTE_SKIP:
            continue
        if cell in SPEC_ALIAS_MAP:
            continue
        parts.append(cell)
    return " | ".join(parts)


def _parse_phase_raid(filename: str) -> tuple[str, str]:
    """Derive (phase, raid) from a CSV filename.

    "Juggs Loot Bias Spreadsheet - Phase 2 SSC.csv" → ("P2", "SSC")
    """
    m = re.search(r"Phase\s+(\d+)\s*(.*?)\.csv$", filename, re.IGNORECASE)
    if m:
        return f"P{m.group(1)}", m.group(2).strip()
    return "Unknown", ""


def _parse_chain(row: list[str]) -> tuple[str, list[PriorityEntry], str]:
    """Parse the priority chain from columns 1+ of an item row.

    Returns (priority_type, entries, notes).
    """
    if len(row) < 2 or not row[1].strip():
        return "ms_os", [], ""

    first = _normalize_alias(row[1])

    if first == "Void":
        return "void", [], _extract_notes(row, 2)
    if first == "MS > OS":
        return "ms_os", [], _extract_notes(row, 2)

    entries: list[PriorityEntry] = []
    rank = 1

    # First spec
    opts = SPEC_ALIAS_MAP.get(first, [])
    entries.append(PriorityEntry(rank=rank, spec_alias=first, class_options=list(opts)))

    # Alternating operator / spec pairs
    i = 2
    while i < len(row):
        op_cell = row[i].strip()
        if not op_cell or op_cell not in OPERATORS:
            # Chain ended — everything from here is notes
            break

        if op_cell == ">":
            rank += 1
        # "=" and ">=" keep the same rank

        i += 1
        if i >= len(row):
            break

        spec_raw = row[i].strip()
        if not spec_raw:
            break

        spec_alias = _normalize_alias(spec_raw)

        # Terminal "MS > OS" after specific priorities
        if spec_alias == "MS > OS":
            entries.append(
                PriorityEntry(rank=rank, spec_alias="MS > OS", class_options=[])
            )
            i += 1
            break

        if spec_alias == "Void":
            i += 1
            break

        opts = SPEC_ALIAS_MAP.get(spec_alias, [])
        entries.append(
            PriorityEntry(rank=rank, spec_alias=spec_alias, class_options=list(opts))
        )
        i += 1

    notes = _extract_notes(row, i)
    has_real = any(e.spec_alias != "MS > OS" for e in entries)
    ptype = "chain" if has_real else "ms_os"
    return ptype, entries, notes


# ── Public API ───────────────────────────────────────────────────────────

def parse_priority_csv(file_path: str | Path) -> list[ItemPriority]:
    """Parse a single loot priority CSV into ItemPriority objects."""
    file_path = Path(file_path)
    phase, raid = _parse_phase_raid(file_path.name)

    items: list[ItemPriority] = []
    current_boss = ""

    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or not row[0].strip():
                continue

            name = row[0].strip().strip('"')

            # Skip headers, patterns, junk
            if name == "Item Name" or name.startswith("Pattern:"):
                continue
            if len(name) <= 1 and not name.isalnum():
                continue

            second = row[1].strip() if len(row) > 1 else ""

            # Boss / section header: nothing meaningful in column 1
            if not second or second == "Bias":
                current_boss = name
                continue

            # Verify this looks like an item row
            norm = _normalize_alias(second)
            is_known = norm in SPEC_ALIAS_MAP or norm in {"Void", "MS > OS"}
            has_ops = any(
                row[j].strip() in OPERATORS
                for j in range(2, min(6, len(row)))
            )

            if not is_known and not has_ops:
                # Section header like "Trash" or misc text
                current_boss = name
                continue

            clean_name, skew = _detect_skew(name)
            ptype, entries, notes = _parse_chain(row)

            items.append(
                ItemPriority(
                    item_name=clean_name,
                    boss_name=current_boss,
                    phase=phase,
                    raid=raid,
                    priority_type=ptype,
                    entries=entries,
                    notes=notes,
                    skew=skew,
                )
            )

    return items


def parse_all_priority_csvs(directory: str | Path) -> list[ItemPriority]:
    """Parse every loot bias CSV in *directory*."""
    directory = Path(directory)
    all_items: list[ItemPriority] = []
    for csv_file in sorted(directory.glob("Juggs Loot Bias Spreadsheet*.csv")):
        all_items.extend(parse_priority_csv(csv_file))
    return all_items
