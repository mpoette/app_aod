# Vignettes AOD

Application [Streamlit](https://streamlit.io) pour explorer les vignettes cliniques d'une étude sur la
**gestion préopératoire des anticoagulants** (AOD, AVK, héparines), et les réponses recueillies via REDCap.

Le dépôt contient deux outils qui partagent le même noyau (`aod/`) :

- **l'explorateur** (`app.py`, Streamlit) : pour chaque vignette, le texte du cas, la **réponse majoritaire**
  à chaque question et la **dispersion** des réponses ; filtres et tri selon les métadonnées ;
- **l'API de benchmark** (`api.py`, FastAPI) : distribue les vignettes à un LLM et corrige ses réponses par
  rapport à la réponse majoritaire des humains (voir [Benchmark des LLM](#benchmark-des-llm)).

## Fonctionnalités

- **Vignettes** : texte du cas et métadonnées (spécialité, sexe, ATC, délai, indication, fonction rénale, ALR,
  origine) ;
  réponse majoritaire et taux d'accord par question ; barres de dispersion ; réponses individuelles.
- **Stratégie globale** : combinaison interruption + jour de dernière prise + relais, ou réversion en urgence immédiate.
- **Filtres** : métadonnées, rôle des répondants, nombre minimal de répondants, recherche dans le texte.
- **Tri** multicritère : métadonnées, accord, confiance, nombre de répondants.
- **Vue d'ensemble** : tableau récapitulatif exportable en CSV, carte de l'accord cas × question, accord selon une métadonnée.
- **Résultats LLM** : essais soumis par l'API de benchmark, lus dans `state/benchmark.sqlite`. Tableau de
  tous les essais (marque, modèle, date, utilisateur, scores…), filtrable, chaque essai pouvant être décoché ;
  pour la sélection : stratégie juste par modèle (couleur par marque), carte cas × modèle, carte cas × question
  d'un modèle. Les essais d'un même modèle sont additionnés.
- **Vignettes de la RCP thrombose** : fondues parmi les cas de l'étude (cas 101 et suivants), repérables par
  la métadonnée *Origine* = « RCP » ; leur référence est la décision de la RCP (voir [Méthode](#méthode)).

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

## Benchmark des LLM

Le principe : un script (sur votre ordinateur) crée un **essai**, demande les vignettes une par une à l'API,
les soumet au LLM, renvoie chaque sortie à l'API, qui la **corrige** et l'enregistre. L'API n'appelle
jamais le LLM elle-même : elle fonctionne avec n'importe quel modèle, et aucune clé de fournisseur n'est
stockée sur le serveur.

```
script ──POST /runs──────────────────────────▶ API   (crée l'essai, fige la référence)
       ──GET /vignettes/{cas}?preprompt=true─▶ API   (prompt à soumettre)
       ──── prompt ──▶ LLM ──── sortie ────┐
       ──POST /runs/{id}/reponses/{cas}────┴─▶ API   (renvoie la grille de correction)
       ──GET /runs/{id}──────────────────────▶ API   (bilan de l'essai)
```

### Lancer l'API en local

```bash
AOD_API_KEYS=moi:dev uv run uvicorn api:app --port 8502
```

Documentation interactive (essayer chaque requête depuis le navigateur) : <http://127.0.0.1:8502/docs>.
Toutes les requêtes, sauf `/health`, exigent une clé : en-tête `Authorization: Bearer <clé>`
ou `X-API-Key: <clé>`. Les clés autorisées sont listées dans `AOD_API_KEYS`, sous la forme `nom:clé`,
séparées par des virgules (ex. `michael:3f9a…,alice:7c21…`). Le nom du titulaire de la clé est enregistré
avec chaque essai (`auteur`) et chaque réponse (`soumis_par`) ; il n'est jamais transmis par le client.
Une clé sans nom (ancien format) reste acceptée, attribuée à `sans_nom`.

### Benchmarker un modèle Claude

```bash
export AOD_API_KEY=dev                      # clé de l'API de benchmark
export ANTHROPIC_API_KEY=...                # clé de l'API Anthropic
uv run bench/benchmark_claude.py --api http://127.0.0.1:8502 --modele claude-opus-5-5
```

Options : `--sans-preprompt`, `--effort low|medium|high|xhigh|max`, `--roles expert_clot expert_gihp`
(référence restreinte à certains rôles ; `--roles rcp` = seulement les vignettes de la RCP),
`--min-repondants 2`, `--cas 1 5 6`, `--notes "..."`.
Pour un autre fournisseur, remplacer la fonction `interroger_claude` du script.

### Règles de correction

- La réponse attendue est l'objet JSON décrit dans `Data/preprompt.txt` ; le JSON est extrait de la sortie
  (un bloc ```` ```json ```` est toléré).
- **Juste** = la valeur fait partie des modalités majoritaires ; en cas d'égalité, n'importe laquelle des
  modalités ex æquo valide. Même règle pour la **stratégie globale**, verdict principal de chaque cas.
- **Référence paramétrable** : tous les répondants par défaut, ou certains rôles seulement (`roles`), avec un
  nombre minimal de répondants (`min_repondants`). Elle est **figée à la création de l'essai**, avec
  l'empreinte des données (`version_donnees`) : un essai reste comparable même si de nouvelles réponses
  humaines arrivent ensuite. Le bilan d'un essai est aussi ventilé par spécialité et par origine (étude / RCP).
- Question sans référence humaine : `non_evaluable`. Question conditionnelle (dernière prise, relais, début
  du relais) qui ne s'applique pas à la réponse du LLM : `non_applicable` (la stratégie porte le verdict).
- Sortie illisible, valeur hors liste ou champ requis manquant : **erreur de format** (`format_valide: false`),
  comptée à part des erreurs cliniques.
- Un **refus** du modèle compte comme une stratégie non trouvée ; une **erreur d'appel** (réseau, clé) est
  exclue du calcul et peut être relancée (`--cas`).

## Tests

```bash
uv run pytest
```

Les tests de notation utilisent des références construites à la main, ceux des vignettes de la RCP un petit
fichier fictif ; le test de bout en bout de l'API s'exécute seulement si des données sont présentes dans `Data/`.

## Organisation du code

| Fichier | Rôle |
|---|---|
| `app.py` | Explorateur Streamlit : filtres, tri, fiches des vignettes, graphiques |
| `api.py` | API de benchmark (FastAPI) |
| `aod/data.py` | Lecture des fichiers (XML REDCap, CSV, XLSX), décodage des réponses, calcul majorité / accord |
| `aod/rcp.py` | Lecture des vignettes de la RCP thrombose (`Data/RCP/*.xlsx`) |
| `aod/scoring.py` | Référence, lecture et validation des sorties de LLM, grille de correction, bilan |
| `aod/store.py` | Stockage des essais et des réponses (SQLite, dans `state/`, non versionné) |
| `aod/resultats.py` | Agrégation des essais pour l'onglet « Résultats LLM » de l'explorateur |
| `bench/benchmark_claude.py` | Script client : benchmark d'un modèle Claude |
| `tests/` | Tests (`uv run pytest`) |
| `pyproject.toml`, `uv.lock`, `.python-version` | Projet uv : dépendances et versions figées |
| `.streamlit/config.toml` | Configuration Streamlit |
| `deploy/` | Fichiers et guide de déploiement sur un VPS (systemd, Nginx, HTTPS) |

## Méthode

- **Correspondance des cas** : chaque sous-dossier de `Data/` est rattaché à une spécialité de `Métadonnées.xlsx`
  (colonne `Xie`). Ses vignettes sont associées, dans l'ordre, à un bloc contigu de « Cas clinique N ». Si un projet
  REDCap couvre plusieurs spécialités (ex. viscérale + urologie), le bloc est étendu là où le sexe du patient
  (premier mot de la vignette) concorde avec les métadonnées.
- **Vignettes de la RCP thrombose** (`Data/RCP/*.xlsx`, feuille « Vignettes ») : le cas *n* du fichier devient
  le cas 100 + *n*, pour ne jamais entrer en collision avec ceux de l'étude. La RCP compte comme un répondant
  unique (rôle `rcp`, sans niveau de confiance). Seules les réponses qu'elle a écrites explicitement sont
  retenues : une cellule vide reste sans réponse (un relais vide n'est pas un « sans relais »), et la stratégie
  globale n'est retenue que si tous ses éléments sont précisés. Sexe, ATC, Délais, Fonction rénale et ALR sont
  lus dans le fichier s'il en a la colonne, sinon déduits du texte selon les conventions de `Métadonnées.xlsx` ;
  l'indication, jugement clinique, n'est jamais déduite (colonne `indication` du fichier, sinon « Non renseigné »).
  Les colonnes de traçabilité (problématique, décision, commentaire, ligne source) ne sont pas lues.
- **Réponse majoritaire** : modalité la plus fréquente ; les égalités sont affichées.
- **Accord** : part des répondants ayant donné la réponse majoritaire (100 % = unanimité).
- **Accord moyen** : moyenne de l'accord sur les questions posées pour le cas (hors stratégie globale).
- Seuls les cas « Complete » dans REDCap sont comptés ; les questions conditionnelles (dernière prise, relais…)
  ne comptent que les répondants concernés.

## Déploiement

Voir [`deploy/README.md`](deploy/README.md).
