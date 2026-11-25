# Données (non versionnées)

Ce dossier est ignoré par git : les exports REDCap contiennent les réponses des participants
et restent en dehors du dépôt. Déposez-y les fichiers en respectant cette organisation :

```
Data/
├── Métadonnées.xlsx          # 1 ligne par « Cas clinique N » : Sexe, ATC, Délais, Indication,
│                             # Fonction rénale, ALR, Xie (= spécialité)
├── preprompt.txt             # (facultatif) prompt utilisé pour les LLM
├── Vasculaire/               # un sous-dossier par projet REDCap
│   ├── *.REDCap.xml          # dictionnaire : texte des vignettes, questions, modalités
│   └── *_DATA_*.csv          # réponses (export REDCap, séparateur « ; »)
├── Viscerale/                # contient aussi les cas d'urologie
├── Orthopédie/               # XML seul tant qu'il n'y a pas de réponses
├── Neurochirurgie/
└── RCP/                      # vignettes de la RCP thrombose
    └── *.xlsx                # feuille « Vignettes » : cas, specialite, vignette, risque_hem, …, dosage
```

Si plusieurs exports sont présents dans un sous-dossier, le plus récent (ordre alphabétique) est utilisé.
