"""
UTWORLDIA — Bot Email Intelligent
Analyse les emails entrants, détecte le type de demande,
envoie un formulaire si nécessaire ou répond directement.
"""

import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from groq import Groq
from dotenv import load_dotenv
import json
import os
import re
import time
import threading
from datetime import datetime
from dataclasses import dataclass, field

load_dotenv("../config/.env")

# ─────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────

CONFIG = {
    "groq_api_key":     os.getenv("GROQ_API_KEY"),
    "sendgrid_api_key": os.getenv("SENDGRID_API_KEY"),

    "gmail": {
        "email":     os.getenv("GMAIL_EMAIL"),
        "password":  os.getenv("GMAIL_APP_PASSWORD"),
        "imap_host": "imap.gmail.com",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
    },

    "company_name":      os.getenv("COMPANY_NAME", "UTWORLDIA"),
    "company_context":   os.getenv("COMPANY_CONTEXT", "Agence d'automatisation IA pour PME."),
    "base_url":          os.getenv("BASE_URL", "http://localhost:8080"),
    "urgency_threshold": 7,
    "check_interval":    60,
}

# ─────────────────────────────────────────────────────
# STRUCTURE DE DONNÉES
# ─────────────────────────────────────────────────────

@dataclass
class EmailMessage:
    uid:                str
    sender:             str
    sender_email:       str
    subject:            str
    body:               str
    date:               str
    urgency_score:      int  = 0
    category:           str  = ""
    needs_form:         bool = False
    form_type:          str  = ""
    suggested_response: str  = ""
    action:             str  = ""

# ─────────────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    """Supprime tous les caractères qui cassent le JSON ou l'IMAP."""
    if not text:
        return ""
    # Supprime les caractères de contrôle sauf \n et \t
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', text)
    # Normalise les sauts de ligne
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Limite la longueur
    return text.strip()

def clean_for_prompt(text: str) -> str:
    """Nettoie le texte pour l'injection dans un prompt IA."""
    text = clean_text(text)
    # Remplace les guillemets et apostrophes qui cassent le JSON
    text = text.replace('\\', ' ').replace('"', ' ').replace("'", ' ')
    return text[:2000]

def decode_mime_header(raw: str) -> str:
    """Décode un header MIME encodé (ex: =?utf-8?...)."""
    if not raw:
        return ""
    try:
        parts = decode_header(raw)
        result = ""
        for part, charset in parts:
            if isinstance(part, bytes):
                result += part.decode(charset or "utf-8", errors="replace")
            else:
                result += str(part)
        return clean_text(result)
    except Exception:
        return clean_text(str(raw))

# ─────────────────────────────────────────────────────
# CONNEXION EMAIL
# ─────────────────────────────────────────────────────

