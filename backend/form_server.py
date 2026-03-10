"""
EVERYMAIL — Serveur de formulaires intelligents
Génère des formulaires dynamiques selon le type de demande
et traite les réponses pour envoyer un email final automatique
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import uuid
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from groq import Groq
from dotenv import load_dotenv

load_dotenv("../config/.env")

# ─────────────────────────────────────────
# CONFIG (doit correspondre à everymail.py)
# ─────────────────────────────────────────

CONFIG = {
    "groq_api_key": os.getenv("GROQ_API_KEY"),
    "gmail": {
        "email": os.getenv("GMAIL_EMAIL"),
        "password": os.getenv("GMAIL_APP_PASSWORD"),
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
    },
    "company_name": os.getenv("COMPANY_NAME", "UTWORLDIA"),
    "company_context": os.getenv("COMPANY_CONTEXT", """Agence IA UTWORLDIA.
        YourAdmi est une application intelligente de gestion financière personnelle et administrative.
        Centralise toutes les factures, gère le budget quotidien, alertes de paiement.
    """),
    "base_url": "http://localhost:8080",  # Change en prod : https://tondomaine.com
}

# ─────────────────────────────────────────
# STOCKAGE EN MÉMOIRE (remplace par DB en prod)
# ─────────────────────────────────────────

# Stocke les sessions de formulaire en attente
# { token: { type, client_email, client_name, original_subject, created_at } }
PENDING_FORMS = {}

# ─────────────────────────────────────────
# DÉFINITION DES FORMULAIRES PAR TYPE
# ─────────────────────────────────────────

FORM_CONFIGS = {
    "devis": {
        "titre": "Demande de devis",
        "description": "Quelques infos pour te préparer un devis précis et personnalisé.",
        "emoji": "💶",
        "color": "#7B2FFF",
        "fields": [
            {"id": "service", "label": "Service souhaité", "type": "select",
             "options": ["Abonnement mensuel", "Abonnement annuel", "Pack Famille", "Version Pro", "Autre"],
             "required": True},
            {"id": "nb_users", "label": "Nombre d'utilisateurs", "type": "select",
             "options": ["1 personne", "2-3 personnes", "4-5 personnes", "6+ personnes"],
             "required": True},
            {"id": "besoin", "label": "Décris ton besoin principal", "type": "textarea",
             "placeholder": "Ex: Je veux gérer mes factures professionnelles et personnelles séparément...",
             "required": False},
            {"id": "budget", "label": "Budget mensuel envisagé", "type": "select",
             "options": ["Moins de 10€/mois", "10-20€/mois", "20-50€/mois", "Plus de 50€/mois", "À définir"],
             "required": False},
        ]
    },
    "rdv": {
        "titre": "Prise de rendez-vous",
        "description": "Choisis le créneau qui te convient le mieux.",
        "emoji": "📅",
        "color": "#059669",
        "fields": [
            {"id": "objet", "label": "Objet du rendez-vous", "type": "select",
             "options": ["Démo de l'application", "Support technique", "Question facturation", "Autre"],
             "required": True},
            {"id": "date", "label": "Date souhaitée", "type": "date",
             "required": True},
            {"id": "heure", "label": "Créneau horaire", "type": "select",
             "options": ["9h00-10h00", "10h00-11h00", "11h00-12h00", "14h00-15h00", "15h00-16h00", "16h00-17h00"],
             "required": True},
            {"id": "format", "label": "Format préféré", "type": "select",
             "options": ["Appel vidéo (Google Meet)", "Appel téléphonique", "Email"],
             "required": True},
            {"id": "note", "label": "Note supplémentaire", "type": "textarea",
             "placeholder": "Précise si tu as des questions spécifiques à aborder...",
             "required": False},
        ]
    },
    "sav": {
        "titre": "Support client",
        "description": "Décris ton problème — on le règle rapidement.",
        "emoji": "🛠️",
        "color": "#DC2626",
        "fields": [
            {"id": "probleme_type", "label": "Type de problème", "type": "select",
             "options": ["Bug / Erreur application", "Problème de connexion", "Facture non reconnue",
                        "Données incorrectes", "Problème de paiement", "Autre"],
             "required": True},
            {"id": "description", "label": "Décris le problème en détail", "type": "textarea",
             "placeholder": "Ex: Quand je clique sur 'Ajouter une facture', l'application se bloque...",
             "required": True},
            {"id": "depuis", "label": "Depuis quand ?", "type": "select",
             "options": ["Aujourd'hui", "Cette semaine", "Ce mois-ci", "Depuis le début"],
             "required": True},
            {"id": "urgence", "label": "Niveau d'urgence", "type": "select",
             "options": ["🔴 Urgent — je suis bloqué", "🟡 Important — j'en ai besoin rapidement",
                        "🟢 Pas urgent — quand vous pouvez"],
             "required": True},
        ]
    },
    "info": {
        "titre": "Demande d'information",
        "description": "Dis-nous ce que tu veux savoir — on te répond précisément.",
        "emoji": "❓",
        "color": "#0EA5E9",
        "fields": [
            {"id": "sujet", "label": "Sujet de ta question", "type": "select",
             "options": ["Fonctionnalités de l'app", "Tarifs et abonnements",
                        "Compatibilité (iOS / Android / Web)", "Sécurité et données privées",
                        "Intégration bancaire", "Autre"],
             "required": True},
            {"id": "question", "label": "Ta question", "type": "textarea",
             "placeholder": "Pose ta question aussi précisément que possible...",
             "required": True},
            {"id": "canal", "label": "Comment préfères-tu recevoir la réponse ?", "type": "select",
             "options": ["Par email (réponse détaillée)", "Par appel téléphonique", "Les deux"],
             "required": False},
        ]
    }
}

# ─────────────────────────────────────────
# GÉNÉRATION HTML DU FORMULAIRE
# ─────────────────────────────────────────

def generate_form_html(token: str, form_type: str, client_name: str) -> str:
    config = FORM_CONFIGS.get(form_type, FORM_CONFIGS["info"])
    color = config["color"]

    fields_html = ""
    for f in config["fields"]:
        req = ' <span style="color:#ef4444">*</span>' if f.get("required") else ""
        label = f'<label style="display:block;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#888;margin-bottom:8px">{f["label"]}{req}</label>'

        if f["type"] == "select":
            options = "".join([f'<option value="{o}">{o}</option>' for o in f["options"]])
            input_html = f'<select name="{f["id"]}" id="{f["id"]}" style="width:100%;background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:12px 16px;color:white;font-size:15px;outline:none;font-family:inherit" {"required" if f.get("required") else ""}><option value="">Sélectionner...</option>{options}</select>'
        elif f["type"] == "textarea":
            input_html = f'<textarea name="{f["id"]}" id="{f["id"]}" placeholder="{f.get("placeholder","")}" style="width:100%;background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:12px 16px;color:white;font-size:15px;outline:none;font-family:inherit;min-height:100px;resize:vertical" {"required" if f.get("required") else ""}></textarea>'
        elif f["type"] == "date":
            input_html = f'<input type="date" name="{f["id"]}" id="{f["id"]}" style="width:100%;background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:12px 16px;color:white;font-size:15px;outline:none;font-family:inherit" {"required" if f.get("required") else ""}>'
        else:
            input_html = f'<input type="text" name="{f["id"]}" id="{f["id"]}" placeholder="{f.get("placeholder","")}" style="width:100%;background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:12px 16px;color:white;font-size:15px;outline:none;font-family:inherit" {"required" if f.get("required") else ""}>'

        fields_html += f'<div style="margin-bottom:20px">{label}{input_html}</div>'

    prenom = client_name.split()[0] if client_name else "là"

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{config['titre']} — {CONFIG['company_name']}</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:#0a0a0f;color:white;font-family:'Space Grotesk',sans-serif;min-height:100vh">

<div style="max-width:560px;margin:0 auto;padding:40px 20px">

  <!-- Header -->
  <div style="text-align:center;margin-bottom:40px">
    <div style="font-family:'Syne',sans-serif;font-size:20px;font-weight:800;color:white;margin-bottom:24px">
      {CONFIG['company_name']}
    </div>
    <div style="font-size:48px;margin-bottom:16px">{config['emoji']}</div>
    <h1 style="font-family:'Syne',sans-serif;font-size:28px;font-weight:800;margin:0 0 12px;letter-spacing:-0.5px">{config['titre']}</h1>
    <p style="color:#888;font-size:16px;margin:0">Salut {prenom} ! {config['description']}</p>
  </div>

  <!-- Formulaire -->
  <div style="background:#13131a;border:1px solid #222;border-radius:16px;padding:36px" id="form-card">
    <form id="main-form">
      {fields_html}
      <button type="submit" style="width:100%;background:{color};color:white;border:none;border-radius:8px;padding:16px;font-family:'Syne',sans-serif;font-size:18px;font-weight:800;cursor:pointer;margin-top:8px">
        Envoyer ma demande ⚡
      </button>
      <p style="text-align:center;color:#555;font-size:13px;margin-top:16px">🔒 Tes données restent privées</p>
    </form>
  </div>

  <!-- Succès (caché) -->
  <div id="success" style="display:none;text-align:center;background:#13131a;border:1px solid #222;border-radius:16px;padding:60px 36px">
    <div style="font-size:64px;margin-bottom:20px">🚀</div>
    <h2 style="font-family:'Syne',sans-serif;font-size:28px;font-weight:800;margin:0 0 12px">Demande envoyée !</h2>
    <p style="color:#888;font-size:16px">On te répond très vite par email avec une réponse complète et personnalisée.</p>
    <p style="color:{color};font-weight:600;margin-top:20px">{CONFIG['company_name']} — On s'occupe de tout ✦</p>
  </div>

</div>

<script>
document.getElementById('main-form').addEventListener('submit', async function(e) {{
  e.preventDefault();
  const btn = this.querySelector('button[type=submit]');
  btn.textContent = '⏳ Envoi en cours...';
  btn.disabled = true;

  const data = {{}};
  new FormData(this).forEach((v, k) => data[k] = v);

  try {{
    const res = await fetch('/submit/{token}', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(data)
    }});

    if (res.ok) {{
      document.getElementById('form-card').style.display = 'none';
      document.getElementById('success').style.display = 'block';
    }} else {{
      btn.textContent = '❌ Erreur — réessaie';
      btn.disabled = false;
    }}
  }} catch(err) {{
    btn.textContent = '❌ Erreur réseau';
    btn.disabled = false;
  }}
}});
</script>
</body>
</html>"""

