# ⚡ UTWORLDIA

> Bot email intelligent + formulaires automatiques pour PME.  
> Powered by Groq (Llama) — 100% gratuit.

---

## 🚀 Installation (5 min)

### 1. Installe les dépendances
```bash
pip3 install -r requirements.txt
```

### 2. Configure ton .env
```bash
cp config/.env.example config/.env
```
Ouvre `config/.env` et remplis :
```
GROQ_API_KEY=gsk-ta-clé-groq
GMAIL_EMAIL=tonmail@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
COMPANY_NAME=Nom du client
COMPANY_CONTEXT=Description de l'activité du client
```

### 3. Lance le bot
```bash
cd backend
python3 everymail.py
```

---

## ⚙️ Comment ça marche

```
Email reçu (non lu)
      ↓
Analyse IA (Groq / Llama)
      ↓
Email vague ?
  ├── OUI → Envoie un formulaire personnalisé par email
  │         → Client remplit → IA génère réponse finale
  └── NON → Score d'urgence
              ├── ≥ 7 → Envoi automatique
              └── < 7 → Brouillon à valider
```

### Formulaires disponibles
| Type | Déclencheur |
|------|-------------|
| `devis` | Demande de devis vague |
| `rdv` | Demande de RDV sans date/heure |
| `sav` | Problème sans description |
| `info` | Question trop générale |

---

## 🔧 Personnaliser pour un client

Ouvre `config/.env` et change :
```
COMPANY_NAME=Garage Dupont
COMPANY_CONTEXT=Garage automobile spécialisé en pneus et entretien.
                Horaires : Lun-Sam 8h-18h. Tel : 02 123 45 67.
GMAIL_EMAIL=contact@garagedupont.be
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

C'est tout — le bot s'adapte automatiquement.

---

## 📁 Structure
```
UTWORLDIA/
├── backend/
│   ├── everymail.py     ← Bot principal
│   └── form_server.py   ← Serveur formulaires
├── config/
│   └── .env.example     ← Template config
├── requirements.txt
└── README.md
```

---

## 💰 Packs UTWORLDIA

| Pack | Prix | Maintenance |
|------|------|-------------|
| Starter | 499€ | 99€/mois |
| Business | 899€ | 149€/mois |
| Premium | 1499€ | 199€/mois |

---

**UTWORLDIA** — Upset The World with AI ⚡
