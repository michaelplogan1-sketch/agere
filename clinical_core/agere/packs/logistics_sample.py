"""Synthetic logistics warehouse for the `logistics` pack (shipments on carrier lanes). Deterministic from the seed."""
from datetime import date, timedelta
from pathlib import Path
import random
import sqlite3


def build(path, n=3000, seed=11):
    path = Path(path); path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / 'semantic.sqlite3')
    db.execute('DROP TABLE IF EXISTS shipment')
    db.execute('CREATE TABLE shipment(shipment_id TEXT PRIMARY KEY, carrier TEXT, lane TEXT, mode TEXT, promised_date TEXT, delivered_date TEXT, '
               'weight_kg REAL, damaged INTEGER, cost_usd REAL, late_days INTEGER)')
    rnd = random.Random(seed)
    carriers = {'Atlas Freight': .08, 'Brightline': .18, 'Cobalt Rail': .30}  # late probability by carrier
    lanes = ['CHI-DAL', 'LAX-SEA', 'ATL-MIA', 'NYC-BOS', 'DEN-PHX']
    as_of = date(2030, 5, 27)
    for i in range(n):
        c = rnd.choice(list(carriers))
        mode = 'rail' if c == 'Cobalt Rail' else rnd.choice(['truck', 'truck', 'air'])
        promised = as_of - timedelta(days=rnd.randrange(0, 120))
        late = rnd.choice([1, 2, 3, 5, 8]) if rnd.random() < carriers[c] else 0
        delivered = promised + timedelta(days=late) if promised + timedelta(days=late) <= as_of and rnd.random() > .02 else None
        db.execute('INSERT INTO shipment VALUES(?,?,?,?,?,?,?,?,?,?)', (f'S{i:05d}', c, rnd.choice(lanes), mode, promised.isoformat(), delivered.isoformat() if delivered else None,
                                                                         round(rnd.uniform(5, 900), 1), int(rnd.random() < (.02 if mode == 'truck' else .05)),
                                                                         round(rnd.uniform(80, 4000), 2), late if delivered else None))
    db.commit(); db.close()
    return path
