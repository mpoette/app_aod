# Déploiement sur un VPS (Ubuntu)

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

## Mises à jour

**Nouvelles données** : relancer le `rsync` de l'étape 2. Rien à redémarrer, l'app détecte les fichiers modifiés au rechargement de la page.

**Nouvelle version du code** (après un `git push` depuis votre ordinateur) :

```bash
cd ~/vignettes-aod && git pull && uv sync --frozen && sudo systemctl restart vignettes-aod
```

`git pull` récupère le code, `uv sync` installe d'éventuelles nouvelles dépendances, et le redémarrage est nécessaire car un programme en cours d'exécution ne relit pas son code.

## Héberger d'autres apps sur le même VPS

Chaque app a son dossier, son service systemd et **son propre port** interne (8502, 8503…). Côté Nginx, un nouveau fichier de site avec son `server_name` (ex. `projet2.scitoul.fr`) et son `proxy_pass`, plus un enregistrement DNS et un `certbot -d` pour ce nom.