# ─────────────────────────────────────────
# GÉNÉRATION EMAIL FINAL AVEC IA
# ─────────────────────────────────────────

def generate_final_response(form_type: str, session: dict, form_data: dict) -> str:
    client = Groq(api_key=CONFIG["groq_api_key"])
    config = FORM_CONFIGS.get(form_type, FORM_CONFIGS["info"])

    prompt = f"""Tu es l'assistant email de {CONFIG['company_name']}.

CONTEXTE :
{CONFIG['company_context']}

Un client vient de remplir un formulaire de type "{config['titre']}".

INFOS CLIENT :
- Nom : {session.get('client_name', 'Client')}
- Email : {session.get('client_email', '')}
- Sujet original : {session.get('original_subject', '')}

RÉPONSES DU FORMULAIRE :
{json.dumps(form_data, ensure_ascii=False, indent=2)}

Rédige un email de réponse complet, personnalisé et professionnel en français.
Ton : moderne, dynamique, humain.
- Commence par "Bonjour [Prénom],"
- Confirme ce qu'on a reçu
- Donne une réponse concrète ou les prochaines étapes précises
- Termine par "À très vite,\\nL'équipe {CONFIG['company_name']} 🚀"
Réponds UNIQUEMENT avec le corps de l'email."""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ Erreur Groq: {e}")
        prenom = session.get('client_name', 'là').split()[0]
        return f"""Bonjour {prenom},

Nous avons bien reçu ta demande et nous allons la traiter dans les plus brefs délais.

Notre équipe revient vers toi très rapidement avec une réponse complète et personnalisée.

À très vite,
L'équipe {CONFIG['company_name']} 🚀"""

