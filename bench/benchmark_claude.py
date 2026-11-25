# /// script
# requires-python = ">=3.12"
# dependencies = ["anthropic>=2.0"]
# ///
"""Benchmark d'un modèle Claude sur les vignettes cliniques AOD, via l'API de benchmark.

Le script crée un essai, récupère les vignettes une par une, les soumet à Claude, renvoie
chaque sortie à l'API (qui la corrige) puis affiche le bilan. Il fonctionne sur votre
ordinateur : c'est lui qui appelle Claude, l'API de benchmark ne voit que les réponses.

Lancer (uv installe les dépendances automatiquement) :

    export AOD_API_KEY="clé de l'API de benchmark"
    export ANTHROPIC_API_KEY="clé de l'API Anthropic"   # ou `ant auth login`
    uv run bench/benchmark_claude.py --modele claude-opus-5-5

Options utiles : --sans-preprompt, --effort high, --roles expert_clot expert_gihp,
--roles rcp (seulement les vignettes de la RCP thrombose, cas 101 et suivants),
--cas 1 5 6 (quelques cas seulement), --api http://127.0.0.1:8502 (API locale).

Pour un autre fournisseur de LLM : bench/benchmark_openai.py (format compatible OpenAI), ou remplacer
la fonction `interroger_claude` par une fonction qui prend le prompt et renvoie (texte, statut, meta).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

import anthropic

API_DEFAUT = "https://aod.scitoul.fr/api"


# --------------------------------------------------------------------------- appels à l'API de benchmark

class ApiBenchmark:
    def __init__(self, base: str, cle: str):
        self.base = base.rstrip("/")
        self.cle = cle

    def _requete(self, methode: str, chemin: str, corps: dict | None = None, params: dict | None = None):
        url = self.base + chemin
        if params:
            url += "?" + "&".join(
                f"{k}={v}" for k, vs in params.items() if vs is not None
                for v in (vs if isinstance(vs, list) else [vs])
            )
        data = json.dumps(corps).encode() if corps is not None else None
        req = urllib.request.Request(url, data=data, method=methode, headers={
            "Authorization": f"Bearer {self.cle}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            sys.exit(f"Erreur de l'API de benchmark ({e.code}) sur {chemin} : {e.read().decode()}")

    def get(self, chemin, **params):
        return self._requete("GET", chemin, params=params)

    def post(self, chemin, corps):
        return self._requete("POST", chemin, corps)


# --------------------------------------------------------------------------- appel à Claude

def interroger_claude(client: anthropic.Anthropic, prompt: str, modele: str, effort: str | None,
                      max_tokens: int) -> tuple[str | None, str, dict]:
    """Soumet le prompt à Claude. Renvoie (texte, statut, meta) ; statut ∈ ok | refus | erreur.

    Pas de repli automatique vers un autre modèle en cas de refus (paramètre `fallbacks`) :
    dans un benchmark, la réponse doit venir du modèle évalué. Un refus est enregistré
    comme tel et compte comme une stratégie non trouvée.
    """
    kwargs = {}
    if effort:
        kwargs["output_config"] = {"effort": effort}
    debut = time.monotonic()
    try:
        rep = client.messages.create(
            model=modele,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.NotFoundError, anthropic.BadRequestError) as e:
        # Problème de configuration (clé, modèle, paramètre) : il se répéterait sur tous les cas.
        sys.exit(f"Appel à Claude refusé (HTTP {e.status_code}) : {e.message}\n"
                 "Vérifiez ANTHROPIC_API_KEY, le nom du modèle et les options, puis relancez.")
    except anthropic.RateLimitError as e:
        return None, "erreur", {"erreur": f"limite de débit : {e.message}"}
    except anthropic.APIStatusError as e:
        return None, "erreur", {"erreur": f"HTTP {e.status_code} : {e.message}"}
    except anthropic.APIConnectionError as e:
        return None, "erreur", {"erreur": f"connexion : {e}"}

    texte = "".join(b.text for b in rep.content if b.type == "text")
    meta = {
        "modele_servi": rep.model,
        "stop_reason": rep.stop_reason,
        "tokens_entree": rep.usage.input_tokens,
        "tokens_sortie": rep.usage.output_tokens,
        "duree_s": round(time.monotonic() - debut, 2),
        "request_id": rep._request_id,
    }
    if rep.stop_reason == "refusal":
        if rep.stop_details:
            meta["refus_categorie"] = rep.stop_details.category
        return texte or None, "refus", meta
    return texte, "ok", meta


# --------------------------------------------------------------------------- programme

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=os.environ.get("AOD_API_URL", API_DEFAUT), help="URL de l'API de benchmark")
    p.add_argument("--modele", default="claude-opus-5-5")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None,
                   help="Effort de raisonnement (défaut : celui du modèle)")
    p.add_argument("--max-tokens", type=int, default=16000)
    p.add_argument("--sans-preprompt", action="store_true", help="Soumettre la vignette seule")
    p.add_argument("--roles", nargs="*", default=None,
                   help="Rôles retenus pour la référence (défaut : tous)")
    p.add_argument("--min-repondants", type=int, default=1)
    p.add_argument("--cas", nargs="*", type=int, default=None, help="Ne traiter que ces cas")
    p.add_argument("--notes", default="", help="Note libre enregistrée avec l'essai")
    args = p.parse_args()
    # Sous Windows (Git Bash, redirection vers un fichier), la sortie est en cp1252 : sans cela,
    # l'affichage de ✓ / ✗ planterait après le premier appel à Claude.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    cle = os.environ.get("AOD_API_KEY")
    if not cle:
        sys.exit("Définissez AOD_API_KEY (clé de l'API de benchmark).")
    api = ApiBenchmark(args.api, cle)
    client = anthropic.Anthropic()
    preprompt = not args.sans_preprompt

    run = api.post("/runs", {
        "modele": args.modele, "preprompt": preprompt, "roles": args.roles,
        "min_repondants": args.min_repondants,
        "parametres": {"effort": args.effort, "max_tokens": args.max_tokens,
                       "client": "bench/benchmark_claude.py", "notes": args.notes},
    })
    cas = args.cas or run["cas_evaluables"]
    print(f"Essai {run['id']} — {args.modele} — par {run.get('auteur') or '?'} — "
          f"données {run['version_donnees']} — {len(cas)} cas\n")

    for n in cas:
        v = api.get(f"/vignettes/{n}", preprompt=str(preprompt).lower())
        texte, statut, meta = interroger_claude(client, v["prompt"], args.modele, args.effort, args.max_tokens)
        g = api.post(f"/runs/{run['id']}/reponses/{n}", {"sortie": texte, "statut": statut, "meta": meta})
        s = g["score"]
        marque = "–" if statut == "erreur" else {"juste": "✓", "faux": "✗"}.get(g["strategie"]["statut"], "–")
        if statut == "erreur":
            detail = f"  [appel à Claude impossible : {meta.get('erreur')}]"
        else:
            detail = "" if g["format_valide"] else f"  [format : {'; '.join(g['erreurs'])}]"
        print(f"Cas {n:>3}  stratégie {marque}  {g['strategie']['reponse'] or '—':<28} "
              f"questions {s['questions_justes']}/{s['questions_evaluees']}  ({statut}){detail}")

    b = api.get(f"/runs/{run['id']}")["bilan"]
    taux = b["strategie"]["taux"]
    print(f"\nStratégie juste : {b['strategie']['justes']}/{b['cas_repondus']}"
          + (f" ({taux:.0%})" if taux is not None else ""))
    print(f"Format invalide : {b['format_invalide']} · refus : {b['refus']} · "
          f"erreurs d'appel (exclues, à relancer avec --cas) : {b['erreurs_api']}")
    for q in b["par_question"].values():
        if q["evaluees"]:
            print(f"  {q['libelle']:<22} {q['justes']}/{q['evaluees']} ({q['taux']:.0%})")
    if len(b.get("par_origine", {})) > 1:
        print("Stratégie juste par origine :")
        for nom, d in b["par_origine"].items():
            print(f"  {nom:<22} {d['justes']}/{d['repondus']} ({d['taux']:.0%})")
    print(f"\nDétail : GET {args.api}/runs/{run['id']}/reponses")


if __name__ == "__main__":
    main()
