"""
CardioGuard — Surveillance Cardiaque Intelligente
VERSION FINALE : Topics MQTT dynamiques (ecg/data/CODE et ecg/alert/CODE)
"""
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_socketio import SocketIO
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
import paho.mqtt.client as mqtt
import json, time, threading, logging, os, smtplib, secrets, sqlite3
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from database import (init_db, get_user_by_email, get_user_by_id,
                      create_user, get_patient_by_code, get_all_patients,
                      get_patients_for_medecin, get_patient_medecin,
                      get_famille_for_patient, get_patient_for_famille,
                      link_famille_to_patient,
                      check_password, save_alert, get_alerts,
                      insert_default_data, create_patient,
                      update_password, archive_patient, restore_patient)

os.makedirs("/home/issra/CardioGuard", exist_ok=True)
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[logging.FileHandler("/home/issra/CardioGuard/app.log"),
              logging.StreamHandler()])
log = logging.getLogger()

GMAIL_USER = "admin.cardioguard@gmail.com"
GMAIL_PASS = " eecn knsd gzyn hrun" 
GMAIL_FROM = "CardioGuard <admin.cardioguard@gmail.com>"
APP_URL    = "https://cardioguard.duckdns.org"

reset_tokens = {}
email_verify_tokens = {}

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cardioguard-secure-2026'

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "Veuillez vous connecter."

socketio = SocketIO(app, cors_allowed_origins="*",
    async_mode='threading', logger=False, engineio_logger=False,
    ping_timeout=60, ping_interval=25)

class User(UserMixin):
    def __init__(self, row):
        self.id           = row['id']
        self.nom          = row['nom']
        self.prenom       = row['prenom']
        self.email        = row['email']
        self.role         = row['role']
        self.patient_code = row['patient_code']
        self.medecin_id   = row['medecin_id']
    def get_id(self): 
        return str(self.id)

@login_manager.user_loader
def load_user(uid):
    row = get_user_by_id(int(uid))
    return User(row) if row else None

def send_email(to, subject, body):
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"]    = GMAIL_FROM
        msg["To"]      = to
        msg.attach(MIMEText(body, "html", "utf-8"))
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.sendmail(GMAIL_USER, [to], msg.as_string())
        log.info(f"✉️ Email envoyé → {to}")
        return True
    except Exception as e:
        log.error(f"❌ Email erreur: {e}")
        return False

