"""Lab notebook: persistent memory of every experiment (Story 3.2).

SQLite via stdlib sqlite3 (WAL, single writer). Every cycle, candidate, decision,
intervention, and outcome is a recorded row — the governor's recall, the UI
timeline, and the phylogeny are all views over these tables.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, seed INTEGER, config TEXT,
    created_at REAL, ended_at REAL, notes TEXT
);
CREATE TABLE IF NOT EXISTS interventions (
    run_id TEXT, epoch INTEGER, tick INTEGER, kind TEXT,
    plugin_name TEXT, details TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS cycles (
    id TEXT PRIMARY KEY, run_id TEXT, epoch INTEGER, tick INTEGER,
    report TEXT, decision TEXT, provider TEXT,
    tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
    started_at REAL, finished_at REAL
);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY, cycle_id TEXT, label TEXT, source TEXT, meta TEXT,
    validation TEXT, shadow_metrics TEXT, fitness_breakdown TEXT,
    fitness REAL, fate TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
    cycle_id TEXT PRIMARY KEY, plugin_name TEXT,
    expected TEXT, measured TEXT, verdict TEXT
);
CREATE TABLE IF NOT EXISTS lineage (
    parent_plugin TEXT, child_candidate_id TEXT, kind TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    run_id TEXT, epoch INTEGER, tick INTEGER, series TEXT, value REAL
);
CREATE INDEX IF NOT EXISTS metrics_series ON metrics(run_id, series, epoch, tick);
CREATE INDEX IF NOT EXISTS cycles_run ON cycles(run_id, tick);
"""


class Notebook:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.run_id: str | None = None

    def close(self) -> None:
        self.db.close()

    # -- run lifecycle -----------------------------------------------------------

    def start_run(self, seed: int, config_json: str, notes: str = "") -> str:
        self.run_id = uuid.uuid4().hex[:12]
        self.db.execute(
            "INSERT INTO runs VALUES (?,?,?,?,NULL,?)",
            (self.run_id, seed, config_json, time.time(), notes),
        )
        self.db.commit()
        return self.run_id

    def resume_latest_run(self) -> str | None:
        row = self.db.execute(
            "SELECT id FROM runs WHERE ended_at IS NULL ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        self.run_id = row["id"] if row else None
        return self.run_id

    # -- writes --------------------------------------------------------------------

    def record_intervention(self, epoch: int, tick: int, kind: str,
                            plugin_name: str = "", details: dict | None = None) -> None:
        self.db.execute(
            "INSERT INTO interventions VALUES (?,?,?,?,?,?,?)",
            (self.run_id, epoch, tick, kind, plugin_name,
             json.dumps(details or {}), time.time()),
        )
        self.db.commit()

    def start_cycle(self, epoch: int, tick: int, report: dict, provider: str) -> str:
        cycle_id = uuid.uuid4().hex[:12]
        self.db.execute(
            "INSERT INTO cycles VALUES (?,?,?,?,?,?,?,0,0,?,NULL)",
            (cycle_id, self.run_id, epoch, tick, json.dumps(report),
             "in_progress", provider, time.time()),
        )
        self.db.commit()
        return cycle_id

    def finish_cycle(self, cycle_id: str, decision: str,
                     tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.db.execute(
            "UPDATE cycles SET decision=?, tokens_in=?, tokens_out=?, finished_at=? WHERE id=?",
            (decision, tokens_in, tokens_out, time.time(), cycle_id),
        )
        self.db.commit()

    def record_candidate(self, cycle_id: str, label: str, source: str, meta: dict,
                         validation: dict, shadow_metrics: dict,
                         fitness_breakdown: dict, fitness: float, fate: str) -> str:
        cand_id = uuid.uuid4().hex[:12]
        self.db.execute(
            "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cand_id, cycle_id, label, source, json.dumps(meta), json.dumps(validation),
             json.dumps(shadow_metrics), json.dumps(fitness_breakdown), fitness, fate),
        )
        parent = meta.get("lineage_parent")
        if parent:
            self.db.execute("INSERT INTO lineage VALUES (?,?,?)", (parent, cand_id, "mutation"))
        self.db.commit()
        return cand_id

    def record_outcome(self, cycle_id: str, plugin_name: str, expected: str,
                       measured: dict, verdict: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?)",
            (cycle_id, plugin_name, expected, json.dumps(measured), verdict),
        )
        self.db.commit()

    def record_metrics(self, epoch: int, tick: int, series_values: dict[str, float]) -> None:
        self.db.executemany(
            "INSERT INTO metrics VALUES (?,?,?,?,?)",
            [(self.run_id, epoch, tick, s, v) for s, v in series_values.items()],
        )
        self.db.commit()

    # -- recall (Story 3.2 AC2: recency + species overlap + outcome tags) -----------

    def recall(self, current_species: list[str], limit: int = 6) -> list[dict]:
        """Most relevant prior experiments: recent first, species-overlap boosted."""
        rows = self.db.execute(
            """SELECT c.id, c.label, c.meta, c.fitness, c.fate, c.fitness_breakdown,
                      cy.tick, cy.decision, o.verdict, o.expected, o.measured
               FROM candidates c
               JOIN cycles cy ON cy.id = c.cycle_id AND cy.run_id = ?
               LEFT JOIN outcomes o ON o.cycle_id = c.cycle_id
               ORDER BY cy.started_at DESC LIMIT 60""",
            (self.run_id,),
        ).fetchall()
        current = set(current_species)
        scored = []
        for i, row in enumerate(rows):
            meta = json.loads(row["meta"] or "{}")
            overlap = len(current & set(meta.get("species", [])))
            recency = 1.0 / (1 + i)
            has_outcome = 1.0 if row["verdict"] else 0.0
            scored.append((overlap * 2.0 + recency + has_outcome, row, meta))
        scored.sort(key=lambda t: -t[0])
        out = []
        for _score, row, meta in scored[:limit]:
            out.append({
                "candidate": row["label"],
                "species": meta.get("species", []),
                "hypothesis": meta.get("hypothesis", ""),
                "fate": row["fate"],
                "fitness": row["fitness"],
                "tick": row["tick"],
                "outcome_verdict": row["verdict"],
                "outcome_expected": row["expected"],
            })
        return out

    # -- reads for the API / UI -------------------------------------------------------

    def cycles(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            """SELECT id, epoch, tick, decision, provider, tokens_in, tokens_out,
                      started_at, finished_at FROM cycles
               WHERE run_id=? ORDER BY started_at DESC LIMIT ?""",
            (self.run_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def candidates_for(self, cycle_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM candidates WHERE cycle_id=?", (cycle_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("meta", "validation", "shadow_metrics", "fitness_breakdown"):
                d[k] = json.loads(d[k] or "{}")
            out.append(d)
        return out

    def all_candidates(self) -> list[dict]:
        """Every candidate this run ever produced — the code lab's plugin browser."""
        rows = self.db.execute(
            """SELECT c.* FROM candidates c
               JOIN cycles cy ON cy.id = c.cycle_id
               WHERE cy.run_id = ? ORDER BY c.rowid""",
            (self.run_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("meta", "validation", "shadow_metrics", "fitness_breakdown"):
                d[k] = json.loads(d[k] or "{}")
            out.append(d)
        return out

    def interventions(self, limit: int = 200) -> list[dict]:
        """Promotion / rollback / control-failure events — timeline chart markers."""
        rows = self.db.execute(
            """SELECT epoch, tick, kind, plugin_name, details, created_at
               FROM interventions WHERE run_id = ?
               ORDER BY created_at LIMIT ?""",
            (self.run_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d["details"] or "{}")
            out.append(d)
        return out