class EmailConnector:

    IGNORE_SENDERS = [
        "noreply", "no-reply", "donotreply", "do-not-reply",
        "newsletter", "mailer", "notification", "notifications",
        "automated", "automatique", "bounce", "postmaster",
        "support@github", "info@linkedin", "member@linkedin",
        "daemon", "alert", "system", "wordpress", "woocommerce",
    ]

    IGNORE_SUBJECTS = [
        "unsubscribe", "se désabonner", "newsletter",
        "promotion", "offre spéciale", "soldes",
        "nouvel horaire", "nouveau programme", "planning",
        "votre facture", "your invoice", "reçu de paiement",
        "verify your", "confirm your", "activate your",
        "password reset", "réinitialisation",
    ]

    def __init__(self):
        self.cfg  = CONFIG["gmail"]
        self.imap = None

    def _reconnect(self) -> bool:
        """Ferme et réouvre la connexion IMAP proprement."""
        try:
            if self.imap:
                try:
                    self.imap.logout()
                except Exception:
                    pass
            self.imap = None
        except Exception:
            pass
        return self.connect()

    def connect(self) -> bool:
        try:
            self.imap = imaplib.IMAP4_SSL(self.cfg["imap_host"])
            self.imap.login(self.cfg["email"], self.cfg["password"])
            print("✅ Gmail connecté")
            return True
        except Exception as e:
            print(f"❌ Connexion Gmail échouée : {e}")
            self.imap = None
            return False

    def _ping(self) -> bool:
        """Vérifie si la connexion est vivante, reconnecte si nécessaire."""
        try:
            self.imap.noop()
            return True
        except Exception:
            print("🔄 Reconnexion IMAP...")
            return self._reconnect()

    def _should_ignore(self, sender_email: str, subject: str, raw_msg) -> bool:
        s = sender_email.lower()
        sub = subject.lower()

        for kw in self.IGNORE_SENDERS:
            if kw in s:
                return True
        for kw in self.IGNORE_SUBJECTS:
            if kw in sub:
                return True

        # Headers typiques des newsletters
        if raw_msg.get("List-Unsubscribe"):
            return True
        if raw_msg.get("Precedence", "").lower() in ("bulk", "list", "junk"):
            return True
        if raw_msg.get("X-Mailer") or raw_msg.get("X-Newsletter"):
            return True

        # Ne jamais se répondre à soi-même
        if sender_email.lower() == self.cfg["email"].lower():
            return True

        return False

    def fetch_unread(self, max_emails: int = 10) -> list[EmailMessage]:
        if not self._ping():
            return []
        try:
            self.imap.select("INBOX")
            _, uids = self.imap.search(None, "UNSEEN")
            if not uids[0]:
                print("📭 Aucun email non lu")
                return []

            uid_list = uids[0].split()[-max_emails:]
            print(f"📬 {len(uid_list)} email(s) non lu(s) — filtrage en cours...")
            messages = []
            ignored  = 0

            for uid in uid_list:
                try:
                    _, data = self.imap.fetch(uid, "(RFC822)")
                    if not data or not data[0]:
                        continue
                    raw = data[0][1]
                    msg = email.message_from_bytes(raw)

                    subject      = decode_mime_header(msg.get("Subject", "(sans objet)"))
                    from_raw     = msg.get("From", "")
                    email_match  = re.findall(r'<(.+?)>', from_raw)
                    sender_email = email_match[0].strip() if email_match else from_raw.strip()
                    sender_name  = decode_mime_header(from_raw.split("<")[0].strip().strip('"')) if "<" in from_raw else decode_mime_header(from_raw)

                    if self._should_ignore(sender_email, subject, msg):
                        print(f"   🚫 Ignoré : '{subject}' de {sender_email}")
                        self.mark_read(uid.decode())
                        ignored += 1
                        continue

                    body = self._extract_body(msg)

                    messages.append(EmailMessage(
                        uid=uid.decode(),
                        sender=sender_name,
                        sender_email=sender_email,
                        subject=subject,
                        body=body,
                        date=msg.get("Date", ""),
                    ))
                except Exception as e:
                    print(f"⚠️ Erreur lecture uid {uid} : {e}")

            print(f"   ✅ {len(messages)} à traiter | 🚫 {ignored} ignoré(s)")
            return messages

        except Exception as e:
            print(f"❌ Erreur fetch : {e}")
            self.imap = None  # Force reconnexion au prochain cycle
            return []

    def _extract_body(self, msg) -> str:
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get("Content-Disposition", ""))
                if ct == "text/plain" and "attachment" not in cd:
                    try:
                        charset = part.get_content_charset() or "utf-8"
                        body = part.get_payload(decode=True).decode(charset, errors="replace")
                        break
                    except Exception:
                        pass
        else:
            try:
                charset = msg.get_content_charset() or "utf-8"
                body = msg.get_payload(decode=True).decode(charset, errors="replace")
            except Exception:
                body = str(msg.get_payload())

        # Supprime le HTML résiduel
        body = re.sub(r'<[^>]+>', ' ', body)
        # Supprime les lignes de citation email (> texte)
        body = re.sub(r'^>.*$', '', body, flags=re.MULTILINE)
        # Nettoie les espaces multiples
        body = re.sub(r'\n{3,}', '\n\n', body)
        return clean_text(body)[:3000]

    def send(self, to: str, subject: str, body: str) -> bool:
        subj = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
        try:
            sendgrid_key = CONFIG.get("sendgrid_api_key")
            if sendgrid_key:
                from sendgrid import SendGridAPIClient
                from sendgrid.helpers.mail import Mail
                message = Mail(
                    from_email=self.cfg["email"],
                    to_emails=to,
                    subject=subj,
                    plain_text_content=body,
                )
                sg = SendGridAPIClient(sendgrid_key)
                response = sg.send(message)
                if response.status_code in (200, 202):
                    print(f"   ✅ Email envoyé via SendGrid à {to}")
                    return True
                else:
                    print(f"   ❌ SendGrid status : {response.status_code}")
                    return False
            else:
                import smtplib
                msg = MIMEMultipart()
                msg["From"]    = self.cfg["email"]
                msg["To"]      = to
                msg["Subject"] = subj
                msg.attach(MIMEText(body, "plain", "utf-8"))
                with smtplib.SMTP(self.cfg["smtp_host"], self.cfg["smtp_port"]) as s:
                    s.ehlo()
                    s.starttls()
                    s.ehlo()
                    s.login(self.cfg["email"], self.cfg["password"])
                    s.send_message(msg)
                print(f"   ✅ Email envoyé via Gmail à {to}")
                return True
        except Exception as e:
            print(f"   ❌ Erreur envoi : {e}")
            return False

    def create_draft(self, to: str, subject: str, body: str) -> bool:
        try:
            msg = MIMEMultipart()
            msg["From"]    = self.cfg["email"]
            msg["To"]      = to
            msg["Subject"] = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            if not self._ping():
                return False
            self.imap.append(
                "[Gmail]/Drafts", "\\Draft",
                imaplib.Time2Internaldate(time.time()),
                msg.as_bytes()
            )
            print(f"   📝 Brouillon créé pour {to}")
            return True
        except Exception as e:
            print(f"   ❌ Erreur brouillon : {e}")
            return False

    def mark_read(self, uid: str):
        try:
            self.imap.store(uid, "+FLAGS", "\\Seen")
        except Exception:
            pass

    def disconnect(self):
        try:
            if self.imap:
                self.imap.logout()
        except Exception:
            pass

