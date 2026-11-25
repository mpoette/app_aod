#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.13.*"
# dependencies = []
# ///
"""Benchmark d'un LLM servi au format compatible OpenAI, via l'API de benchmark AOD.

Convient à tout serveur qui expose /v1/chat/completions : LiteLLM, vLLM, Ollama, llama.cpp,
LM Studio, OpenRouter, ou les API de Mistral, DeepSeek, OpenAI, Gemini… Le modèle est appelé
directement, une vignette par appel, sans historique ni outils : mêmes conditions que
bench/benchmark_claude.py, pour que les essais soient comparables.

Lancement avec uv : le bloc « /// script » ci-dessus déclare la version de Python et les
dépendances (aucune). uv trouve ou télécharge Python 3.13 et exécute le script sans créer de
dossier .venv ; le fichier peut être copié seul sur une machine.

    export AOD_API_KEY="clé de l'API de benchmark"
    export LLM_API_KEY="clé du serveur du modèle"        # facultatif si le serveur n'en demande pas
    uv run benchmark_openai.py --base-url http://localhost:11434/v1 --modele mon-modele

Sous Linux ou macOS, après `chmod +x benchmark_openai.py`, `./benchmark_openai.py …` suffit.
Sans uv, `python3 benchmark_openai.py …` fonctionne aussi avec Python 3.10 ou plus récent.

Options utiles : --nom (nom enregistré dans l'essai, si celui du serveur est un alias),
--temperature 0, --sans-preprompt, --roles rcp, --cas 1 5 6, --timeout 600 (modèle lent).

Réseau : si la machine passe par un proxy pour sortir sur Internet, définissez HTTPS_PROXY
pour joindre l'API AOD, et NO_PROXY=<nom du serveur du modèle> pour le joindre en direct.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

API_DEFAUT = "https://aod.scitoul.fr/api"
# Raisonnement affiché par certains modèles (Qwen3, DeepSeek-R1…) avant leur réponse : retiré avant
# correction, sinon les accolades qu'il contient peuvent gêner l'extraction du JSON.
RAISONNEMENT = re.compile(r"<think>.*?</think>", re.DOTALL)


def _contexte_ssl() -> ssl.SSLContext:
    """Vérification HTTPS habituelle (magasin de certificats du système), sans le mode strict.

    Depuis Python 3.13, le mode X.509 strict est actif par défaut. Il rejette les certificats
    fabriqués par les proxys d'inspection HTTPS des réseaux d'entreprise ou d'hôpital
    (« Missing Authority Key Identifier »), que les navigateurs acceptent. Seul ce mode est
    retiré : la chaîne de certificats et le nom du serveur restent vérifiés, comme en Python 3.12.
    """
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


SSL = _contexte_ssl()


def _json(url: str, cle: str | None, corps: dict | None = None, methode: str = "GET",
          timeout: float = 60) -> dict:
    """Requête HTTP JSON ; lève urllib.error.HTTPError / URLError / TimeoutError."""
    entetes = {"Content-Type": "application/json"}
    if cle:
        entetes["Authorization"] = f"Bearer {cle}"
    data = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(url, data=data, method=methode, headers=entetes)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL) as r:
        return json.loads(r.read())


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
        try:
            return _json(url, self.cle, corps, methode)
        except urllib.error.HTTPError as e:
            sys.exit(f"Erreur de l'API de benchmark ({e.code}) sur {chemin} : {e.read().decode()}")
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            sys.exit(f"API de benchmark injoignable ({self.base}) : {e}\n"
                     "Derrière un proxy, définissez HTTPS_PROXY.")

    def get(self, chemin, **params):
        return self._requete("GET", chemin, params=params)

    def post(self, chemin, corps):
        return self._requete("POST", chemin, corps)


# --------------------------------------------------------------------------- appel au modèle

def interroger(base_url: str, cle: str | None, prompt: str, modele: str, max_tokens: int,
               temperature: float | None, timeout: float) -> tuple[str | None, str, dict]:
    """Soumet le prompt au modèle. Renvoie (texte, statut, meta) ; statut ∈ ok | refus | erreur.

    Pas de nouvel essai ni de repli vers un autre modèle : la réponse doit venir du modèle évalué.
    """
    corps = {"model": modele, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}
    if temperature is not None:
        corps["temperature"] = temperature
    debut = time.monotonic()
    try:
        rep = _json(base_url.rstrip("/") + "/chat/completions", cle, corps, "POST", timeout)
    except urllib.error.HTTPError as e:
        message = e.read().decode(errors="replace")[:500]
        if e.code in (400, 401, 403, 404):
            # Problème de configuration (clé, nom du modèle, paramètre) : il se répéterait sur tous les cas.
            sys.exit(f"Appel au modèle refusé (HTTP {e.code}) : {message}\n"
                     "Vérifiez --base-url, --modele, LLM_API_KEY et les options, puis relancez.")
        return None, "erreur", {"erreur": f"HTTP {e.code} : {message}"}
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        return None, "erreur", {"erreur": f"connexion : {e}"}

    choix = (rep.get("choices") or [{}])[0]
    message = choix.get("message") or {}
    brut = message.get("content") or ""
    texte = RAISONNEMENT.sub("", brut).strip()
    usage = rep.get("usage") or {}
    meta = {
        "modele_servi": rep.get("model"),
        "finish_reason": choix.get("finish_reason"),
        "tokens_entree": usage.get("prompt_tokens"),
        "tokens_sortie": usage.get("completion_tokens"),
        "duree_s": round(time.monotonic() - debut, 2),
        "raisonnement_retire": texte != brut.strip(),
    }
    if message.get("refusal") or choix.get("finish_reason") == "content_filter":
        meta["refus"] = message.get("refusal")
        return texte or None, "refus", meta
    return texte, "ok", meta


# --------------------------------------------------------------------------- programme

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=os.environ.get("AOD_API_URL", API_DEFAUT), help="URL de l'API de benchmark")
    p.add_argument("--base-url", required=True, help="URL du serveur du modèle, jusqu'à /v1 inclus")
    p.add_argument("--modele", required=True, help="Nom du modèle pour le serveur")
    p.add_argument("--nom", default=None, help="Nom enregistré dans l'essai (défaut : --modele)")
    p.add_argument("--max-tokens", type=int, default=16000)
    p.add_argument("--temperature", type=float, default=None, help="Défaut : celle du serveur")
    p.add_argument("--timeout", type=float, default=300, help="Délai maximal d'un appel au modèle (s)")
    p.add_argument("--sans-preprompt", action="store_true", help="Soumettre la vignette seule")
    p.add_argument("--roles", nargs="*", default=None, help="Rôles retenus pour la référence (défaut : tous)")
    p.add_argument("--min-repondants", type=int, default=1)
    p.add_argument("--cas", nargs="*", type=int, default=None, help="Ne traiter que ces cas")
    p.add_argument("--notes", default="", help="Note libre enregistrée avec l'essai")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    cle = os.environ.get("AOD_API_KEY")
    if not cle:
        sys.exit("Définissez AOD_API_KEY (clé de l'API de benchmark).")
    cle_llm = os.environ.get("LLM_API_KEY")
    api = ApiBenchmark(args.api, cle)
    preprompt = not args.sans_preprompt
    nom = args.nom or args.modele

    cas = args.cas or [c["cas"] for c in api.get(
        "/vignettes", evaluables="true", roles=args.roles, min_repondants=args.min_repondants)["vignettes"]]
    run = None
    for n in cas:
        v = api.get(f"/vignettes/{n}", preprompt=str(preprompt).lower())
        texte, statut, meta = interroger(args.base_url, cle_llm, v["prompt"], args.modele, args.max_tokens,
                                         args.temperature, args.timeout)
        if run is None:
            # créé après le premier appel au modèle : une erreur de configuration (adresse, nom du
            # modèle, clé) arrête le script sans laisser d'essai vide dans la base
            run = api.post("/runs", {
                "modele": nom, "preprompt": preprompt, "roles": args.roles,
                "min_repondants": args.min_repondants,
                "parametres": {"modele_serveur": args.modele, "max_tokens": args.max_tokens,
                               "temperature": args.temperature, "client": "bench/benchmark_openai.py",
                               "notes": args.notes},
            })
            print(f"Essai {run['id']} — {nom} — par {run.get('auteur') or '?'} — "
                  f"données {run['version_donnees']} — {len(cas)} cas\n")
        g = api.post(f"/runs/{run['id']}/reponses/{n}", {"sortie": texte, "statut": statut, "meta": meta})
        s = g["score"]
        marque = "–" if statut == "erreur" else {"juste": "✓", "faux": "✗"}.get(g["strategie"]["statut"], "–")
        if statut == "erreur":
            detail = f"  [appel au modèle impossible : {meta.get('erreur')}]"
        else:
            detail = "" if g["format_valide"] else f"  [format : {'; '.join(g['erreurs'])}]"
        print(f"Cas {n:>3}  stratégie {marque}  {g['strategie']['reponse'] or '—':<28} "
              f"questions {s['questions_justes']}/{s['questions_evaluees']}  ({statut}){detail}")

    if run is None:
        sys.exit("Aucun cas à traiter.")
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
        for nom_o, d in b["par_origine"].items():
            print(f"  {nom_o:<22} {d['justes']}/{d['repondus']} ({d['taux']:.0%})")
    print(f"\nDétail : GET {args.api}/runs/{run['id']}/reponses")


if __name__ == "__main__":
    main()
