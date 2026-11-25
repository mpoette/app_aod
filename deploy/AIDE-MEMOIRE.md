# Aide-mémoire du serveur

Les commandes courantes pour administrer une installation faite selon [README.md](README.md) (qui explique
le pourquoi de chaque élément). Elles supposent les emplacements de ce guide : le dépôt cloné dans
`~/vignettes-aod`, les clés de l'API dans `/etc/vignettes-aod/api.env`, les comptes du site dans
`/etc/nginx/.htpasswd-aod`, les services `vignettes-aod` et `aod-api`.

Dans les commandes, remplacez ce qui est en `MAJUSCULES` : `UTILISATEUR` (votre compte sur le serveur),
`DOMAINE` (le nom du site, ex. `aod.exemple.fr`), `NOM`, `MODELE`, `IDENTIFIANT`…

Sauf mention contraire, tout se fait **sur le serveur**, dans le dossier du projet :

```bash
ssh UTILISATEUR@DOMAINE
```

```bash
cd ~/vignettes-aod
```

| Fichier | Contenu | Lu par |
|---|---|---|
| `/etc/vignettes-aod/api.env` | clés **acceptées** par l'API AOD (`nom:clé`) | le service `aod-api`, au démarrage |
| `/etc/nginx/.htpasswd-aod` | comptes et mots de passe **du site** | Nginx, à chaque connexion |
| `~/.aod-bench.env` | clés **utilisées** par les scripts de benchmark (AOD, OVH, Anthropic…) | vous, avec `source` |
| `~/vignettes-aod/state/benchmark.sqlite` | essais et réponses des LLM | l'API et l'onglet Résultats LLM |

---

## 1. Lancer un essai de LLM

**Charger les clés** (à refaire dans chaque nouveau terminal ou session tmux) :

```bash
set -a && source ~/.aod-bench.env && set +a
```

`~/.aod-bench.env` contient une variable par ligne, sans espace autour du `=`, sans `export` ni `;` ;
guillemets simples seulement si la clé contient `$`, une espace, `#`, `!`… :

```
AOD_API_KEY=…
LLM_API_KEY=…
ANTHROPIC_API_KEY=…
```

Vérifier qu'elles sont chargées (affiche leur longueur, pas leur contenu ; 0 = non chargée) :

```bash
echo "AOD : ${#AOD_API_KEY} · LLM : ${#LLM_API_KEY} · Anthropic : ${#ANTHROPIC_API_KEY}"
```

**Travailler dans tmux**, pour que l'essai continue si la connexion SSH se coupe :

