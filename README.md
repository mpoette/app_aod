# Vignettes AOD

Application [Streamlit](https://streamlit.io) pour explorer les vignettes cliniques d'une étude sur la
**gestion préopératoire des anticoagulants** (AOD, AVK, héparines), et les réponses recueillies via REDCap.

Pour chaque vignette, l'app affiche le texte du cas, la **réponse majoritaire** à chaque question et la
**dispersion** des réponses. Les cas peuvent être filtrés et triés selon leurs métadonnées.

## Fonctionnalités

- **Vignettes** : texte du cas et métadonnées (spécialité, sexe, ATC, délai, indication, fonction rénale, ALR) ;
  réponse majoritaire et taux d'accord par question ; barres de dispersion ; réponses individuelles.
- **Stratégie globale** : combinaison interruption + jour de dernière prise + relais, ou réversion en urgence immédiate.
- **Filtres** : métadonnées, rôle des répondants, nombre minimal de répondants, recherche dans le texte.
- **Tri** multicritère : métadonnées, accord, confiance, nombre de répondants.
- **Vue d'ensemble** : tableau récapitulatif exportable en CSV, carte de l'accord cas × question, accord selon une métadonnée.

## Lancer en local

Prérequis : [uv](https://docs.astral.sh/uv/), qui installe lui-même Python 3.13 et les dépendances.

```bash
uv run streamlit run app.py
```

Les données ne sont **pas** dans ce dépôt (elles contiennent les réponses des participants).
Placez-les dans `Data/` (organisation décrite dans [`Data/README.md`](Data/README.md)),
ou indiquez leur emplacement avec la variable `AOD_DATA_DIR` :

```bash
AOD_DATA_DIR="/chemin/vers/Data" uv run streamlit run app.py
```

Ajouter une dépendance : `uv add <paquet>` ; mettre à jour : `uv lock --upgrade && uv sync`.

## Organisation du code

| Fichier | Rôle |
|---|---|
| `app.py` | Interface Streamlit : filtres, tri, fiches des vignettes, graphiques |
| `data_loader.py` | Lecture des fichiers (XML REDCap, CSV, XLSX), décodage des réponses, calcul majorité / accord |
| `pyproject.toml`, `uv.lock`, `.python-version` | Projet uv : dépendances et versions figées |
| `.streamlit/config.toml` | Configuration Streamlit |
| `deploy/` | Fichiers et guide de déploiement sur un VPS (systemd, Nginx, HTTPS) |

## Méthode

- **Correspondance des cas** : chaque sous-dossier de `Data/` est rattaché à une spécialité de `Métadonnées.xlsx`
  (colonne `Xie`). Ses vignettes sont associées, dans l'ordre, à un bloc contigu de « Cas clinique N ». Si un projet
  REDCap couvre plusieurs spécialités (ex. viscérale + urologie), le bloc est étendu là où le sexe du patient
  (premier mot de la vignette) concorde avec les métadonnées.
- **Réponse majoritaire** : modalité la plus fréquente ; les égalités sont affichées.
- **Accord** : part des répondants ayant donné la réponse majoritaire (100 % = unanimité).
- **Accord moyen** : moyenne de l'accord sur les questions posées pour le cas (hors stratégie globale).
- Seuls les cas « Complete » dans REDCap sont comptés ; les questions conditionnelles (dernière prise, relais…)
  ne comptent que les répondants concernés.

## Déploiement

Voir [`deploy/README.md`](deploy/README.md).