def get_patients_for_medecin_with_archived(medecin_id):
    """Récupère les patients actifs ET archivés d'un médecin"""
    conn = sqlite3.connect('/home/issra/CardioGuard/ecg.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM patients WHERE medecin_id=? ORDER BY archived, nom, prenom", (medecin_id,))
    rows = c.fetchall()
    conn.close()
    return rows

MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_USER = "Issra"
MQTT_PASS = "Cg2026SecureMqtt!"
MQTT_OK   = False
patient_data = {}

def get_buf(code):
    if code not in patient_data:
        patient_data[code] = {'total': 0, 'signal_active': False, 'last_time': None}
    return patient_data[code]

def on_connect(c, u, f, rc):
    global MQTT_OK
    MQTT_OK = (rc == 0)
    if rc == 0:
        c.subscribe("ecg/data/#",  qos=0)
        c.subscribe("ecg/alert/#", qos=1)
        log.info("✅ MQTT connecté - Subscribed to ecg/data/# et ecg/alert/#")

def on_disconnect(c, u, rc):
    global MQTT_OK
    MQTT_OK = False

def on_message(c, u, msg):
    try:
        d = json.loads(msg.payload)
        
        # ✅ EXTRAIRE le code du topic (ecg/data/CODE ou ecg/alert/CODE)
        topic_parts = msg.topic.split('/')
        code = (topic_parts[-1].upper() if len(topic_parts) > 2 else None)        
        if not code:
            log.warning(f"⚠️ Topic invalide : {msg.topic}")
            return
        
        log.debug(f"📡 Message reçu pour patient {code} sur {msg.topic}")
        
        if "ecg/data" in msg.topic:
            buf   = get_buf(code)
            batch = d.get("b", [])
            now   = time.time()
            
            if not buf['signal_active']:
                buf['signal_active'] = True
                buf['total'] = 0
                log.info(f"📡 Signal patient {code} ACTIF")
            
            buf['last_time'] = now
            buf['total'] += len(batch)
            socketio.emit('ecg_batch', {'b': batch, 'total': buf['total'], 'code': code})
            
        elif "ecg/alert" in msg.topic:
            proba = d.get('proba', 0)
            
            patient = get_patient_by_code(code)
            if patient:
                medecin = get_patient_medecin(code)
                famille = get_famille_for_patient(code)
                
                alert_time = time.strftime("%H:%M:%S")
                alert_date = time.strftime("%d/%m/%Y")
                
                log.warning(f"🚨 ALERTE pour patient {code} - Proba: {proba}")
                
                # Email au PATIENT
                if patient:
                    body_patient = f"""<div style="font-family:sans-serif;max-width:500px;margin:auto"><h2 style="color:#4f9cf9">💓 CardioGuard — Notification</h2><p>Bonjour <strong>{patient['prenom']} {patient['nom']}</strong>,</p><p style="font-size:16px;margin:20px 0">Une <strong>activité inhabituelle</strong> a été détectée.</p><div style="background:#1e293b;border:2px solid #4f9cf9;border-radius:12px;padding:20px;text-align:center;margin:16px 0"><div style="font-size:14px;color:#94a3b8;margin-bottom:12px">📅 {alert_date} — ⏰ {alert_time}</div></div><p style="font-size:14px;color:#6ee7b7;line-height:1.8">✅ <strong>Les médecins ont été alertés automatiquement</strong> et s'occupent de la situation.<br><strong>Restez calme.</strong></p><a href="{APP_URL}/dashboard/{code}" style="display:inline-block;padding:12px 24px;background:linear-gradient(135deg,#4f9cf9,#7c3aed);color:white;border-radius:8px;text-decoration:none;margin:20px 0">Voir mon dashboard →</a></div>"""
                    send_email(patient.get('email') or 'admin@cardioguard.com', "CardioGuard — Activité détectée", body_patient)
                
                # Email au MÉDECIN
                if medecin:
                    body_medecin = f"""<div style="font-family:sans-serif;max-width:500px;margin:auto"><h2 style="color:#f85149">⚠️ CardioGuard — ALERTE PATIENT</h2><p>Dr. <strong>{medecin['prenom']} {medecin['nom']}</strong>,</p><p style="font-size:16px;margin:20px 0;color:#f85149"><strong>Activité inhabituelle détectée</strong></p><div style="background:#1e293b;border:2px solid #f85149;border-radius:12px;padding:20px;margin:16px 0"><div style="font-size:14px;color:#e6edf3;margin-bottom:12px"><strong>Patient :</strong> {patient['prenom']} {patient['nom']}<br><strong>Code :</strong> {code}<br><strong>Heure :</strong> {alert_time} ({alert_date})</div></div><a href="{APP_URL}/dashboard/{code}" style="display:inline-block;padding:12px 24px;background:#f85149;color:white;border-radius:8px;text-decoration:none;margin:20px 0">Voir le dashboard →</a></div>"""
                    send_email(medecin['email'], "⚠️ CardioGuard — ALERTE PATIENT", body_medecin)
                
                # Email à la FAMILLE
                if famille:
                    body_famille = f"""<div style="font-family:sans-serif;max-width:500px;margin:auto"><h2 style="color:#4f9cf9">💓 CardioGuard — Notification</h2><p>Bonjour <strong>{famille['prenom']} {famille['nom']}</strong>,</p><p style="font-size:16px;margin:20px 0">Une <strong>activité inhabituelle</strong> a été détectée chez <strong>{patient['prenom']} {patient['nom']}</strong>.</p><div style="background:#1e293b;border:2px solid #4f9cf9;border-radius:12px;padding:20px;text-align:center;margin:16px 0"><div style="font-size:14px;color:#94a3b8;margin-bottom:12px">📅 {alert_date} — ⏰ {alert_time}</div></div><p style="font-size:14px;color:#6ee7b7;line-height:1.8">✅ <strong>Les médecins ont été alertés automatiquement</strong> et s'occupent de la situation.<br><strong>Restez calme.</strong></p><a href="{APP_URL}/dashboard/{code}" style="display:inline-block;padding:12px 24px;background:linear-gradient(135deg,#4f9cf9,#7c3aed);color:white;border-radius:8px;text-decoration:none;margin:20px 0">Voir le dashboard →</a></div>"""
                    send_email(famille['email'], "CardioGuard — Notification", body_famille)
            
            alert = {'time': alert_time, 'date': alert_date, 'proba': round(proba, 3),
                    'patient': patient['prenom'] + ' ' + patient['nom'] if patient else 'Inconnu', 'code': code}
            save_alert(code, proba)
            socketio.emit('new_alert', alert)
            
    except Exception as e:
        log.debug(f"MQTT msg err: {e}")

def mqtt_loop():
    while True:
        try:
            c = mqtt.Client(client_id="cardioguard_main")
            c.username_pw_set(MQTT_USER, MQTT_PASS)
            c.on_connect = on_connect
            c.on_disconnect = on_disconnect
            c.on_message = on_message
            c.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            c.loop_forever()
        except Exception as e:
            log.error(f"MQTT: {e}")
            time.sleep(5)

def status_loop():
    while True:
        for code, buf in patient_data.items():
            if buf['signal_active'] and buf['last_time']:
                if (time.time() - buf['last_time']) > 3:
                    buf['signal_active'] = False
                    buf['total'] = 0
                    socketio.emit('signal_lost', {'code': code})
        
        socketio.emit('status', {'mqtt_ok': MQTT_OK, 'time': time.strftime("%H:%M:%S"),
                                'date': time.strftime("%A %d %B %Y")})
        time.sleep(1)

threading.Thread(target=mqtt_loop, daemon=True).start()
threading.Thread(target=status_loop, daemon=True).start()

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        row      = get_user_by_email(email)
        
        if row and check_password(password, row['password_hash']):
            login_user(User(row))
            flash(f"✅ Bienvenue {row['prenom']} !", "success")
            return redirect(url_for('home'))
        
        flash("❌ Email ou mot de passe incorrect.", "error")
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nom          = request.form.get('nom', '').strip()
        prenom       = request.form.get('prenom', '').strip()
        email        = request.form.get('email', '').strip()
        password     = request.form.get('password', '')
        role         = request.form.get('role', '')
        patient_code = request.form.get('patient_code', '').strip().upper()

        if not all([nom, prenom, email, password, role]):
            flash("❌ Tous les champs sont obligatoires.", "error")
            return render_template('register.html')

        medecin_id = None
        if role in ['patient', 'famille']:
            if not patient_code:
                flash("❌ Le code patient est obligatoire.", "error")
                return render_template('register.html')
            patient = get_patient_by_code(patient_code)
            if not patient:
                flash(f"❌ Code patient '{patient_code}' introuvable.", "error")
                return render_template('register.html')
            medecin = get_patient_medecin(patient_code)
            medecin_id = medecin['id'] if medecin else None

        try:
            ok, msg = create_user(nom, prenom, email, password, role, patient_code or None, medecin_id)
        except Exception as e:
            flash(f"❌ Erreur BD: {str(e)}", "error")
            return render_template('register.html')
        
        if ok:
            user_row = get_user_by_email(email)
            
            if role == 'famille' and patient_code:
                link_famille_to_patient(user_row['id'], patient_code)
            
            token = secrets.token_urlsafe(32)
            email_verify_tokens[token] = {'email': email, 'exp': time.time() + 3600}
            verify_link = f"{APP_URL}/verify-email/{token}"
            
            body = f"""<div style="font-family:sans-serif;max-width:500px;margin:auto"><h2 style="color:#4f9cf9">💓 Bienvenue sur CardioGuard</h2><p>Bonjour <strong>{prenom} {nom}</strong>,</p><p>Votre compte a été créé avec succès !</p><p>Veuillez vérifier votre adresse email en cliquant sur le lien ci-dessous :</p><a href="{verify_link}" style="display:inline-block;padding:12px 24px;background:linear-gradient(135deg,#4f9cf9,#7c3aed);color:white;border-radius:8px;text-decoration:none;margin:16px 0">Vérifier mon email →</a><p style="color:#94a3b8;font-size:12px">Ce lien expire dans 1 heure.</p></div>"""
            send_email(email, "CardioGuard — Vérifiez votre email", body)
            
            flash("✅ Compte créé ! Un email de vérification a été envoyé.", "success")
            log.info(f"📝 Inscription : {email} ({role})")
            return redirect(url_for('login'))
        
        flash(f"❌ {msg}", "error")
    
    return render_template('register.html')

@app.route('/verify-email/<token>')
def verify_email(token):
    data = email_verify_tokens.get(token)
    if not data or time.time() > data['exp']:
        flash("❌ Lien expiré ou invalide.", "error")
        return redirect(url_for('register'))
    
    del email_verify_tokens[token]
    flash("✅ Email vérifié ! Vous pouvez maintenant vous connecter.", "success")
    return redirect(url_for('login'))

@app.route('/home')
@login_required
def home():
    if current_user.role == 'medecin':
        return redirect(url_for('medecin'))
    
    code = current_user.patient_code
    if not code:
        flash("❌ Aucun patient associé.", "error")
        return redirect(url_for('login'))
    
    return redirect(url_for('dashboard', code=code))

@app.route('/medecin')
@login_required
def medecin():
    if current_user.role != 'medecin':
        return redirect(url_for('home'))
    
    all_patients = get_patients_for_medecin_with_archived(current_user.id)
    patients = [p for p in all_patients if p['archived'] == 0]
    archived_patients = [p for p in all_patients if p['archived'] == 1]
    
    return render_template('medecin.html', user=current_user, 
                         patients=patients, archived_patients=archived_patients)

@app.route('/medecin/add-patient', methods=['GET', 'POST'])
@login_required
def add_patient():
    if current_user.role != 'medecin':
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        mode = request.form.get('mode', 'new')
        
        if mode == 'new':
            nom    = request.form.get('nom', '').strip()
            prenom = request.form.get('prenom', '').strip()
            code   = request.form.get('code', '').strip().upper()
            
            if not all([nom, prenom, code]):
                flash("❌ Tous les champs sont obligatoires.", "error")
                return render_template('add_patient.html', user=current_user)
            
            try:
                ok, msg = create_patient(nom, prenom, code, current_user.id)
                if ok:
                    flash(f"✅ Patient {prenom} {nom} créé. Code : {code}", "success")
                    log.info(f"➕ Patient créé : {code}")
                    return redirect(url_for('medecin'))
                flash(f"❌ {msg}", "error")
            except Exception as e:
                flash(f"❌ Erreur BD: {str(e)}", "error")
        
        else:
            existing_code = request.form.get('existing_code', '').strip().upper()
            
            if not existing_code:
                flash("❌ Entrez le code patient.", "error")
                return render_template('add_patient.html', user=current_user)
            
            patient = get_patient_by_code(existing_code)
            if not patient:
                flash(f"❌ Patient avec code '{existing_code}' introuvable.", "error")
                return render_template('add_patient.html', user=current_user)
            
            if patient['medecin_id'] == current_user.id:
                flash(f"❌ Ce patient est déjà dans votre liste.", "error")
                return render_template('add_patient.html', user=current_user)
            
            try:
                conn = sqlite3.connect('/home/issra/CardioGuard/ecg.db')
                conn.execute("UPDATE patients SET medecin_id=? WHERE code=?", 
                           (current_user.id, existing_code))
                conn.commit()
                conn.close()
                flash(f"✅ Patient {patient['prenom']} {patient['nom']} ajouté à votre liste.", "success")
                log.info(f"➕ Patient assigné : {existing_code}")
                return redirect(url_for('dashboard', code=existing_code))
            except Exception as e:
                flash(f"❌ Erreur BD: {str(e)}", "error")
    
    return render_template('add_patient.html', user=current_user)

@app.route('/medecin/archive/<code>', methods=['POST'])
@login_required
def archive_pat(code):
    if current_user.role != 'medecin':
        return redirect(url_for('home'))
    
    patient = get_patient_by_code(code)
    if not patient or patient['medecin_id'] != current_user.id:
        flash("❌ Accès non autorisé.", "error")
        return redirect(url_for('medecin'))
    
    try:
        archive_patient(code)
        flash(f"✅ Patient {patient['prenom']} {patient['nom']} archivé.", "success")
    except Exception as e:
        flash(f"❌ Erreur : {str(e)}", "error")
    return redirect(url_for('medecin'))

@app.route('/medecin/restore/<code>', methods=['POST'])
@login_required
def restore_pat(code):
    if current_user.role != 'medecin':
        return redirect(url_for('home'))
    
    conn = sqlite3.connect('/home/issra/CardioGuard/ecg.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM patients WHERE code=?", (code,))
    patient = c.fetchone()
    conn.close()
    
    if not patient or patient['medecin_id'] != current_user.id:
        flash("❌ Accès non autorisé.", "error")
        return redirect(url_for('medecin'))
    
    try:
        restore_patient(code)
        flash(f"✅ Patient {patient['prenom']} {patient['nom']} restauré.", "success")
    except Exception as e:
        flash(f"❌ Erreur : {str(e)}", "error")
    return redirect(url_for('medecin'))

@app.route('/dashboard/<code>')
@login_required
def dashboard(code):
    patient = get_patient_by_code(code)
    if not patient:
        flash("❌ Patient introuvable.", "error")
        return redirect(url_for('home'))
    
    if current_user.role == 'medecin':
        if patient['medecin_id'] != current_user.id:
            flash("❌ Ce patient n'est pas dans votre liste.", "error")
            return redirect(url_for('medecin'))
    else:
        if current_user.patient_code != code:
            flash("❌ Accès non autorisé.", "error")
            return redirect(url_for('home'))
    
    alerts = get_alerts(code, limit=20)
    return render_template('dashboard.html', user=current_user, patient=patient, alerts=alerts)

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        row   = get_user_by_email(email)
        if row:
            token = secrets.token_urlsafe(32)
            reset_tokens[token] = {'email': email, 'exp': time.time() + 3600}
            link  = f"{APP_URL}/reset-password/{token}"
            body  = f"""<div style="font-family:sans-serif;max-width:500px;margin:auto"><h2 style="color:#4f9cf9">💓 CardioGuard — Réinitialisation</h2><p>Bonjour <strong>{row['prenom']} {row['nom']}</strong>,</p><p>Cliquez sur le lien ci-dessous pour réinitialiser votre mot de passe :</p><a href="{link}" style="display:inline-block;padding:12px 24px;background:linear-gradient(135deg,#4f9cf9,#7c3aed);color:white;border-radius:8px;text-decoration:none;margin:16px 0">Réinitialiser mon mot de passe</a><p style="color:#94a3b8;font-size:12px">Ce lien expire dans 1 heure.</p></div>"""
            send_email(email, "CardioGuard — Réinitialisation de mot de passe", body)
        
        flash("✅ Si cet email existe, un lien vous a été envoyé.", "success")
    
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    data = reset_tokens.get(token)
    if not data or time.time() > data['exp']:
        flash("❌ Lien expiré ou invalide.", "error")
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        if len(password) < 6:
            flash("❌ Le mot de passe doit faire au moins 6 caractères.", "error")
            return render_template('reset_password.html', token=token)
        
        try:
            update_password(data['email'], password)
            del reset_tokens[token]
            flash("✅ Mot de passe mis à jour !", "success")
            log.info(f"🔑 Mot de passe réinitialisé : {data['email']}")
            return redirect(url_for('login'))
        except Exception as e:
            flash(f"❌ Erreur : {str(e)}", "error")
    
    return render_template('reset_password.html', token=token)

@app.route('/forgot-code', methods=['GET', 'POST'])
def forgot_code():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        row   = get_user_by_email(email)
        
        if row and row['patient_code']:
            patient = get_patient_by_code(row['patient_code'])
            medecin = get_patient_medecin(row['patient_code'])
            
            if medecin and patient:
                body = f"""<div style="font-family:sans-serif;max-width:500px;margin:auto"><h2 style="color:#4f9cf9">💓 CardioGuard — Demande de code patient</h2><p>Dr. <strong>{medecin['prenom']} {medecin['nom']}</strong>,</p><p><strong>{row['prenom']} {row['nom']}</strong> a demandé son code patient.</p><div style="background:#1e293b;border:2px solid #4f9cf9;border-radius:12px;padding:20px;text-align:center;margin:16px 0"><div style="font-size:14px;color:#94a3b8;margin-bottom:8px"><strong>Patient :</strong> {patient['prenom']} {patient['nom']}</div><div style="font-size:24px;font-weight:700;color:#4f9cf9;letter-spacing:2px">{row['patient_code']}</div></div></div>"""
                send_email(medecin['email'], "CardioGuard — Demande de code patient", body)
        
        flash("✅ Si un code existe, le médecin a été notifié.", "success")
    
    return render_template('forgot_code.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("✅ Déconnecté.", "success")
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    insert_default_data()
    log.info("="*50)
    log.info("  CardioGuard — Surveillance Cardiaque")
    log.info(f"  URL : {APP_URL}")
    log.info("="*50)
    
    socketio.run(app, host='0.0.0.0', port=5001, debug=False, 
                 use_reloader=False, allow_unsafe_werkzeug=True)
