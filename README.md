# OPTCG Manager

Personal-use desktop app for managing a One Piece TCG collection and decks.

See [DESIGN.md](DESIGN.md) for the full design and [PHASE_0.md](PHASE_0.md) for the current phase plan.

## Phase 0 — Setup & Data Foundation

Goal: a local SQLite database populated with all OPTCG card data from OptcgAPI.

### Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Usage

```bash
# Create the database schema
python -m src.db_setup
python -m src.db_setup --reset    # drop and recreate

# Seed the known_types table
python -m src.known_types

# Sync all cards from OptcgAPI into data/optcg.db
python -m src.sync
python -m src.sync --use-cache    # cache raw API responses locally
```

### Project layout

```
optcg-manager/
├── DESIGN.md
├── PHASE_0.md
├── README.md
├── requirements.txt
├── data/                  # data/optcg.db lives here
└── src/
    ├── db_setup.py
    ├── api_client.py
    ├── sanitize.py
    ├── known_types.py
    └── sync.py
```
