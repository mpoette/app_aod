# Déploiement sur un VPS (Ubuntu)

> Une fois l'installation faite, les commandes courantes (lancer un essai de LLM, gérer les clés de l'API et
> les comptes du site, mettre à jour, vérifier les services) sont regroupées dans
> [AIDE-MEMOIRE.md](AIDE-MEMOIRE.md).

## Vue d'ensemble

```
Navigateur ──HTTPS (443)──▶ Nginx ──(mot de passe OK)──▶ Streamlit (127.0.0.1:8501) ──▶ Data/
                             ▲
                    pare-feu : seuls 22, 80, 443 ouverts
```

- **Streamlit** est le petit serveur web de l'app. Il n'écoute qu'en local (`127.0.0.1`), il est donc injoignable directement depuis Internet.
- **systemd** garde l'app en marche : il la lance au démarrage du serveur et la relance si elle plante (`vignettes-aod.service`).
- **Nginx** est le portier : seul à écouter sur les ports publics, il exige le mot de passe, chiffre la connexion (HTTPS) et transmet à l'app. Il peut servir plusieurs apps sur le même VPS : un fichier de site par sous-domaine (`nginx-vignettes-aod.conf`).
- **Certbot** obtient et renouvelle automatiquement le certificat HTTPS (Let's Encrypt).

Le code arrive sur le serveur par **git** (`git clone` puis `git pull`). Les **données** ne sont pas dans le dépôt : elles sont copiées séparément avec `rsync`.

Les exemples supposent le compte `michael` et le domaine `aod.scitoul.fr` ; adaptez-les si besoin, dans les commandes comme dans les deux fichiers de configuration.

## 1. Logiciels du serveur (une fois)

```bash
sudo apt update && sudo apt install -y nginx certbot python3-certbot-nginx apache2-utils rsync git
```

Le pare-feu : on autorise SSH **avant** d'activer (sinon on se coupe sa propre connexion), puis HTTP/HTTPS.

```bash
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

uv s'installe dans le dossier personnel (`~/.local/bin/uv`) et gérera Python lui-même :

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc
```

## 2. Le code et les données

Sur le serveur, on récupère le dépôt. Il est public, donc aucune authentification n'est nécessaire pour le cloner. On le place dans `~/vignettes-aod`, le chemin attendu par le service systemd :

```bash
git clone https://github.com/mpoette/app_aod.git ~/vignettes-aod
```

Depuis votre ordinateur, on envoie les données dans le dossier `Data/` du clone. `rsync` n'envoie que ce qui a changé :

```bash
rsync -av "LLM et AOD/Data/" michael@aod.scitoul.fr:~/vignettes-aod/Data/
```

Le `/` final de la source signifie « le contenu de ce dossier », pour ne pas créer `Data/Data`.

Sans `rsync` (Windows avec Git Bash), `scp` fait la même chose, en recopiant tout à chaque fois :

```bash
scp -r "LLM et AOD/Data/"* michael@aod.scitoul.fr:~/vignettes-aod/Data/
```

De retour sur le serveur, uv installe Python et les versions exactes figées dans `uv.lock` :

```bash
cd ~/vignettes-aod && uv sync --frozen
```

## 3. Le service systemd

```bash
sudo cp ~/vignettes-aod/deploy/vignettes-aod.service /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now vignettes-aod
```

`daemon-reload` fait relire ses fichiers à systemd ; `enable` active le démarrage automatique ; `--now` lance tout de suite. Pour suivre l'état et les messages de l'app :

```bash
systemctl status vignettes-aod
```

```bash
journalctl -u vignettes-aod -f
```

## 4. Nginx et le mot de passe

Le fichier de mots de passe (`-c` le crée ; sans `-c` pour ajouter d'autres personnes) :

```bash
sudo htpasswd -c /etc/nginx/.htpasswd-aod michael
```

Le site : on le place dans `sites-available`, on l'active par un lien dans `sites-enabled`, on fait vérifier la syntaxe, puis on recharge Nginx sans coupure :

```bash
sudo cp ~/vignettes-aod/deploy/nginx-vignettes-aod.conf /etc/nginx/sites-available/vignettes-aod
```

```bash
sudo ln -s /etc/nginx/sites-available/vignettes-aod /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
```

Prérequis : un enregistrement DNS de type A `aod` → IP du VPS dans la zone de `scitoul.fr`.

## 5. HTTPS

```bash
sudo certbot --nginx -d aod.scitoul.fr --redirect
```

Let's Encrypt vérifie que le nom mène bien à ce serveur, puis Certbot ajoute le HTTPS au site Nginx et redirige HTTP vers HTTPS. Le renouvellement est automatique (`certbot.timer`).

## 6. L'API de benchmark

L'API est un second programme, à côté de l'explorateur : son propre service systemd, son propre port interne
(8502), publiée par Nginx sous `https://aod.scitoul.fr/api/`. Elle n'utilise pas le mot de passe Nginx mais
ses **propres clés d'accès**, pour qu'un script puisse s'y connecter sans fenêtre de connexion.

**Le code.** Si l'explorateur a été installé avant l'arrivée de l'API, le clone du serveur ne contient pas
encore `api.py` ni les nouvelles dépendances (FastAPI, uvicorn) : on le met d'abord à jour. Le redémarrage
de l'explorateur est indispensable, car son code a changé lui aussi (`data_loader.py` est devenu `aod/data.py`) :

```bash
cd ~/vignettes-aod && git pull && uv sync --frozen && sudo systemctl restart vignettes-aod
```

**Les clés d'accès.** On les range hors du dépôt, dans un fichier que seul l'administrateur peut lire
(`chmod 600`) ; systemd le lit au démarrage et passe son contenu à l'API comme variables d'environnement.
Chaque clé porte le **nom de son titulaire** (`nom:clé`) : l'API enregistre ce nom avec chaque essai, ce qui
permet de savoir qui a fait quoi. `openssl rand -hex 32` génère une clé aléatoire de 64 caractères :

```bash
sudo mkdir -p /etc/vignettes-aod && echo "AOD_API_KEYS=michael:$(openssl rand -hex 32)" | sudo tee /etc/vignettes-aod/api.env > /dev/null && sudo chmod 600 /etc/vignettes-aod/api.env
```

```bash
sudo cat /etc/vignettes-aod/api.env
```

Notez la clé affichée, **sans** le préfixe `michael:` : c'est elle que le script de benchmark utilisera
(`AOD_API_KEY`).

**Donner une clé à un collègue** (ici `alice`) : on ajoute `,alice:<nouvelle clé>` en fin de ligne, puis on
redémarre l'API, qui ne lit ce fichier qu'au démarrage. La clé s'affiche une fois, pour être transmise par un
canal sûr (gestionnaire de mots de passe, messagerie chiffrée) :

```bash
NOUVELLE=$(openssl rand -hex 32) && sudo sed -i "s/\$/,alice:$NOUVELLE/" /etc/vignettes-aod/api.env && sudo systemctl restart aod-api && echo "Clé d'alice : $NOUVELLE"
```

**Retirer une clé** : `sudo nano /etc/vignettes-aod/api.env`, supprimer `,alice:<clé>`, enregistrer, puis
`sudo systemctl restart aod-api`. Une clé sans nom (ancien format) reste acceptée, attribuée à `sans_nom` ;
pour la nommer, il suffit d'ajouter `nom:` devant.

**Le service**, sur le même principe que celui de l'explorateur :

```bash
sudo cp ~/vignettes-aod/deploy/aod-api.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now aod-api
```

```bash
curl -s http://127.0.0.1:8502/health
```

**Nginx.** Attention : le fichier du site sur le serveur a été **modifié par Certbot** (il y a ajouté le
HTTPS). Ne le remplacez donc pas par celui du dépôt, ajoutez-y le bloc `/api/` à la main :

```bash
sudo nano /etc/nginx/sites-available/vignettes-aod
```

Dans le bloc `server` qui contient `listen 443 ssl` (celui où se trouve déjà `location / { ... }`), ajoutez,
juste après l'accolade fermante de `location /` :

```nginx
    location /api/ {
        auth_basic off;
        proxy_pass http://127.0.0.1:8502/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120;
    }
```

- `auth_basic off` : pas de fenêtre de mot de passe pour ce chemin, l'API vérifie elle-même sa clé.
- Le `/` final de `proxy_pass http://127.0.0.1:8502/` retire le préfixe : une requête vers `/api/vignettes`
  arrive à l'API comme `/vignettes`. L'option `--root-path /api` du service permet à l'API de savoir
  qu'elle est publiée sous `/api` (pour sa page de documentation).

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Vérification depuis votre ordinateur : `https://aod.scitoul.fr/api/health` doit répondre `{"statut":"ok"}`,
et `https://aod.scitoul.fr/api/docs` affiche la documentation interactive. Les essais sont stockés dans
`~/vignettes-aod/state/benchmark.sqlite` (non versionné).

## Mises à jour

**Nouvelles données** : relancer le `rsync` de l'étape 2. Rien à redémarrer, l'app détecte les fichiers modifiés au rechargement de la page.

**Nouvelle version du code** (après un `git push` depuis votre ordinateur) :

```bash
cd ~/vignettes-aod && git pull && uv sync --frozen && sudo systemctl restart vignettes-aod aod-api
```

`git pull` récupère le code, `uv sync` installe d'éventuelles nouvelles dépendances, et le redémarrage des deux services est nécessaire car un programme en cours d'exécution ne relit pas son code. Les nouvelles données, elles, sont prises en compte sans redémarrage par l'explorateur comme par l'API.

## Héberger d'autres apps sur le même VPS

Chaque app a son dossier, son service systemd et **son propre port** interne (8502, 8503…). Côté Nginx, un nouveau fichier de site avec son `server_name` (ex. `projet2.scitoul.fr`) et son `proxy_pass`, plus un enregistrement DNS et un `certbot -d` pour ce nom.
