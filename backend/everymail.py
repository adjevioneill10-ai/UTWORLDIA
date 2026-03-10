"""
UTWORLDIA — Bot Email Intelligent
Analyse les emails entrants, détecte le type de demande,
envoie un formulaire si nécessaire ou répond directement.
"""

import imaplib
import smtplib
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
from dataclasses import dataclass

load_dotenv("../config/.env")

# ─────────────────────────────────────────────────────
# CONFIGURATION — Modifie ici pour chaque client
# ─────────────────────────────────────────────────────

CONFIG = {
    # ── IA ──────────────────────────────────────────
    "groq_api_key": os.getenv("GROQ_API_KEY"),

    # ── Email du client ──────────────────────────────
    "gmail": {
        "email":       os.getenv("GMAIL_EMAIL"),
        "password":    os.getenv("GMAIL_APP_PASSWORD"),
        "imap_host":   "imap.gmail.com",
        "smtp_host":   "smtp.gmail.com",
        "smtp_port":   587,
    },

    # ── Identité de l'entreprise ─────────────────────
    "company_name":    os.getenv("COMPANY_NAME", "UTWORLDIA"),
    "company_context": os.getenv("COMPANY_CONTEXT", "Agence d'automatisation IA pour PME."),
    "base_url":        os.getenv("BASE_URL", "http://localhost:8080"),

    # ── Comportement ────────────────────────────────
    "urgency_threshold": 7,
    "check_interval": 60,
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
# CONNEXION EMAIL (IMAP / SMTP)
# ─────────────────────────────────────────────────────

class EmailConnector:

    def __init__(self):
        self.cfg  = CONFIG["gmail"]
        self.imap = None

    def connect(self) -> bool:
        try:
            self.imap = imaplib.IMAP4_SSL(self.cfg["imap_host"])
            self.imap.login(self.cfg["email"], self.cfg["password"])
            print("✅ Gmail connecté")
            return True
        except Exception as e:
            print(f"❌ Connexion Gmail échouée : {e}")
            return False

    IGNORE_SENDERS = [
        "noreply", "no-reply", "donotreply", "do-not-reply",
        "newsletter", "mailer", "notification", "notifications",
        "automated", "automatique", "bounce", "postmaster",
        "support@github", "info@linkedin", "member@linkedin",
    ]

    IGNORE_SUBJECTS = [
        "unsubscribe", "se désabonner", "newsletter",
        "promotion", "offre spéciale", "soldes",
        "nouvel horaire", "nouveau programme", "planning",
        "votre facture", "your invoice", "reçu de paiement",
    ]

    IGNORE_HEADERS = [
        "list-unsubscribe", "x-mailer", "x-newsletter",
        "precedence: bulk", "precedence: list",
    ]

    def _should_ignore(self, sender_email: str, subject: str, raw_msg) -> bool:
        sender_lower  = sender_email.lower()
        subject_lower = subject.lower()

        for keyword in self.IGNORE_SENDERS:
            if keyword in sender_lower:
                return True

        for keyword in self.IGNORE_SUBJECTS:
            if keyword in subject_lower:
                return True

        for header in self.IGNORE_HEADERS:
            if raw_msg.get(header.split(":")[0], "").lower():
                return True

        own_email = self.cfg["email"].lower()
        if sender_email.lower() == own_email:
            return True

        return False

    def fetch_unread(self, max_emails: int = 20) -> list[EmailMessage]:
        if not self.imap:
            if not self.connect():
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
                    raw = data[0][1]
                    msg = email.message_from_bytes(raw)

                    subj_raw = decode_header(msg["Subject"])[0]
                    subject  = subj_raw[0].decode(subj_raw[1] or "utf-8") if isinstance(subj_raw[0], bytes) else (subj_raw[0] or "(sans objet)")

                    from_raw     = msg.get("From", "")
                    email_match  = re.findall(r'<(.+?)>', from_raw)
                    sender_email = email_match[0] if email_match else from_raw
                    sender_name  = from_raw.split("<")[0].strip().strip('"') if "<" in from_raw else from_raw

                    if self._should_ignore(sender_email, subject, msg):
                        print(f"   🚫 Ignoré (auto/newsletter) : '{subject}' de {sender_email}")
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
            return []

    def _extract_body(self, msg) -> str:
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
                    except:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
            except:
                body = str(msg.get_payload())
        body = re.sub(r'<[^>]+>', '', body)
        return body.strip()[:3000]

    def send(self, to: str, subject: str, body: str) -> bool:
        try:
            msg = MIMEMultipart()
            msg["From"]    = self.cfg["email"]
            msg["To"]      = to
            msg["Subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self.cfg["smtp_host"], self.cfg["smtp_port"]) as s:
                s.starttls()
                s.login(self.cfg["email"], self.cfg["password"])
                s.send_message(msg)
            print(f"   ✅ Email envoyé à {to}")
            return True
        except Exception as e:
            print(f"   ❌ Erreur envoi : {e}")
            return False

    def create_draft(self, to: str, subject: str, body: str) -> bool:
        try:
            msg = MIMEMultipart()
            msg["From"]    = self.cfg["email"]
            msg["To"]      = to
            msg["Subject"] = f"Re: {subject}"
            msg.attach(MIMEText(body, "plain", "utf-8"))

            if not self.imap:
                self.connect()
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
        except:
            pass

    def disconnect(self):
        try:
            if self.imap:
                self.imap.logout()
        except:
            pass

# ─────────────────────────────────────────────────────
# MOTEUR IA
# ─────────────────────────────────────────────────────

class AIEngine:

    def __init__(self):
        self.client = Groq(api_key=CONFIG["groq_api_key"])

    def _default_response(self) -> dict:
        return {
            "urgency_score": 5,
            "category": "Autre",
            "needs_form": False,
            "form_type": "",
            "suggested_response": f"Bonjour,\n\nNous avons bien reçu votre message et nous vous répondrons très rapidement.\n\nCordialement,\nL'équipe {CONFIG['company_name']}",
        }

    def _parse_raw(self, raw: str) -> dict:
        if not raw:
            raise ValueError("Réponse vide de l'IA")
        # Extrait le JSON des balises markdown
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        # Isole uniquement le bloc JSON { ... }
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]
        # Supprime tous les caractères de contrôle invalides
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', raw)
        raw = raw.replace('\r', '')
        return json.loads(raw)

    def analyze(self, msg: EmailMessage) -> EmailMessage:
        print(f"\n🤖 Analyse : '{msg.subject}' (de {msg.sender})")

        prompt = f"""Tu es l'assistant email officiel de {CONFIG['company_name']}.

CONTEXTE DE L'ENTREPRISE :
{CONFIG['company_context']}

EMAIL REÇU :
De      : {msg.sender} <{msg.sender_email}>
Sujet   : {msg.subject}
Contenu : {msg.body}

INSTRUCTIONS :
Réponds UNIQUEMENT en JSON avec ce format exact :
{{
  "urgency_score": <entier 1-10>,
  "category": "<Devis|RDV|SAV|Information|Spam|Autre>",
  "needs_form": <true ou false>,
  "form_type": "<devis|rdv|sav|info|null>",
  "reasoning": "<explication en 1 phrase>",
  "response": "<réponse email complète>"
}}

RÈGLE needs_form = TRUE si l'email est vague et nécessite plus d'infos :
- Demande de devis sans préciser le service ou budget → form_type = "devis"
- Demande de RDV sans date/heure/objet → form_type = "rdv"
- Problème SAV sans description précise → form_type = "sav"
- Question générale trop vague → form_type = "info"

RÈGLE needs_form = FALSE si l'email est clair et complet → on répond directement.

STYLE DE RÉPONSE — RÈGLES STRICTES :
- Langue : français uniquement
- Vouvoiement OBLIGATOIRE : toujours "vous", jamais "tu"
- Commence TOUJOURS par "Bonjour [Prénom]," sur sa propre ligne
- Une ligne vide entre chaque paragraphe
- Structure en 3 paragraphes :
  1. Accusé de réception professionnel et chaleureux
  2. Explication claire de ce qu'on va faire
  3. Invitation à agir
- Si needs_form = TRUE : écrire dans le 2ème paragraphe :
  "Afin de vous préparer une réponse précise et personnalisée, nous vous invitons à compléter notre formulaire rapide (2 minutes)."
- Si needs_form = FALSE : répondre directement et complètement
- Signature EXACTE en fin d'email :
  "Cordialement,
  L'équipe {CONFIG['company_name']}"
- JAMAIS d'emoji dans la signature
- JAMAIS mélanger "vous" et "tu"

URGENCE :
- 8-10 : Urgent (client bloqué, bug critique, plainte)
- 5-7  : Normal (devis, RDV, question)
- 1-4  : Basse priorité (info générale, spam probable)"""

        result = None

        # Tentative 1
        try:
            resp = self.client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
                temperature=0.3,
            )
            raw = resp.choices[0].message.content.strip()
            result = self._parse_raw(raw)
        except Exception as e:
            print(f"   ⚠️ Tentative 1 échouée ({e}) — nouvelle tentative...")
            time.sleep(3)

        # Tentative 2
        if result is None:
            try:
                resp2 = self.client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=800,
                    temperature=0.1,
                )
                raw2 = resp2.choices[0].message.content.strip()
                result = self._parse_raw(raw2)
            except Exception as e2:
                print(f"   ❌ Tentative 2 échouée ({e2}) — réponse par défaut")
                result = self._default_response()

        # Applique le résultat
        msg.urgency_score      = int(result.get("urgency_score", 5))
        msg.category           = result.get("category", "Autre")
        msg.needs_form         = bool(result.get("needs_form", False))
        msg.form_type          = result.get("form_type") or ""
        msg.suggested_response = result.get("response") or result.get("suggested_response", "")

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
        # 1. Analyse IA
        msg = self.ai.analyze(msg)

        if not msg.suggested_response:
            self.stats["errors"] += 1
            return "error"

        # 2. Si l'email est vague → envoie un formulaire
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
                    f"\n\n"
                    f"Voici le lien vers votre formulaire :\n"
                    f"{form_link}\n\n"
                    f"Dès que vous l'aurez complété, vous recevrez une réponse personnalisée automatiquement."
                )
                print(f"   📋 Formulaire : {form_link}")
                self.connector.send(msg.sender_email, msg.subject, msg.suggested_response)
                self.stats["forms"] += 1
                msg.action = "form"
            except ImportError:
                print("   ⚠️ form_server non disponible — réponse directe")
                self.connector.send(msg.sender_email, msg.subject, msg.suggested_response)
                self.stats["sent"] += 1
                msg.action = "auto_send"

        # 3. Email complet → envoi auto ou brouillon selon urgence
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