| Action | Commande |
|---|---|
| ouvrir une session | `tmux new -s bench` |
| se détacher (l'essai continue) | **Ctrl+B** puis **D** |
| revenir | `tmux attach -t bench` |
| lister les sessions | `tmux ls` |

**Modèle servi au format OpenAI** — toujours commencer par `--cas 1`. Exemple avec OVHcloud AI Endpoints ;
pour un autre fournisseur (Mistral, OpenRouter, vLLM…), changer `--base-url` et la clé `LLM_API_KEY` :

```bash
uv run bench/benchmark_openai.py --api http://127.0.0.1:8502 --base-url https://oai.endpoints.kepler.ai.cloud.ovh.net/v1 --modele MODELE --nom MODELE-ovh --cas 1
```

**Modèle Claude** :

```bash
uv run bench/benchmark_claude.py --api http://127.0.0.1:8502 --modele claude-opus-5-5 --cas 1
```

Puis relancer **sans** `--cas` pour tous les cas évaluables. Options utiles :

| Option | Effet |
|---|---|
| `--cas 1 5 6` | seulement ces cas (relancer des erreurs d'appel) |
| `--roles rcp` | seulement les vignettes de la RCP |
| `--nom NOM` | nom affiché dans l'onglet (préciser l'hébergeur : `-ovh`) |
| `--notes "…"` | note libre enregistrée avec l'essai |
| `--sans-preprompt` | vignette seule, sans préprompt |
| `--temperature 0` | (OpenAI) réponses moins variables |
| `--effort high` | (Claude) effort de raisonnement |

**Lister les modèles disponibles** (ici chez OVH ; le chemin `/models` est le même ailleurs) :

```bash
curl -s -H "Authorization: Bearer $LLM_API_KEY" https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data']]"
```

**Plusieurs modèles, trois répétitions chacun** (dans tmux) :

```bash
for m in MODELE1 MODELE2; do for i in 1 2 3; do uv run bench/benchmark_openai.py --api http://127.0.0.1:8502 --base-url https://oai.endpoints.kepler.ai.cloud.ovh.net/v1 --modele "$m" --nom "$m-ovh" --notes "répétition $i"; done; done
```

---

## 2. Consulter et gérer les essais

Les résultats se lisent dans l'onglet **🤖 Résultats LLM** du site. En ligne de commande :

**Lister les essais** (identifiant, date, modèle, auteur, nombre de réponses) :

```bash
python3 -c "import sqlite3; [print(*r, sep='  ') for r in sqlite3.connect('state/benchmark.sqlite').execute('SELECT r.id, substr(r.cree_le,1,16), r.modele, coalesce(r.auteur,\"inconnu\"), count(p.cas) FROM runs r LEFT JOIN reponses p ON p.run_id=r.id GROUP BY r.id ORDER BY r.cree_le')]"
```

**Sauvegarder la base** (copie cohérente même si un essai est en cours) :

```bash
python3 -c "import sqlite3,datetime; s=sqlite3.connect('state/benchmark.sqlite'); d=sqlite3.connect(f'state/benchmark-{datetime.date.today()}.sqlite'); s.backup(d); d.close(); print('sauvegarde faite')"
```

**Supprimer un essai** (essai de test, essai vide…) — sauvegarder d'abord, c'est définitif :

```bash
python3 -c "import sqlite3,sys; c=sqlite3.connect('state/benchmark.sqlite'); r=sys.argv[1]; n=c.execute('DELETE FROM reponses WHERE run_id=?',(r,)).rowcount; m=c.execute('DELETE FROM runs WHERE id=?',(r,)).rowcount; c.commit(); print(m,'essai et',n,'réponses supprimés')" IDENTIFIANT
```

---

## 3. Clés de l'API AOD

Après **toute** modification de `api.env`, redémarrer l'API (elle ne lit ce fichier qu'au démarrage) :

```bash
sudo systemctl restart aod-api
```

**Lister les clés** (nom et 8 premiers caractères seulement) :

```bash
sudo sed 's/^AOD_API_KEYS=//' /etc/vignettes-aod/api.env | tr ',' '\n' | awk -F: '{ if (NF==1) print "sans_nom   " substr($1,1,8) "…"; else print $1 "   " substr($2,1,8) "…" }'
```

**Créer une clé** pour `NOM` (minuscules, sans `,` ni `:`), puis redémarrer l'API. La clé s'affiche une seule
fois : la transmettre par un canal sûr (gestionnaire de mots de passe, messagerie chiffrée) :

```bash
NOM=NOM; NOUVELLE=$(openssl rand -hex 32) && sudo sed -i "s/\$/,$NOM:$NOUVELLE/" /etc/vignettes-aod/api.env && echo "Clé de $NOM : $NOUVELLE"
```

**Afficher la clé** de `NOM` :

```bash
NOM=NOM; sudo sed 's/^AOD_API_KEYS=//' /etc/vignettes-aod/api.env | tr ',' '\n' | grep -i "^ *$NOM *:" | cut -d: -f2- | tr -d ' '
```

