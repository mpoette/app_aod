"""Stockage des essais de benchmark (SQLite, un fichier).

Un « run » (essai) = un modèle évalué avec des paramètres donnés. La référence (modalités
majoritaires) est figée au moment de la création de l'essai : si de nouvelles réponses
humaines arrivent ensuite, les résultats de l'essai restent comparables et reproductibles.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    cree_le         TEXT NOT NULL,
    modele          TEXT NOT NULL,
    preprompt       INTEGER NOT NULL,
    roles           TEXT,              -- JSON : liste de codes de rôle, ou null = tous
    min_repondants  INTEGER NOT NULL,
    version_donnees TEXT NOT NULL,     -- empreinte du dossier Data/
    parametres      TEXT NOT NULL,     -- JSON libre (effort, max_tokens, notes…)
    reference       TEXT NOT NULL,     -- JSON : référence figée par cas
    auteur          TEXT               -- titulaire de la clé qui a créé l'essai
);
CREATE TABLE IF NOT EXISTS reponses (
    run_id      TEXT NOT NULL REFERENCES runs(id),
    cas         INTEGER NOT NULL,
    soumis_le   TEXT NOT NULL,
    statut      TEXT NOT NULL,         -- ok | refus | erreur
    sortie      TEXT,                  -- texte brut renvoyé par le LLM
    meta        TEXT NOT NULL,         -- JSON libre (tokens, durée, modèle servi…)
    grille      TEXT NOT NULL,         -- JSON : grille de correction
    soumis_par  TEXT,                  -- titulaire de la clé qui a soumis la réponse
    PRIMARY KEY (run_id, cas)
);
"""

# Colonnes ajoutées après la création des premières bases : ajoutées au besoin à l'ouverture.
# Les essais antérieurs gardent la valeur NULL (auteur inconnu).
MIGRATIONS = {"runs": {"auteur": "TEXT"}, "reponses": {"soumis_par": "TEXT"}}


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)
            for table, colonnes in MIGRATIONS.items():
                existantes = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                for col, typ in colonnes.items():
                    if col not in existantes:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    # ------------------------------------------------------------------ essais
    def creer_run(self, modele: str, preprompt: bool, roles: list[str] | None, min_repondants: int,
                  version_donnees: str, parametres: dict, reference: dict,
                  auteur: str | None = None) -> dict:
        run_id = uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs (id, cree_le, modele, preprompt, roles, min_repondants,"
                " version_donnees, parametres, reference, auteur) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, _maintenant(), modele, int(preprompt), json.dumps(roles), min_repondants,
                 version_donnees, json.dumps(parametres, ensure_ascii=False),
                 json.dumps(reference, ensure_ascii=False), auteur),
            )
        return self.run(run_id)

    def run(self, run_id: str, avec_reference: bool = True) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if r is None:
            return None
        d = {
            "id": r["id"], "cree_le": r["cree_le"], "auteur": r["auteur"], "modele": r["modele"],
            "preprompt": bool(r["preprompt"]), "roles": json.loads(r["roles"]),
            "min_repondants": r["min_repondants"], "version_donnees": r["version_donnees"],
            "parametres": json.loads(r["parametres"]),
        }
        if avec_reference:
            d["reference"] = json.loads(r["reference"])
        return d

    def runs(self) -> list[dict]:
        with self._conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM runs ORDER BY cree_le DESC")]
        return [self.run(i, avec_reference=False) for i in ids]

    # ------------------------------------------------------------------ réponses
    def enregistrer_reponse(self, run_id: str, cas: int, statut: str, sortie: str | None,
                            meta: dict, grille: dict, soumis_par: str | None = None) -> None:
        """Une seule réponse par cas et par essai : une nouvelle soumission remplace la précédente."""
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO reponses (run_id, cas, soumis_le, statut, sortie, meta, grille,"
                " soumis_par) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, cas, _maintenant(), statut, sortie,
                 json.dumps(meta, ensure_ascii=False), json.dumps(grille, ensure_ascii=False), soumis_par),
            )

    def reponses(self, run_id: str | None = None) -> list[dict]:
        """Réponses d'un essai, ou de tous les essais si run_id est None (avec la clé run_id)."""
        with self._conn() as c:
            if run_id is None:
                rows = c.execute("SELECT * FROM reponses ORDER BY run_id, cas").fetchall()
            else:
                rows = c.execute("SELECT * FROM reponses WHERE run_id = ? ORDER BY cas", (run_id,)).fetchall()
        return [{
            **({"run_id": r["run_id"]} if run_id is None else {}),
            "cas": r["cas"], "soumis_le": r["soumis_le"], "soumis_par": r["soumis_par"],
            "statut": r["statut"], "sortie": r["sortie"],
            "meta": json.loads(r["meta"]), "grille": json.loads(r["grille"]),
        } for r in rows]