# ─────────────────────────────────────────
# ENVOI EMAIL FINAL
# ─────────────────────────────────────────

def send_final_email(to: str, subject: str, body: str) -> bool:
    try:
        msg = MIMEMultipart()
        msg["From"] = CONFIG["gmail"]["email"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(CONFIG["gmail"]["smtp_host"], CONFIG["gmail"]["smtp_port"]) as server:
            server.starttls()
            server.login(CONFIG["gmail"]["email"], CONFIG["gmail"]["password"])
            server.send_message(msg)
        print(f"✅ Email final envoyé à {to}")
        return True
    except Exception as e:
        print(f"❌ Erreur envoi: {e}")
        return False

# ─────────────────────────────────────────
# SERVEUR HTTP
# ─────────────────────────────────────────

class FormHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"📡 {datetime.now().strftime('%H:%M:%S')} — {format % args}")

    def do_GET(self):
        parsed = urlparse(self.path)

        # Route : /form/{token} → affiche le formulaire
        if parsed.path.startswith("/form/"):
            token = parsed.path.split("/form/")[1].strip("/")

            if token not in PENDING_FORMS:
                self.send_error(404, "Formulaire introuvable ou expiré")
                return

            session = PENDING_FORMS[token]
            html = generate_form_html(token, session["form_type"], session["client_name"])

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)

        # Route : /submit/{token} → traite le formulaire soumis
        if parsed.path.startswith("/submit/"):
            token = parsed.path.split("/submit/")[1].strip("/")

            if token not in PENDING_FORMS:
                self.send_response(404)
                self.end_headers()
                return

            # Lit les données JSON
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                form_data = json.loads(body)
            except:
                self.send_response(400)
                self.end_headers()
                return

            session = PENDING_FORMS[token]
            print(f"\n📋 Formulaire reçu de {session['client_email']} (type: {session['form_type']})")
            print(f"   Données: {form_data}")

            # Génère la réponse IA
            print("🤖 Génération réponse IA...")
            response_text = generate_final_response(session["form_type"], session, form_data)

            # Envoie l'email final
            subject = f"Re: {session['original_subject']}"
            send_final_email(session["client_email"], subject, response_text)

            # Supprime la session (usage unique)
            del PENDING_FORMS[token]

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())

        else:
            self.send_error(404)

# ─────────────────────────────────────────
# FONCTIONS UTILITAIRES (appelées par everymail.py)
# ─────────────────────────────────────────

def create_form_session(form_type: str, client_email: str, client_name: str, original_subject: str) -> str:
    """Crée une session de formulaire et retourne le lien"""
    token = str(uuid.uuid4())[:12]
    PENDING_FORMS[token] = {
        "form_type": form_type,
        "client_email": client_email,
        "client_name": client_name,
        "original_subject": original_subject,
        "created_at": datetime.now().isoformat(),
    }
    link = f"{CONFIG['base_url']}/form/{token}"
    print(f"🔗 Formulaire créé : {link}")
    return link


def start_server(port: int = 8080):
    """Lance le serveur de formulaires"""
    server = HTTPServer(("0.0.0.0", port), FormHandler)
    print(f"🌐 Serveur formulaires démarré sur http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    # Test standalone
    token = create_form_session("devis", "test@gmail.com", "Thomas Dupont", "Demande de devis")
    print(f"Test : ouvre http://localhost:8080/form/{token.split('/')[-1]}")
    start_server()
