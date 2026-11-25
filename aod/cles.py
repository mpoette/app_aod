"""Clés d'accès à l'API, chacune associée au nom de son titulaire.

Format de la variable AOD_API_KEYS : entrées séparées par des virgules, chacune « nom:clé »
(ex. « michael:3f9a…,alice:7c21… »). Une entrée sans nom (« clé » seule, l'ancien format)
reste acceptée et est attribuée à SANS_NOM.
"""
from __future__ import annotations

import secrets

SANS_NOM = "sans_nom"


def lire_cles(texte: str) -> dict[str, str]:
    """« nom:clé,nom2:clé2 » → {clé: nom}. Les entrées vides sont ignorées."""
    cles = {}
    for entree in texte.split(","):
        nom, sep, cle = entree.strip().partition(":")
        if not sep:  # pas de « : » → ancien format, clé seule
            nom, cle = SANS_NOM, nom
        nom, cle = nom.strip() or SANS_NOM, cle.strip()
        if cle:
            cles[cle] = nom
    return cles


def titulaire(fournies: list[str | None], cles: dict[str, str]) -> str | None:
    """Nom du titulaire de la première clé fournie qui est valide, sinon None.

    Comparaison à temps constant (secrets.compare_digest) pour ne pas laisser deviner une clé
    caractère par caractère à partir du temps de réponse.
    """
    for fournie in fournies:
        if not fournie:
            continue
        for cle, nom in cles.items():
            if secrets.compare_digest(fournie.encode(), cle.encode()):
                return nom
    return None