# ─────────────────────────────────────────────────────
# MOTEUR IA
# ─────────────────────────────────────────────────────

class AIEngine:

    MODELS = [
        "llama-3.1-8b-instant",     # 500 000 tokens/jour
        "gemma2-9b-it",             # 500 000 tokens/jour
        "mixtral-8x7b-32768",       # 500 000 tokens/jour
        "llama-3.3-70b-versatile",  # 100 000 tokens/jour
    ]

    def __init__(self):
        self.client      = Groq(api_key=CONFIG["groq_api_key"])
        self.model_index = 0

    def _current_model(self) -> str:
        return self.MODELS[self.model_index]

    def _next_model(self) -> str:
        self.model_index = (self.model_index + 1) % len(self.MODELS)
        model = self.MODELS[self.model_index]
        print(f"   🔄 Bascule vers : {model}")
        return model

    def _default_response(self) -> dict:
        return {
            "urgency_score": 5,
            "category":      "Autre",
            "needs_form":    False,
            "form_type":     "",
            "response":      (
                f"Bonjour,\n\n"
                f"Nous avons bien reçu votre message et nous vous répondrons très rapidement.\n\n"
                f"Cordialement,\nL'équipe {CONFIG['company_name']}"
            ),
        }

    def _parse_raw(self, raw: str) -> dict:
        if not raw:
            raise ValueError("Réponse vide de l'IA")

        # Extrait le bloc JSON des balises markdown
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        # Isole uniquement le bloc { ... }
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

        # Supprime les caractères de contrôle invalides dans le JSON
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', raw)
        raw = raw.replace('\r', '')

        data = json.loads(raw)

        # Valide les champs obligatoires
        if "urgency_score" not in data or "response" not in data:
            raise ValueError("JSON incomplet")

        return data

    def analyze(self, msg: EmailMessage) -> EmailMessage:
        print(f"\n🤖 Analyse : '{msg.subject}' (de {msg.sender})")

        # Nettoie tout le contenu avant injection dans le prompt
        subject = clean_for_prompt(msg.subject)
        body    = clean_for_prompt(msg.body)
        sender  = clean_for_prompt(msg.sender)
        company = clean_for_prompt(CONFIG['company_name'])
        context = clean_for_prompt(CONFIG['company_context'])

        prompt = (
            f"Tu es l assistant email officiel de {company}.\n\n"
            f"CONTEXTE DE L ENTREPRISE :\n{context}\n\n"
            f"EMAIL RECU :\n"
            f"De      : {sender} ({msg.sender_email})\n"
            f"Sujet   : {subject}\n"
            f"Contenu : {body}\n\n"
            f"INSTRUCTIONS :\n"
            f"Reponds UNIQUEMENT en JSON valide avec ce format exact :\n"
            f"{{\n"
            f'  "urgency_score": <entier 1-10>,\n'
            f'  "category": "<Devis|RDV|SAV|Information|Spam|Autre>",\n'
            f'  "needs_form": <true ou false>,\n'
            f'  "form_type": "<devis|rdv|sav|info|null>",\n'
            f'  "reasoning": "<explication en 1 phrase>",\n'
            f'  "response": "<reponse email complete>"\n'
            f"}}\n\n"
            f"REGLE needs_form = true si email vague :\n"
            f"- Devis sans details du service → form_type devis\n"
            f"- RDV sans date ni objet → form_type rdv\n"
            f"- SAV sans description → form_type sav\n"
            f"- Question trop vague → form_type info\n\n"
            f"STYLE DE REPONSE :\n"
            f"- Francais uniquement\n"
            f"- Vouvoiement OBLIGATOIRE\n"
            f"- Commence par Bonjour [Prenom],\n"
            f"- 3 paragraphes : accuse reception / action / invitation\n"
            f"- Si needs_form true : mentionner le formulaire rapide (2 minutes)\n"
            f"- Signature : Cordialement, L equipe {company}\n"
            f"- JAMAIS d emoji dans la signature\n\n"
            f"URGENCE : 8-10 urgent | 5-7 normal | 1-4 faible"
        )

        result = None

        for attempt in range(len(self.MODELS)):
            try:
                model = self._current_model()
                resp  = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1000,
                    temperature=0.2,
                )
                raw    = resp.choices[0].message.content.strip()
                result = self._parse_raw(raw)
                print(f"   ✅ Modèle : {model}")
                break
            except Exception as e:
                err = str(e)
                if "429" in err or "rate_limit" in err.lower():
                    print(f"   ⚠️ {self._current_model()} épuisé — bascule...")
                    self._next_model()
                    time.sleep(2)
                else:
                    print(f"   ⚠️ Erreur ({e}) — retry...")
                    time.sleep(3)

        if result is None:
            print("   ❌ Tous les modèles épuisés — réponse par défaut")
            result = self._default_response()

        msg.urgency_score      = int(result.get("urgency_score", 5))
        msg.category           = result.get("category", "Autre")
        msg.needs_form         = bool(result.get("needs_form", False))
        msg.form_type          = result.get("form_type") or ""
        msg.suggested_response = result.get("response", "")

        print(f"   📊 Urgence : {msg.urgency_score}/10 | Catégorie : {msg.category} | Formulaire : {msg.needs_form}")

        return msg

