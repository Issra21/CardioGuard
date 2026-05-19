"""
CardioGuard — Database Module
SQLite3 pour users, patients, alerts, medecin_patients, famille
VERSION COMPLÈTE : Multi-médecins + Archivage + Famille
"""
import sqlite3, hashlib
import time
DB_PATH = "/home/azureuser/app/ecg.db"

def get_db_connection(timeout=30, retries=3):
    """Connexion BD avec retry automatique"""
    for attempt in range(retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=timeout, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout={}".format(timeout * 1000))
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
            else:
                raise
    raise sqlite3.OperationalError("BD verrouillée après {} tentatives".format(retries))

# ═══════════════════════════════════════════════
# INITIALISATION BD
# ═══════════════════════════════════════════════

def init_db():
    """Crée les tables si elles n'existent pas"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Table users
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        prenom TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        patient_code TEXT,
        medecin_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(medecin_id) REFERENCES users(id)
    )''')
    
    # Table patients (avec médecin assigné + archivage)
    c.execute('''CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        prenom TEXT NOT NULL,
        code TEXT UNIQUE NOT NULL,
        topic_mqtt TEXT NOT NULL,
        medecin_id INTEGER NOT NULL,
        archived INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(medecin_id) REFERENCES users(id)
    )''')
    
    # Table famille (1 membre famille = 1 patient)
    c.execute('''CREATE TABLE IF NOT EXISTS famille (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        patient_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(patient_id) REFERENCES patients(id),
        UNIQUE(user_id, patient_id)
    )''')
    
    # Table alerts
    c.execute('''CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_code TEXT NOT NULL,
        proba REAL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    conn.close()
    print("✅ Base de données initialisée")

# ═══════════════════════════════════════════════
# PASSWORD HELPERS
# ═══════════════════════════════════════════════

def hash_password(password):
    """Hash le mot de passe avec SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(password, hash_val):
    """Vérifie si le mot de passe correspond au hash"""
    return hash_password(password) == hash_val

# ═══════════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════════

def create_user(nom, prenom, email, password, role, patient_code=None, medecin_id=None):
    """
    Crée un nouvel utilisateur
    role: 'medecin', 'patient', 'famille'
    patient_code: code du patient (patient/famille)
    medecin_id: ID du médecin (patient/famille)
    """
    try:
        conn = get_db_connection()
        conn.execute(
            'INSERT INTO users (nom,prenom,email,password_hash,role,patient_code,medecin_id) VALUES (?,?,?,?,?,?,?)',
            (nom, prenom, email, hash_password(password), role, patient_code, medecin_id)
        )
        conn.commit()
        conn.close()
        return True, "Utilisateur créé"
    except sqlite3.IntegrityError:
        return False, "Cet email est déjà utilisé"
    except Exception as e:
        return False, f"Erreur : {str(e)}"

def get_user_by_email(email):
    """Récupère un user par email"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email=?", (email,))
    row = c.fetchone()
    conn.close()
    return row

def get_user_by_id(uid):
    """Récupère un user par ID"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    conn.close()
    return row

def get_user_patient_code(email):
    """Récupère le code patient d'un user"""
    row = get_user_by_email(email)
    return row['patient_code'] if row else None

def update_password(email, new_password):
    """Met à jour le mot de passe d'un user"""
    conn = get_db_connection()
    conn.execute(
        'UPDATE users SET password_hash=? WHERE email=?',
        (hash_password(new_password), email)
    )
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════════
# PATIENTS
# ═══════════════════════════════════════════════

def create_patient(nom, prenom, code, medecin_id, topic_mqtt="ecg/data"):
    """Crée un nouveau patient assigné à un médecin"""
    try:
        conn = get_db_connection()
        conn.execute(
            'INSERT INTO patients (nom,prenom,code,topic_mqtt,medecin_id) VALUES (?,?,?,?,?)',
            (nom, prenom, code, topic_mqtt, medecin_id)
        )
        conn.commit()
        conn.close()
        return True, "Patient créé"
    except sqlite3.IntegrityError:
        return False, "Ce code patient existe déjà"
    except Exception as e:
        return False, f"Erreur : {str(e)}"

def get_patient_by_code(code):
    """Récupère un patient par son code (non archivé)"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM patients WHERE code=? AND archived=0", (code,))
    row = c.fetchone()
    conn.close()
    return row

def get_patients_for_medecin(medecin_id, include_archived=False):
    """Récupère les patients d'un médecin"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if include_archived:
        c.execute("SELECT * FROM patients WHERE medecin_id=? ORDER BY nom, prenom", (medecin_id,))
    else:
        c.execute("SELECT * FROM patients WHERE medecin_id=? AND archived=0 ORDER BY nom, prenom", (medecin_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_patients():
    """Récupère tous les patients (non archivés)"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM patients WHERE archived=0 ORDER BY nom, prenom")
    rows = c.fetchall()
    conn.close()
    return rows

def archive_patient(patient_code):
    """Archive un patient"""
    conn = get_db_connection()
    conn.execute("UPDATE patients SET archived=1 WHERE code=?", (patient_code,))
    conn.commit()
    conn.close()

def restore_patient(patient_code):
    """Désarchive un patient"""
    conn = get_db_connection()
    conn.execute("UPDATE patients SET archived=0 WHERE code=?", (patient_code,))
    conn.commit()
    conn.close()

def get_patient_medecin(patient_code):
    """Récupère le médecin d'un patient"""
    conn =get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT u.* FROM users u
        JOIN patients p ON p.medecin_id = u.id
        WHERE p.code=?
    """, (patient_code,))
    row = c.fetchone()
    conn.close()
    return row

# ═══════════════════════════════════════════════
# FAMILLE
# ═══════════════════════════════════════════════

def link_famille_to_patient(user_id, patient_code):
    """Crée le lien entre un membre famille et un patient"""
    try:
        conn = get_db_connection()
        # Récupérer l'ID du patient
        c = conn.cursor()
        c.execute("SELECT id FROM patients WHERE code=?", (patient_code,))
        patient_row = c.fetchone()
        if not patient_row:
            return False, "Patient introuvable"
        
        patient_id = patient_row[0]
        conn.execute(
            'INSERT INTO famille (user_id, patient_id) VALUES (?, ?)',
            (user_id, patient_id)
        )
        conn.commit()
        conn.close()
        return True, "Famille liée au patient"
    except sqlite3.IntegrityError:
        return False, "Lien déjà existant"
    except Exception as e:
        return False, f"Erreur : {str(e)}"

def get_famille_for_patient(patient_code):
    """Récupère le membre famille d'un patient"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT u.* FROM users u
        JOIN famille f ON f.user_id = u.id
        JOIN patients p ON f.patient_id = p.id
        WHERE p.code=?
    """, (patient_code,))
    row = c.fetchone()
    conn.close()
    return row

def get_patient_for_famille(user_id):
    """Récupère le patient d'un membre famille"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT p.* FROM patients p
        JOIN famille f ON f.patient_id = p.id
        WHERE f.user_id=?
    """, (user_id,))
    row = c.fetchone()
    conn.close()
    return row

# ═══════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════

def save_alert(patient_code, proba):
    """Enregistre une alerte pour un patient"""
    try:
        conn = get_db_connection()
        conn.execute(
            'INSERT INTO alerts (patient_code,proba) VALUES (?,?)',
            (patient_code, proba)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur save_alert: {e}")

def get_alerts(patient_code, limit=20):
    """Récupère les N dernières alertes d'un patient"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT * FROM alerts WHERE patient_code=? ORDER BY timestamp DESC LIMIT ?",
        (patient_code, limit)
    )
    rows = c.fetchall()
    conn.close()
    return rows

# ═══════════════════════════════════════════════
# DEFAULT DATA
# ═══════════════════════════════════════════════

def insert_default_data():
    """Insère les données par défaut"""
    # Crée un médecin de référence
    ok, msg = create_user("Médecin", "Référent", "medecin@ecg.tn", "medecin123", "medecin")
    if ok:
        print("ℹ️  Médecin créé")
        # Récupérer l'ID du médecin
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email='medecin@ecg.tn'")
        medecin_id = c.fetchone()[0]
        conn.close()
        
        # Crée le patient Issra assigné au médecin
        ok2, msg2 = create_patient("Saidi", "Issra", "ISSRA2026", medecin_id, "ecg/data")
        if ok2:
            print("ℹ️  Patient Issra créé")
        else:
            print(f"ℹ️  Patient Issra déjà existant")
    else:
        print("ℹ️  Médecin déjà existant")
    
    print("✅ Données par défaut insérées")

# ═══════════════════════════════════════════════
# TEST
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    insert_default_data()
    print("\n✅ Base de données prête !")
