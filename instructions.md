# Spécifications & Instructions du Projet : Sports Data Quality & Reconciliation Engine

## 1. Contexte & Rôle du Système
Ce projet est un pipeline ETL automatisé d'ingestion, d'harmonisation (Entity Resolution), de validation stricte (Data Quality Engine) et d'audit de données de cotes sportives (marché 1N2 en football).
L'objectif est d'ingérer des flux web non standardisés, de garantir l'absence d'incohérences logiques et mathématiques, de persister les données propres dans MySQL et de générer un classeur d'audit Excel (`openpyxl`) consignant les données valides et le journal des anomalies rejetées.

---

## 2. Environnement & Stack Technique
- **Langage :** Python 3.11+
- **Ingestion & Scraping :** Playwright (Headless Chromium), Requests, BeautifulSoup4
- **Manipulation & Calcul :** Pandas, Pydantic (validation de schéma d'échange)
- **Harmonisation d'entités :** RapidFuzz (Tier 2), API LLM OpenAI/Anthropic avec sortie JSON stricte (Tier 3)
- **Base de Données & ORM :** MySQL 8.0 (déployé sous Docker Compose), SQLAlchemy 2.0, PyMySQL
- **Restitution & Reporting :** OpenPyXL (formatage de cellules, formules d'audit, styles conditionnels), Streamlit

---

## 3. Architecture des Dossiers
```text
sports-data-quality-engine/
├── .env
├── .gitignore
├── .instructions.md
├── docker-compose.yml
├── requirements.txt
├── README.md
├── data/
│   ├── raw/
│   └── reference/
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py
│   │   └── models.py
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── golden_source_loader.py
│   │   └── live_odds_scraper.py
│   ├── reconciliation/
│   │   ├── __init__.py
│   │   └── entity_resolver.py
│   ├── validation/
│   │   ├── __init__.py
│   │   └── dq_engine.py
│   └── reporting/
│       ├── __init__.py
│       └── excel_exporter.py
├── app.py
└── main.py