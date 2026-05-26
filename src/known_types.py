"""Seed the known_types table with OPTCG sub_types.

Idempotent — re-running never duplicates. Expand SEED_TYPES as unknown_type_log
reveals new types during sync.

Run:
    python -m src.known_types
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from src.db_setup import DB_PATH, connect

SEED_TYPES: list[str] = [
    # Core factions (API uses "The" prefix variants)
    "Marine",
    "Navy",
    "Revolutionary Army",
    "Cipher Pol",
    "CP0",
    "CP6",
    "CP7",
    "CP8",
    "CP9",
    "Former CP9",
    "World Government",
    "The Seven Warlords of the Sea",
    "Seven Warlords of the Sea",
    "The Four Emperors",
    "Four Emperors",
    "Worst Generation",
    "Supernovas",
    "Impel Down",
    "Baroque Works",
    "Former Baroque Works",
    "Shichibukai",
    # Pirate crews
    "Straw Hat Crew",
    "Fake Straw Hat Crew",
    "Heart Pirates",
    "Kid Pirates",
    "Firetank Pirates",
    "Fire Tank Pirates",
    "On-Air Pirates",
    "Hawkins Pirates",
    "Bonney Pirates",
    "Drake Pirates",
    "Fallen Monk Pirates",
    "Animal Kingdom Pirates",
    "Big Mom Pirates",
    "Blackbeard Pirates",
    "Blackbeard Pirates Allies",
    "Whitebeard Pirates",
    "Former Whitebeard Pirates",
    "Whitebeard Pirates Allies",
    "Red-Haired Pirates",
    "Donquixote Pirates",
    "Buggy Pirates",
    "Buggy's Delivery",
    "Cross Guild",
    "Foxy Pirates",
    "Krieg Pirates",
    "Arlong Pirates",
    "Former Arlong Pirates",
    "Black Cat Pirates",
    "The Sun Pirates",
    "Sun Pirates",
    "Bellamy Pirates",
    "Roger Pirates",
    "Former Roger Pirates",
    "Rocks Pirates",
    "Former Rocks Pirates",
    "Beautiful Pirates",
    "Barto Club",
    "Barto Club Pirates",
    "Caribou Pirates",
    "Golden Lion Pirates",
    "New Giant Warrior Pirates",
    "New Giant Pirates",
    "New Giant Pirate Crew",
    "Giant Warrior Pirates",
    "Thriller Bark Pirates",
    "Rumbar Pirates",
    "Former Rumbar Pirates",
    "Spade Pirates",
    "Alvida Pirates",
    "Bluejam Pirates",
    "Gyro Pirates",
    "Flying Pirates",
    "Jellyfish Pirates",
    "Peachbeard Pirates",
    "Treasure Pirates",
    "Rolling Pirates",
    "Former Rolling Pirates",
    "Former Big Mom Pirates",
    "Brownbeard Pirates",
    "Wapol Pirates",
    "Space Pirates",
    "New Fish-Man Pirates",
    "Eldoraggo Crew",
    "Gasparde Pirates",
    "Trump Pirates",
    # Locations / regions
    "East Blue",
    "West Blue",
    "North Blue",
    "South Blue",
    "Grand Line",
    "Alabasta",
    "Alabasta Kingdom",
    "Drum Kingdom",
    "Sky Island",
    "Skypiea",
    "Water Seven",
    "Water 7",
    "Galley-La Company",
    "Thriller Bark",
    "Sabaody Archipelago",
    "Amazon Lily",
    "Marineford",
    "Fish-Man Island",
    "Punk Hazard",
    "Dressrosa",
    "Whole Cake Island",
    "Wano Country",
    "Land of Wano",
    "Egghead",
    "Elbaf",
    "Goa Kingdom",
    "Sorbet Kingdom",
    "Kingdom of Prodence",
    "Prodence Kingdom",
    "Germa Kingdom",
    "Kingdom of GERMA",
    "Windmill Village",
    "Frost Moon Village",
    "Jaya",
    "Ohara",
    "Long Ring Long Land",
    "Sniper Island",
    "Flevance",
    "Baterilla",
    "Bowin Island",
    "Lulucia Kingdom",
    "Foolshout Island",
    "Mogaro Kingdom",
    "The Moon",
    "Hot Springs Island",
    "Asuka Island",
    "Crown Island",
    "Mecha Island",
    "Omatsuri Island",
    "Shipbuilding Town",
    # Organizations / groups
    "GERMA 66",
    "Germa 66",
    "The Vinsmoke Family",
    "Vinsmoke Family",
    "SWORD",
    "SERAPHIM",
    "SSG",
    "G-5",
    "Mink Tribe",
    "Minks",
    "The Tontattas",
    "Tontatta Tribe",
    "Kuja Pirates",
    "Kuja",
    "SH Grand Fleet",
    "Straw Hat Grand Fleet",
    "Mountain Bandits",
    "Beasts Pirates",
    "Tobiroppo",
    "Numbers",
    "Flying Six",
    "Calamities",
    "Sweet Commander",
    "Three All-Stars",
    "Holy Knights",
    "Knights of God",
    "Mary Geoise",
    "Marijoa",
    "The Akazaya Nine",
    "Kouzuki Clan",
    "Kozuki Clan",
    "Kurozumi Clan",
    "The Franky Family",
    "Franky Family",
    "Donquixote Family",
    "Accino Family",
    "The Flying Fish Riders",
    "Five Elders",
    "Celestial Dragons",
    "World Nobles",
    "Shandian Warrior",
    "Vassals",
    "Homies",
    "Happosui Army",
    "Happo Navy",
    "Neo Navy",
    "Former Navy",
    "The Victims' Club",
    "Monkey Mountain Alliance",
    "The House of Lambs",
    "The Pirates Fest",
    # Races / species
    "Fish-Man",
    "Fishman",
    "Merfolk",
    "Giant",
    "Dwarf",
    "Lunarian",
    "Mink",
    "Long Arm Tribe",
    "Long Leg Tribe",
    "Three-Eye Tribe",
    "Snakeneck Tribe",
    "Kinokobito",
    "Neptunian",
    "Animal",
    "Sprite",
    "Biological Weapon",
    "Alchemi",
    # Misc
    "Vegapunk",
    "Scientist",
    "SMILE",
    "Wisemen",
    "ASL",
    "Film",
    "FILM",
    "Red",
    "Movie",
    "Bandit",
    "Jailer Beast",
    "Ninja",
    "Samurai",
    "Yakuza",
    "Corrida Colosseum",
    "Yonta Maria Fleet",
    "Ideo Pirates",
    "Leo Pirates",
    "Orlumbus Fleet",
    "NEO MARINES",
    "Neo Marines",
    "Thriller Bark Victim's Association",
    "World Pirates",
    "MADS",
    "ODYSSEY",
    "Music",
    "Muggy Kingdom",
    "King of the Pirates",
    "Plague",
    "Journalist",
    "Special",
    "Botanist",
    "Grantesoro",
    "Mugiwara Chase",
    "Monsters",
    "The Owner of Cindry's Shadow",
    "Weevil's Mother",
    "Evil Black",
    "Straw Hat Cre",
]


def seed_known_types(conn: sqlite3.Connection, types: list[str]) -> tuple[int, int]:
    """Insert known types idempotently. Returns (added, already_present)."""
    cur = conn.cursor()
    added = 0
    present = 0
    for t in types:
        cur.execute(
            "INSERT OR IGNORE INTO known_types (type_name) VALUES (?)", (t,)
        )
        if cur.rowcount == 1:
            added += 1
        else:
            present += 1
    conn.commit()
    return added, present


def list_known_types(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT type_name FROM known_types ORDER BY type_name")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the known_types table.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        added, present = seed_known_types(conn, SEED_TYPES)
        total = added + present
        print(f"Seeded known_types: {added} added, {present} already present, {total} total.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
