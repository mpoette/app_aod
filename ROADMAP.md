# Feuille de route

Pistes pour la suite du projet, de la plus proche à la plus lointaine.

## 1. Réponses des LLM dans l'explorateur

Afficher dans l'app Streamlit les réponses des modèles benchmarkés, à côté de celles des répondants humains :

- pour chaque vignette, la réponse de chaque modèle (ou essai) à chaque question, et si elle est juste
  au regard de la référence humaine (modalités majoritaires) ;
- un verdict sur la stratégie globale par modèle ;
- une vue d'ensemble : taux de réussite par modèle, par question et selon les métadonnées (spécialité, ATC,
  délai, fonction rénale…).

Source : les essais enregistrés par l'API (`aod/store.py`, SQLite dans `state/`), relus avec la même
notation (`aod/scoring.py`).

## 2. Import automatique depuis REDCap

Le formulaire de saisie des experts reste REDCap. Un script lancé sur le PC de l'investigateur :

- interroge l'API REDCap (un jeton par projet) ;
- ne retient que les questionnaires complets (« Complete ») ;
- envoie les réponses à l'app sur le serveur, à la place des exports CSV copiés à la main par `scp`.

Les jetons REDCap restent sur le PC et ne sont jamais versionnés.

## 3. Refonte en site Django (long terme)

Une reconstruction native, et non une intégration de Streamlit. On commence par un **document de
conception**.

- **Données** : PostgreSQL comme source de vérité. Il contient les cas, les questions, les répondants
  (humains et LLM), les réponses et les imports tracés.
- **Apps Django** :
  - `comptes`
  - `vignettes`
  - `reponses`
  - `explorateur`
  - `benchmark` (API avec Django Ninja)
  - `formulaire`
  - `blog` (Wagtail)
- **Interface** : pages rendues côté serveur, avec HTMX et Plotly.js. Les filtres sont portés par l'URL,
  donc partageables.
- **Rôles** : admin, investigateur, expert, invité.
- **Réutilisation** : le noyau `aod/` (lecture des données, notation).
