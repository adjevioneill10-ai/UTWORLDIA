"""
UTWORLDIA — Bot Email Intelligent
Production-ready + intégration Calendly
"""

import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from groq import Groq
from dotenv import load_dotenv
import os
import re
import time
import threading
from datetime import datetime
from dataclasses import dataclass

load_dotenv("../config/.env")

# ─────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────

GROQ_KEY     = os.getenv("GROQ_API_KEY")
GMAIL_EMAIL  = os.getenv("GMAIL_EMAIL")
GMAIL_PASS   = os.getenv("GMAIL_APP_PASSWORD")
BREVO_KEY    = os.getenv("BREVO_API_KEY")
CALENDLY     = os.getenv("CALENDLY_LINK", "")
COMPANY      = os.getenv("COMPANY_NAME", "UTWORLDIA")
CONTEXT      = os.getenv("COMPANY_CONTEXT", "Agence d automatisation IA pour PME belges.")
BASE_URL     = os.getenv("BASE_URL", "http://localhost:8080")
THRESHOLD    = 7
INTERVAL     = 60

# ─────────────────────────────────────────────────────
# FILTRES
# ─────────────────────────────────────────────────────

IGNORE_SENDERS = [
    "noreply@", "no-reply@", "donotreply@",
    "mailer-daemon@", "postmaster@", "bounce@",
    "newsletter@", "notifications@", "automated@",
]

IGNORE_SUBJECTS = [
    "unsubscribe", "se désabonner",
    "password reset", "réinitialisation du mot de passe",
    "verify your email", "confirm your email",
]

# ─────────────────────────────────────────────────────
# STRUCTURE
# ─────────────────────────────────────────────────────

@dataclass
class Mail:
    uid:        str
    sender:     str
    from_email: str
    subject:    str
    body:       str

# ─────────────────────────────────────────────────────
# IMAP
# ─────────────────────────────────────────────────────

_imap = None

def imap_connect() -> bool:
    global _imap
    try:
        if _imap:
            try: _imap.logout()
            except: pass
        _imap = imaplib.IMAP4_SSL("imap.gmail.com")
        _imap.login(GMAIL_EMAIL, GMAIL_PASS)
        print("✅ IMAP connecté")
        return True
    except Exception as e:
        print(f"❌ IMAP erreur : {e}")
        _imap = None
        return False

def imap_ping() -> bool:
    global _imap
    try:
        _imap.noop()
        return True
    except:
        return imap_connect()

def decode_str(raw) -> str:
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
        return result.strip()
    except:
        return str(raw).strip()

def should_ignore(from_email: str, subject: str, msg) -> str:
    fe  = from_email.lower()
    sub = subject.lower()
    for kw in IGNORE_SENDERS:
        if kw in fe:
            return f"sender:{kw}"
    for kw in IGNORE_SUBJECTS:
        if kw in sub:
            return f"subject:{kw}"
    if msg.get("List-Unsubscribe"):
        return "header:List-Unsubscribe"
    if msg.get("Precedence", "").lower() in ("bulk", "list", "junk"):
        return "header:Precedence"
    if from_email.lower() == GMAIL_EMAIL.lower():
        return "self-reply"
    return ""

def extract_body(msg) -> str:
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
                except:
                    pass
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            body = msg.get_payload(decode=True).decode(charset, errors="replace")
        except:
            body = str(msg.get_payload())
    body = re.sub(r'<[^>]+>', ' ', body)
    body = re.sub(r'^>.*$', '', body, flags=re.MULTILINE)
    body = re.sub(r'\n{3,}', '\n\n', body)
    return body.strip()[:2000]

def fetch_unread() -> list:
    if not imap_ping():
        return []
    try:
        _imap.select("INBOX")
        _, uids = _imap.search(None, "UNSEEN")
        if not uids[0]:
            print("📭 Aucun email non lu")
            return []

        uid_list = uids[0].split()[-10:]
        print(f"📬 {len(uid_list)} email(s) non lu(s) — filtrage...")
        mails   = []
        ignored = 0

        for uid in uid_list:
            try:
                _, data = _imap.fetch(uid, "(RFC822)")
                if not data or not data[0]:
                    continue
                raw = data[0][1]
                msg = email.message_from_bytes(raw)

                subject    = decode_str(msg.get("Subject", "(sans objet)"))
                from_raw   = msg.get("From", "")
                match      = re.findall(r'<(.+?)>', from_raw)
                from_email = match[0].strip() if match else from_raw.strip()
                sender     = decode_str(
                    from_raw.split("<")[0].strip().strip('"')
                ) if "<" in from_raw else from_raw.strip()

                reason = should_ignore(from_email, subject, msg)
                if reason:
                    print(f"   🚫 Ignoré ({reason}) : '{subject}' de {from_email}")
                    mark_read(uid.decode())
                    ignored += 1
                    continue

                body = extract_body(msg)
                print(f"   📧 Retenu : '{subject}' de {from_email}")
                mails.append(Mail(
                    uid=uid.decode(),
                    sender=sender,
                    from_email=from_email,
                    subject=subject,
                    body=body,
                ))
            except Exception as e:
                print(f"   ⚠️ Erreur uid {uid} : {e}")

        print(f"   → {len(mails)} à traiter | {ignored} ignoré(s)")
        return mails

    except Exception as e:
        print(f"❌ Fetch erreur : {e}")
        imap_connect()
        return []

