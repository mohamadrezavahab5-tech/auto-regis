"""Read an NBO / CRM export (xlsx or csv) into normalised dict rows, using column positions from config/columns.json."""
import json
from pathlib import Path

import pandas as pd

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def load_columns(source: str, path=None) -> dict:
    with open(path or CONFIG_DIR / "columns.json", encoding="utf-8") as f:
        return json.load(f)[source]


def read_export(file, source: str, columns: dict = None):
    """-> list of {'smr','status','site', ...} ; header row skipped; blank rows dropped. Fails loudly on a too-narrow file."""
    columns = columns or load_columns(source)
    p = Path(file)
    df = pd.read_csv(p, dtype=str, header=None, keep_default_na=False) if p.suffix.lower() == ".csv" else pd.read_excel(p, dtype=str, header=None).fillna("")
    need = max(col_index(v) for v in columns.values()) + 1
    if df.shape[1] < need:
        raise ValueError(f"{p.name}: has {df.shape[1]} columns but the configured positions need {need}; the export layout changed")
    rows = []
    for _, r in df.iloc[1:].iterrows():
        row = {name: str(r.iloc[col_index(letter)]).strip() for name, letter in columns.items()}
        if row.get("smr"):
            rows.append(row)
    return rows


def approved_rows(rows, statuses):
    wanted = {s.strip() for s in statuses}
    return [r for r in rows if r.get("status", "").strip() in wanted]