**Renouveler la clé** de `NOM` (l'ancienne cesse de fonctionner ; les essais passés restent à son nom) :

```bash
NOM=NOM; NOUVELLE=$(openssl rand -hex 32) && sudo sed -i -E "s/([=,])$NOM:[^,]*/\1$NOM:$NOUVELLE/" /etc/vignettes-aod/api.env && echo "Nouvelle clé de $NOM : $NOUVELLE"
```

**Supprimer la clé** de `NOM` :

```bash
NOM=NOM; sudo sed -i -E "s/([=,])$NOM:[^,]*/\1/; s/,,+/,/g; s/=,/=/; s/,\$//" /etc/vignettes-aod/api.env
```

**Nommer une clé sans nom** (`DEBUT` = ses 8 premiers caractères, vus dans la liste) :

```bash
NOM=NOM; DEBUT=DEBUT; sudo sed -i "s/\([=,]\)$DEBUT/\1$NOM:$DEBUT/" /etc/vignettes-aod/api.env
```

Ou à la main : `sudo nano /etc/vignettes-aod/api.env` — une seule ligne, `AOD_API_KEYS=nom:clé,nom2:clé2`, sans espace
ni guillemets (Ctrl+O, Entrée, Ctrl+X).

---

## 4. Comptes du site (mot de passe Nginx)

Ces comptes ouvrent **le site seulement** : ni l'API, ni la machine. Aucun redémarrage nécessaire.

| Action | Commande |
|---|---|
| lister les comptes | `sudo cut -d: -f1 /etc/nginx/.htpasswd-aod` |
| créer un compte | `sudo htpasswd /etc/nginx/.htpasswd-aod NOM` |
| changer un mot de passe | `sudo htpasswd /etc/nginx/.htpasswd-aod NOM` (même commande) |
| vérifier un mot de passe | `sudo htpasswd -v /etc/nginx/.htpasswd-aod NOM` |
| supprimer un compte | `sudo htpasswd -D /etc/nginx/.htpasswd-aod NOM` |
| générer un mot de passe | `openssl rand -base64 18` |

⚠️ **Jamais `-c`** : cette option recrée le fichier et efface tous les comptes.

Les mots de passe ne s'affichent pas à la saisie et ne peuvent pas être relus (le fichier ne garde qu'une
empreinte) : on ne peut que les remplacer.

---

## 5. Mettre à jour le code

Après un `git push` depuis l'ordinateur :

```bash
git pull && uv sync --frozen && sudo systemctl restart vignettes-aod aod-api
```

---

## 6. Mettre à jour les données

**Depuis l'ordinateur** (pas de `rsync` : `scp`). Aucun redémarrage : le site et l'API relisent les fichiers.

```bash
scp "CHEMIN/LOCAL/FICHIER.xlsx" UTILISATEUR@DOMAINE:~/vignettes-aod/Data/
```

Vignettes de la RCP : dans `Data/RCP/` (le dossier doit exister : `mkdir -p ~/vignettes-aod/Data/RCP`).

---

## 7. Services et vérifications

| Action | Commande |
|---|---|
| état des services | `systemctl status vignettes-aod aod-api --no-pager` |
| redémarrer le site / l'API | `sudo systemctl restart vignettes-aod` / `sudo systemctl restart aod-api` |
| journal du site / de l'API (Ctrl+C pour quitter) | `journalctl -u vignettes-aod -f` / `journalctl -u aod-api -f` |
| 30 derniers messages d'erreur de l'API | `journalctl -u aod-api -n 30 --no-pager` |
| l'API répond-elle ? | `curl -s http://127.0.0.1:8502/health` (attendre 2 s après un redémarrage) |
| configuration Nginx valide + rechargement | `sudo nginx -t && sudo systemctl reload nginx` |
| certificat HTTPS (date d'expiration) | `sudo certbot certificates` |
| espace disque | `df -h /` |

⚠️ Le fichier du site Nginx a été modifié par Certbot : ne pas le remplacer par celui du dépôt.

---

## 8. Accès SSH

Le serveur n'accepte que les clés SSH. Pour autoriser un nouveau poste : sur ce poste, `cat ~/.ssh/id_ed25519.pub`
(ou `id_rsa.pub`) ; puis, depuis un poste déjà autorisé, ajouter la ligne avec **deux** `>` :

```bash
echo "LIGNE_DE_LA_CLE_PUBLIQUE" >> ~/.ssh/authorized_keys
```

Retirer un poste : supprimer sa ligne dans `nano ~/.ssh/authorized_keys` (repérable par le commentaire en fin de
ligne). Chaque clé autorisée ouvre **toute la machine** : uniquement des postes à vous.
