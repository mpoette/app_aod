"""API de benchmark des LLM sur les vignettes cliniques AOD.

Lancer en local :  AOD_API_KEYS=moi:dev uv run uvicorn api:app --port 8502
Documentation interactive : http://127.0.0.1:8502/docs

Variables d'environnement :
- AOD_API_KEYS   : clés d'accès « nom:clé », séparées par des virgules (obligatoire) ;
                   le nom du titulaire est enregistré avec chaque essai et chaque réponse
- AOD_DATA_DIR   : dossier des données (par défaut ./Data, sinon ../Data)
- AOD_STATE_DIR  : dossier où est stockée la base des essais (par défaut ./state)
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from aod.cles import lire_cles, titulaire
from aod.data import META_COLUMNS, build_dataset, data_fingerprint, load_preprompt
from aod.scoring import bilan, construire_reference, noter
from aod.store import Store

ICI = Path(__file__).resolve().parent
ROLES = ["interne_ar", "mar_vasculaire", "mar_autre", "expert_clot", "expert_gihp"]


def _dossier_donnees() -> Path:
    if os.environ.get("AOD_DATA_DIR"):
        return Path(os.environ["AOD_DATA_DIR"]).expanduser()
    for cand in (ICI / "Data", ICI.parent / "Data"):
        if any(cand.glob("*.xlsx")):
            return cand
    return ICI / "Data"


DATA_DIR = _dossier_donnees()
STORE = Store(Path(os.environ.get("AOD_STATE_DIR", ICI / "state")) / "benchmark.sqlite")


# --------------------------------------------------------------------------- données (rechargées si modifiées)

class _Donnees:
    """Garde le jeu de données en mémoire et le relit quand les fichiers de Data/ changent."""

    def __init__(self):
        self._verrou = threading.Lock()
        self._mtime = None
        self.ds = None

    def get(self):
        mtime = max((p.stat().st_mtime for p in DATA_DIR.rglob("*") if p.is_file()), default=0)
        with self._verrou:
            if self.ds is None or mtime != self._mtime:
                self.ds = build_dataset(DATA_DIR)
                self.version = data_fingerprint(DATA_DIR)
                self.preprompt = load_preprompt(DATA_DIR)
                self._mtime = mtime
            return self


DONNEES = _Donnees()


# --------------------------------------------------------------------------- authentification

_bearer = HTTPBearer(auto_error=False)
_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verifier_cle(bearer: HTTPAuthorizationCredentials | None = Security(_bearer),
                 cle: str | None = Security(_header)) -> str:
    """Vérifie la clé (en-tête Bearer ou X-API-Key) et renvoie le nom de son titulaire."""
    autorisees = lire_cles(os.environ.get("AOD_API_KEYS", ""))
    if not autorisees:
        raise HTTPException(503, "API non configurée : aucune clé définie (AOD_API_KEYS).")
    # Les deux en-têtes sont essayés : une valeur erronée dans l'un n'empêche pas l'autre de valider.
    nom = titulaire([bearer.credentials if bearer else None, cle], autorisees)
    if nom is None:
        raise HTTPException(401, "Clé d'API manquante ou invalide.",
                            headers={"WWW-Authenticate": "Bearer"})
    return nom


# --------------------------------------------------------------------------- application

app = FastAPI(
    title="Benchmark LLM — vignettes AOD",
    description=(
        "Distribue les vignettes cliniques à un LLM et corrige ses réponses par rapport à la "
        "réponse majoritaire des répondants humains.\n\n"
        "Déroulé : `POST /runs` (créer un essai) → pour chaque cas `GET /vignettes/{cas}` "
        "(prompt à soumettre au LLM) → `POST /runs/{run_id}/reponses/{cas}` (sortie du LLM, "
        "renvoie la grille de correction) → `GET /runs/{run_id}` (bilan).\n\n"
        "Authentification : en-tête `Authorization: Bearer <clé>` ou `X-API-Key: <clé>`."
    ),
    version="0.1.0",
)
protege = [Depends(verifier_cle)]


def _cas_ou_404(d, cas: int):
    c = d.ds.cases[d.ds.cases["case_id"] == cas]
    if c.empty:
        raise HTTPException(404, f"Cas {cas} inconnu.")
    return c.iloc[0]


def _verifier_roles(roles: list[str] | None) -> list[str] | None:
    if not roles:
        return None
    inconnus = sorted(set(roles) - set(ROLES))
    if inconnus:
        raise HTTPException(422, f"Rôles inconnus : {inconnus}. Rôles possibles : {ROLES}")
    return sorted(set(roles))


@app.get("/health", tags=["Divers"], summary="État du service (sans authentification)")
def health():
    return {"statut": "ok"}


@app.get("/vignettes", tags=["Vignettes"], dependencies=protege,
         summary="Liste des cas et de leurs métadonnées")
def lister_vignettes(
    specialite: str | None = None,
    evaluables: bool = Query(False, description="Ne garder que les cas qui ont une référence humaine"),
    roles: list[str] | None = Query(None, description=f"Rôles pris en compte pour la référence : {ROLES}"),
    min_repondants: int = Query(1, ge=1),
):
    d = DONNEES.get()
    ref = construire_reference(d.ds.cases, d.ds.answers, _verifier_roles(roles), min_repondants)
    out = []
    for _, c in d.ds.cases.iterrows():
        r = ref[str(int(c["case_id"]))]
        if specialite and c["Spécialité"].lower() != specialite.lower():
            continue
        if evaluables and not r["evaluable"]:
            continue
        out.append({"cas": int(c["case_id"]), **{m: c[m] for m in META_COLUMNS},
                    "urgence_immediate": bool(c["Urgence immédiate"]),
                    "n_repondants": r["n_repondants"], "evaluable": r["evaluable"]})
    return {"version_donnees": d.version, "n": len(out), "vignettes": out}


@app.get("/vignettes/{cas}", tags=["Vignettes"], dependencies=protege,
         summary="Prompt d'un cas, avec ou sans préprompt")
def lire_vignette(cas: int, preprompt: bool = Query(True, description="Faire précéder la vignette du préprompt")):
    d = DONNEES.get()
    c = _cas_ou_404(d, cas)
    if not c["texte_prompt"]:
        raise HTTPException(404, f"Le texte de la vignette du cas {cas} n'est pas disponible.")
    if preprompt and not d.preprompt:
        raise HTTPException(404, "Préprompt introuvable (Data/preprompt.txt).")
    prompt = f"{d.preprompt}\n{c['texte_prompt']}" if preprompt else c["texte_prompt"]
    return {"cas": cas, "preprompt": preprompt, "prompt": prompt, "vignette": c["texte_prompt"],
            "urgence_immediate": bool(c["Urgence immédiate"]),
            "metadonnees": {m: c[m] for m in META_COLUMNS}}


class NouveauRun(BaseModel):
    modele: str = Field(..., examples=["claude-opus-5-5"], description="Nom du modèle évalué")
    preprompt: bool = Field(True, description="Les prompts soumis incluent-ils le préprompt ?")
    roles: list[str] | None = Field(None, description=f"Rôles retenus pour la référence ({ROLES}) ; null = tous")
    min_repondants: int = Field(1, ge=1, description="Nombre minimal de répondants pour qu'un cas soit évaluable")
    parametres: dict[str, Any] = Field(default_factory=dict, description="Libre : effort, max_tokens, notes…")


@app.post("/runs", tags=["Essais"], dependencies=protege, status_code=201,
          summary="Créer un essai (la référence est figée à ce moment)")
def creer_run(body: NouveauRun, auteur: str = Depends(verifier_cle)):
    d = DONNEES.get()
    roles = _verifier_roles(body.roles)
    ref = construire_reference(d.ds.cases, d.ds.answers, roles, body.min_repondants)
    run = STORE.creer_run(body.modele, body.preprompt, roles, body.min_repondants,
                          d.version, body.parametres, ref, auteur=auteur)
    run.pop("reference")
    run["cas_evaluables"] = sorted(int(k) for k, v in ref.items() if v["evaluable"])
    return run


@app.get("/runs", tags=["Essais"], dependencies=protege, summary="Liste des essais")
def lister_runs():
    return STORE.runs()


def _run_ou_404(run_id: str) -> dict:
    run = STORE.run(run_id)
    if run is None:
        raise HTTPException(404, f"Essai {run_id} inconnu.")
    return run


@app.get("/runs/{run_id}", tags=["Essais"], dependencies=protege, summary="Bilan d'un essai")
def lire_run(run_id: str):
    run = _run_ou_404(run_id)
    ref = run.pop("reference")
    reps = STORE.reponses(run_id)
    run["bilan"] = bilan(reps, ref, DONNEES.get().ds.cases)
    run["resultats"] = [{
        "cas": r["cas"], "statut": r["statut"],
        "strategie": r["grille"]["strategie"]["statut"],
        "format_valide": r["grille"]["format_valide"],
        "questions_justes": r["grille"]["score"]["questions_justes"],
        "questions_evaluees": r["grille"]["score"]["questions_evaluees"],
    } for r in reps]
    return run


@app.get("/runs/{run_id}/reponses", tags=["Essais"], dependencies=protege,
         summary="Toutes les réponses d'un essai, avec sorties brutes et grilles")
def lister_reponses(run_id: str):
    _run_ou_404(run_id)
    return STORE.reponses(run_id)


class Soumission(BaseModel):
    sortie: str | None = Field(None, description="Texte brut renvoyé par le LLM (le JSON y est extrait)")
    reponse: dict[str, Any] | None = Field(None, description="Ou : l'objet JSON déjà décodé")
    statut: Literal["ok", "refus", "erreur"] = Field("ok", description="refus = le LLM a décliné ; "
                                                     "erreur = l'appel au LLM a échoué")
    meta: dict[str, Any] = Field(default_factory=dict, description="Libre : tokens, durée, modèle servi…")


@app.post("/runs/{run_id}/reponses/{cas}", tags=["Essais"], dependencies=protege,
          summary="Soumettre la réponse du LLM pour un cas et recevoir la grille de correction")
def soumettre(run_id: str, cas: int, body: Soumission, auteur: str = Depends(verifier_cle)):
    run = _run_ou_404(run_id)
    ref_cas = run["reference"].get(str(cas))
    if ref_cas is None:
        raise HTTPException(404, f"Cas {cas} absent de la référence de cet essai.")
    if body.statut == "ok" and body.sortie is None and body.reponse is None:
        raise HTTPException(422, "Fournir `sortie` (texte brut) ou `reponse` (objet JSON).")
    if body.statut == "ok":
        grille = noter(ref_cas, sortie=body.sortie, reponse=body.reponse)
    else:
        grille = noter(ref_cas, sortie="")
        grille["erreurs"] = [f"pas de réponse exploitable (statut : {body.statut})"]
    STORE.enregistrer_reponse(run_id, cas, body.statut, body.sortie, body.meta, grille, soumis_par=auteur)
    return {"run_id": run_id, "cas": cas, "statut": body.statut, "soumis_par": auteur, **grille}