def mark_read(uid: str):
    try: _imap.store(uid, "+FLAGS", "\\Seen")
    except: pass

def create_draft(to: str, subject: str, body: str) -> bool:
    subj = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_EMAIL
        msg["To"]      = to
        msg["Subject"] = subj
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if not imap_ping():
            return False
        _imap.append(
            "[Gmail]/Brouillons", "\\Draft",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes()
        )
        print(f"   📝 Brouillon créé pour {to}")
        return True
    except Exception as e:
        print(f"   ❌ Brouillon erreur : {e}")
        return False

# ─────────────────────────────────────────────────────
# ENVOI EMAIL
# ─────────────────────────────────────────────────────

def send_email(to: str, subject: str, body: str) -> bool:
    subj = f"Re: {subject}" if not subject.lower().startswith("re:") else subject

    if BREVO_KEY:
        try:
            import urllib.request, json as _j
            payload = _j.dumps({
                "sender":      {"name": COMPANY, "email": GMAIL_EMAIL},
                "to":          [{"email": to}],
                "subject":     subj,
                "textContent": body,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.brevo.com/v3/smtp/email",
                data=payload,
                headers={"api-key": BREVO_KEY, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status in (200, 201):
                    print(f"   ✅ Envoyé via Brevo à {to}")
                    return True
        except Exception as e:
            print(f"   ❌ Brevo erreur : {e}")

    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_EMAIL
        msg["To"]      = to
        msg["Subject"] = subj
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_EMAIL, GMAIL_PASS)
            s.send_message(msg)
        print(f"   ✅ Envoyé via Gmail à {to}")
        return True
    except Exception as e:
        print(f"   ❌ Gmail erreur : {e}")
        return False

# ─────────────────────────────────────────────────────
# MOTEUR IA
# ─────────────────────────────────────────────────────

_groq      = Groq(api_key=GROQ_KEY)
_models    = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]
_model_idx = 0

def clean(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
    return text.strip()[:1500]

def ai_analyze(mail: Mail) -> dict:
    global _model_idx
    print(f"\n🤖 Analyse : '{mail.subject}' (de {mail.sender})")

    subject = clean(mail.subject)
    body    = clean(mail.body)
    sender  = clean(mail.sender)

    calendly_info = (
        f"- Si CATEGORIE=RDV, inclus ce lien dans la reponse : {CALENDLY}\n"
        if CALENDLY else ""
    )

    prompt = (
        f"Tu es le gestionnaire email de {COMPANY}.\n"
        f"Contexte : {CONTEXT}\n\n"
        f"EMAIL RECU :\n"
        f"De : {sender} ({mail.from_email})\n"
        f"Sujet : {subject}\n"
        f"Message : {body}\n\n"
        f"Reponds avec EXACTEMENT ce format (une valeur par ligne) :\n"
        f"SCORE: [1-10]\n"
        f"CATEGORIE: [Devis ou RDV ou SAV ou Information ou Autre]\n"
        f"FORMULAIRE: [oui ou non]\n"
        f"TYPE: [devis ou rdv ou sav ou info ou aucun]\n"
        f"REPONSE: [email complet en francais]\n\n"
        f"REGLES :\n"
        f"- FORMULAIRE=oui si la demande manque de details\n"
        f"- FORMULAIRE=non si tu peux repondre directement\n"
        f"- SCORE 8-10 = urgent, 5-7 = normal, 1-4 = faible\n"
        f"- Commence par : Bonjour {sender},\n"
        f"- Vouvoiement OBLIGATOIRE\n"
        f"- Signature : Cordialement, L equipe {COMPANY}\n"
        f"- JAMAIS d emojis dans la signature\n"
        f"{calendly_info}"
    )

    for _ in range(len(_models)):
        try:
            model = _models[_model_idx]
            resp  = _groq.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
                temperature=0.2,
            )
            raw = resp.choices[0].message.content.strip()
            print(f"   ✅ Modèle : {model}")
            return parse_ai(raw)
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                _model_idx = (_model_idx + 1) % len(_models)
                print(f"   ⚠️ Limite — bascule vers {_models[_model_idx]}")
                time.sleep(2)
            else:
                print(f"   ⚠️ IA erreur : {e}")
                time.sleep(3)
                break

    print("   ❌ IA indisponible — réponse par défaut")
    return default_response()

def parse_ai(raw: str) -> dict:
    result = {"score": 5, "category": "Autre", "needs_form": False, "form_type": "", "response": ""}
    lines  = raw.strip().splitlines()
    resp_lines  = []
    in_response = False

    for line in lines:
        line = line.strip()
        if line.startswith("SCORE:"):
            try: result["score"] = int(re.search(r"\d+", line).group())
            except: pass
        elif line.startswith("CATEGORIE:"):
            result["category"] = line.split(":", 1)[1].strip()
        elif line.startswith("FORMULAIRE:"):
            val = line.split(":", 1)[1].strip().lower()
            result["needs_form"] = val in ("oui", "true", "yes")
        elif line.startswith("TYPE:"):
            val = line.split(":", 1)[1].strip().lower()
            result["form_type"] = "" if val in ("aucun", "null", "none") else val
        elif line.startswith("REPONSE:"):
            in_response = True
            resp_lines.append(line.split(":", 1)[1].strip())
        elif in_response:
            resp_lines.append(line)

    result["response"] = "\n".join(resp_lines).strip()
    if not result["response"]:
        raise ValueError("Réponse IA vide")
    return result

def default_response() -> dict:
    return {
        "score": 5,
        "category": "Autre",
        "needs_form": False,
        "form_type": "",
        "response": (
            f"Bonjour,\n\n"
            f"Nous avons bien reçu votre message et nous vous répondrons très rapidement.\n\n"
            f"Cordialement,\nL'équipe {COMPANY}"
        ),
    }

# ─────────────────────────────────────────────────────
# ORCHESTRATEUR
# ─────────────────────────────────────────────────────

_stats = {"total": 0, "sent": 0, "drafts": 0, "forms": 0, "errors": 0}

def process(mail: Mail):
    result = ai_analyze(mail)
    print(f"   📊 Score:{result['score']}/10 | {result['category']} | Formulaire:{result['needs_form']}")

    if not result["response"]:
        _stats["errors"] += 1
        mark_read(mail.uid)
        return

    body = result["response"]

    # Ajoute lien formulaire si nécessaire
    if result["needs_form"] and result["form_type"]:
        try:
            from form_server import create_form_session
            link = create_form_session(
                form_type        = result["form_type"],
                client_email     = mail.from_email,
                client_name      = mail.sender,
                original_subject = mail.subject,
            )
            body += f"\n\nVoici votre formulaire :\n{link}\n"
            print(f"   📋 Formulaire : {link}")
        except ImportError:
            result["needs_form"] = False

    # Envoie ou brouillon selon le score
    if result["score"] >= THRESHOLD or result["needs_form"]:
        if send_email(mail.from_email, mail.subject, body):
            _stats["forms" if result["needs_form"] else "sent"] += 1
        else:
            create_draft(mail.from_email, mail.subject, body)
            _stats["drafts"] += 1
    else:
        create_draft(mail.from_email, mail.subject, body)
        _stats["drafts"] += 1

    mark_read(mail.uid)
    _stats["total"] += 1

def run_once():
    print(f"\n{'='*50}")
    print(f"⚡ UTWORLDIA — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*50}")
    mails = fetch_unread()
    for mail in mails:
        process(mail)
    print(
        f"\n📊 Total:{_stats['total']} | "
        f"✅ Envoyés:{_stats['sent']} | "
        f"📝 Brouillons:{_stats['drafts']} | "
        f"📋 Formulaires:{_stats['forms']} | "
        f"❌ Erreurs:{_stats['errors']}"
    )

def run_forever():
    try:
        from form_server import start_server
        threading.Thread(target=start_server, args=(8080,), daemon=True).start()
        print("🌐 Serveur formulaires démarré")
    except ImportError:
        print("⚠️ form_server non trouvé — formulaires désactivés")

    imap_connect()

    print(f"🚀 UTWORLDIA démarré — vérification toutes les {INTERVAL}s")
    print(f"📧 Compte : {GMAIL_EMAIL}")
    print(f"🏢 Client : {COMPANY}")
    if CALENDLY:
        print(f"📅 Calendly : {CALENDLY}")
    print()

    while True:
        try:
            run_once()
            print(f"\n⏳ Prochaine vérification dans {INTERVAL}s...")
            time.sleep(INTERVAL)
        except KeyboardInterrupt:
            print("\n👋 UTWORLDIA arrêté.")
            try: _imap.logout()
            except: pass
            break
        except Exception as e:
            print(f"❌ Erreur inattendue : {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_forever()