# ─────────────────────────────────────────────────────
# ORCHESTRATEUR PRINCIPAL
# ─────────────────────────────────────────────────────

class UTWORLDIA:

    def __init__(self):
        self.ai        = AIEngine()
        self.connector = EmailConnector()
        self.stats     = {"total": 0, "sent": 0, "drafts": 0, "forms": 0, "errors": 0}

    def process(self, msg: EmailMessage) -> str:
        msg = self.ai.analyze(msg)

        if not msg.suggested_response:
            self.stats["errors"] += 1
            self.connector.mark_read(msg.uid)
            return "error"

        if msg.needs_form and msg.form_type:
            try:
                from form_server import create_form_session
                form_link = create_form_session(
                    form_type        = msg.form_type,
                    client_email     = msg.sender_email,
                    client_name      = msg.sender,
                    original_subject = msg.subject,
                )
                msg.suggested_response += (
                    f"\n\nVoici le lien vers votre formulaire :\n"
                    f"{form_link}\n\n"
                    f"Dès que vous l'aurez complété, vous recevrez une réponse personnalisée automatiquement."
                )
                print(f"   📋 Formulaire : {form_link}")
            except ImportError:
                print("   ⚠️ form_server non disponible — réponse directe")
                msg.needs_form = False

            self.connector.send(msg.sender_email, msg.subject, msg.suggested_response)
            self.stats["forms" if msg.needs_form else "sent"] += 1
            msg.action = "form" if msg.needs_form else "auto_send"

        else:
            if msg.urgency_score >= CONFIG["urgency_threshold"]:
                self.connector.send(msg.sender_email, msg.subject, msg.suggested_response)
                self.stats["sent"] += 1
                msg.action = "auto_send"
            else:
                self.connector.create_draft(msg.sender_email, msg.subject, msg.suggested_response)
                self.stats["drafts"] += 1
                msg.action = "draft"

        self.connector.mark_read(msg.uid)
        self.stats["total"] += 1
        return msg.action

    def run_once(self):
        print(f"\n{'='*52}")
        print(f"⚡ UTWORLDIA — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        print(f"{'='*52}")

        messages = self.connector.fetch_unread()
        for msg in messages:
            self.process(msg)

        print(f"\n📊 Total : {self.stats['total']} | "
              f"✅ Envoyés : {self.stats['sent']} | "
              f"📝 Brouillons : {self.stats['drafts']} | "
              f"📋 Formulaires : {self.stats['forms']} | "
              f"❌ Erreurs : {self.stats['errors']}")

    def run_forever(self):
        try:
            from form_server import start_server
            t = threading.Thread(target=start_server, args=(8080,), daemon=True)
            t.start()
        except ImportError:
            print("⚠️ form_server.py non trouvé — formulaires désactivés")

        print("🚀 UTWORLDIA démarré en mode continu")
        print(f"⏱️  Vérification toutes les {CONFIG['check_interval']}s")
        print(f"🌐 Formulaires : {CONFIG['base_url']}")
        print("   Ctrl+C pour arrêter\n")

        while True:
            try:
                self.run_once()
                print(f"\n⏳ Prochaine vérification dans {CONFIG['check_interval']}s...")
                time.sleep(CONFIG["check_interval"])
            except KeyboardInterrupt:
                print("\n\n👋 UTWORLDIA arrêté.")
                self.connector.disconnect()
                break
            except Exception as e:
                print(f"\n❌ Erreur inattendue : {e}")
                time.sleep(30)

# ─────────────────────────────────────────────────────
# LANCEMENT
# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    bot = UTWORLDIA()
    bot.run_forever()
