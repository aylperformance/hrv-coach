# HRV Coach

Application web d'analyse de la Variabilité de la Fréquence Cardiaque (VFC) pour le protocole assis/debout 10 minutes.

## Fonctionnalités

- **Gestion des athlètes** : CRUD complet, archivage non destructif, photo-initiales colorées
- **Import** : fichiers `.txt` (intervalles RR ligne par ligne) et `.csv` Kubios Scientific Lite
- **Détection automatique** de la transition assis→debout (avec correction manuelle via curseur)
- **Exclusion automatique** des 60 premières secondes de chaque segment
- **Marqueurs HRV complets** :
  - Temporel : Mean RR, Mean/Min/Max HR, SDNN, RMSSD, pNN50
  - Fréquentiel (Welch FFT, interp 4 Hz) : VLF, LF, HF, Total Power, LF/HF, %, n.u.
  - Non-linéaire : SD1, SD2, SD2/SD1, SampEn, DFA α1, Stress Index Baevsky
  - Synthétique : PNS index, SNS index
- **Classification Freville** : 5 types de fatigue + alertes SNC / hydratation / DFA
- **Graphique d'évolution** Chart.js avec médiane glissante, code couleur par type
- **Snapshot du jour** + filtre par statut (vert/orange/rouge)
- **Responsive** : mobile (consultation) et desktop (analyse)

## Stack

- **Backend** : Python 3.12 + FastAPI 0.115 + SQLAlchemy 2.0
- **Base** : SQLite (1 fichier, migration PostgreSQL facile)
- **Calculs** : NumPy + SciPy (Welch, interpolation cubique)
- **Frontend** : Jinja2 + CSS pur + Chart.js 4 (CDN)
- **Déploiement** : Docker, prêt pour Railway ou Render

## Installation locale

```bash
# Cloner / extraire le projet, puis :
cd HRV-Coach
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt

# Lancer le serveur
uvicorn main:app --reload --port 8000
```

Ouvrir <http://localhost:8000>.

La base SQLite est créée automatiquement au démarrage (`hrv_coach.db` à côté de `main.py`).

## Validation du moteur

Une suite de tests synthétiques est disponible :

```bash
python test_engine.py
```

Elle valide chaque marqueur (temporel, fréquentiel, non-linéaire), la détection de transition, le filtre Malik, le pipeline complet sur un signal synthétique avec transition à ~65→85 bpm.

## Déploiement Railway

1. Créer un compte Railway et installer la CLI (`npm i -g @railway/cli`).
2. Depuis le dossier du projet :
   ```bash
   railway login
   railway init
   railway up
   ```
3. Dans le dashboard Railway :
   - Ajouter un **volume** monté sur `/data` (1 Go suffit largement)
   - Variable d'environnement : `HRV_DB_PATH=/data/hrv_coach.db`
4. Générer un domaine public dans Settings → Networking.

Le `railway.toml` configure le build Docker et le healthcheck `/health`.

## Déploiement Render

1. Créer un compte Render, lier le repo Git.
2. Render détecte `render.yaml` et crée le service web + le disque persistant `/data` automatiquement.
3. Vérifier dans Environment que `HRV_DB_PATH=/data/hrv_coach.db`.

## Build Docker local

```bash
docker build -t hrv-coach .
docker run -p 8000:8000 -v "$(pwd)/data:/data" hrv-coach
```

## Structure

```
HRV-Coach/
├── main.py                # FastAPI app
├── database.py            # SQLAlchemy 2.0 : Athlete, Test
├── hrv_engine.py          # Tous les calculs HRV + classification Freville
├── test_engine.py         # Tests unitaires synthétiques
├── routes/
│   ├── athletes.py        # CRUD athlètes
│   ├── tests.py           # Import / preview / confirm / detail / API
│   └── dashboard.py       # Page d'accueil + archived
├── templates/
│   ├── base.html
│   ├── index.html         # Liste athlètes + snapshot
│   ├── athlete.html       # Profil + historique + graphique évolution
│   ├── athlete_form.html  # Création/édition athlète
│   ├── test_import.html   # Drag&drop + preview + curseur transition
│   ├── test_detail.html   # Banner couleur + tous les marqueurs
│   └── archived.html
├── static/
│   ├── style.css          # Palette vert/orange/rouge, responsive
│   └── app.js             # Drag&drop, preview AJAX, Chart.js
├── Dockerfile
├── railway.toml
├── render.yaml
├── requirements.txt
└── .gitignore
```

## Formats de fichiers supportés

### .txt (intervalles RR bruts)
Un intervalle RR en millisecondes par ligne (séparateurs supportés : retour ligne, virgule, point-virgule, espace).

```
1143
966
895
910
...
```

Date extraite automatiquement du nom de fichier au format `YYYY-MM-DD[_HH-MM-SS].txt` (ex : `2026-05-04_07-52-32.txt`).

### .csv Kubios Scientific Lite
Sections `RESULTS FOR SINGLE SAMPLES` avec colonnes `SAMPLE 1` (assis) et `SAMPLE 2` (debout) — les valeurs sont reprises directement sans recalcul.

## Notes implémentation

- **Filtre artefacts** : Malik 20 %, remplacement par interpolation linéaire.
- **FFT** : Welch avec fenêtre Hann sur tachogramme interpolé cubique à 4 Hz.
- **SampEn** : m=2, r=0.2×SD ; algorithme O(n²) suffisant pour des segments de ~300-500 beats.
- **DFA α1** : échelles 4–16 beats, régression log-log.
- **Stress Index** : Baevsky avec bins 50 ms.
- **PNS / SNS** : composites z-score normalisés sur population adulte (proche Kubios).

## Roadmap éventuelle

- Authentification multi-coach
- Export PDF du rapport
- Migration PostgreSQL pour multi-utilisateurs
- API publique pour intégration montre/capteur
- Comparaison médiane personnelle vs population de référence

---

Code généré pour Amaury — usage solo coach sportif.
