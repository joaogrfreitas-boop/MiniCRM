from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
import sqlite3, pandas as pd, unicodedata, datetime, json, os, secrets
from io import BytesIO

# Auth
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = "dev"
DB_FILE = "crm_v4.db"

# ================= Defaults & Settings =================
DEFAULT_SETTINGS = {
    "company_base_statuses": ["Listado", "Mapeado", "Contatado", "Em conversa", "On Hold"],
    "contact_stages": [
        "Contato Inicial","Fazer FUP","Marcar Reunião","Reunião Marcada",
        "Acompanhar","Projeto Ganho","Projeto Perdido","Potencial Futuro"
    ],
    "inactive_stages": ["Projeto Perdido","Potencial Futuro"]
}
OPP_STAGES = [
  "Qualificação","Descoberta","Proposta","Negociação","Fechado - Ganho","Fechado - Perdido"
]
OPP_PLAYBOOKS = {
  "Qualificação": ["Confirmar dor e fit", "Identificar decisor e budget", "Definir próximo passo"],
  "Descoberta": ["Explorar requisitos", "Mapear stakeholders", "Validar critérios de sucesso"],
  "Proposta": ["Enviar proposta", "Apresentar valor", "Alinhar escopo e cronograma"],
  "Negociação": ["Revisar termos", "Definir aprovação", "Plano de implantação"],
  "Fechado - Ganho": ["Kickoff", "PO/NDA", "Plano de ação"],
  "Fechado - Perdido": ["Registrar motivo", "Fechar tarefas abertas", "Seguimento futuro"]
}

# ============== DB helpers ==============
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def load_settings(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id=1), json TEXT)")
    row = conn.execute("SELECT json FROM settings WHERE id=1").fetchone()
    cfg = DEFAULT_SETTINGS.copy()
    if row and row["json"]:
        try:
            cfg.update(json.loads(row["json"]) or {})
        except Exception:
            pass
    return cfg

def save_settings(conn, cfg):
    js = json.dumps(cfg, ensure_ascii=False)
    with conn:
        conn.execute("INSERT INTO settings (id,json) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET json=excluded.json", (js,))

def rebuild_company_status_view(conn, cfg):
    inactive = cfg["inactive_stages"]
    conn.executescript("DROP VIEW IF EXISTS company_status_view;")
    placeholders = ",".join("'" + s.replace("'", "''") + "'" for s in inactive)
    sql = f"""
    CREATE VIEW company_status_view AS
    SELECT
      c.id,
      c.name,
      c.reg_status_base,
      CASE
        WHEN EXISTS (
          SELECT 1 FROM contacts ct WHERE ct.company_id=c.id AND ct.contact_stage NOT IN ({placeholders})
        ) THEN 'Ativo'
        WHEN EXISTS (
          SELECT 1 FROM contacts ct WHERE ct.company_id=c.id
        ) THEN 'Inativo'
        ELSE NULL
      END AS reg_status_effective
    FROM companies c;
    """
    conn.executescript(sql)

def ensure_activity_log(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT DEFAULT CURRENT_TIMESTAMP,
        type TEXT,
        company_id INTEGER,
        contact_id INTEGER,
        details TEXT
    )""")

def log_event(conn, type_, company_id=None, contact_id=None, details=""):
    try:
        conn.execute("INSERT INTO activity_log (type,company_id,contact_id,details) VALUES (?,?,?,?)",
                     (type_, company_id, contact_id, details))
    except Exception:
        pass

def init_db():
    conn = get_db()
    conn.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS companies (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      category TEXT DEFAULT '',
      subcategory TEXT DEFAULT '',
      reg_status_base TEXT DEFAULT 'Listado',
      city TEXT DEFAULT '',
      state TEXT DEFAULT '',
      notes TEXT DEFAULT '',
      owner_id INTEGER,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_name ON companies(name);

    CREATE TABLE IF NOT EXISTS contacts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
      name TEXT NOT NULL,
      role TEXT DEFAULT '',
      email TEXT DEFAULT '',
      phone TEXT DEFAULT '',
      contact_stage TEXT DEFAULT 'Contato Inicial',
      priority INTEGER DEFAULT 2,
      next_action TEXT DEFAULT '',
      next_date TEXT DEFAULT '',
      notes TEXT DEFAULT '',
      owner_id INTEGER,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
    CREATE INDEX IF NOT EXISTS idx_contacts_stage ON contacts(contact_stage);

    CREATE TABLE IF NOT EXISTS tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
      contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      due_date TEXT,
      done INTEGER DEFAULT 0,
      outcome TEXT,
      notes TEXT,
      owner_id INTEGER,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_company ON tasks(company_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);

    CREATE TABLE IF NOT EXISTS opportunities (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      stage TEXT DEFAULT 'Qualificação',
      amount REAL DEFAULT 0,
      probability INTEGER DEFAULT 10,
      close_date TEXT,
      owner TEXT,
      notes TEXT,
      owner_id INTEGER,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_opps_company ON opportunities(company_id);
    CREATE INDEX IF NOT EXISTS idx_opps_stage ON opportunities(stage);
    """)
    # settings + view + activity log
    cfg = load_settings(conn)
    save_settings(conn, cfg)
    rebuild_company_status_view(conn, cfg)
    ensure_activity_log(conn)

    # users table
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      email TEXT UNIQUE NOT NULL,
      pwd_hash TEXT NOT NULL,
      role TEXT DEFAULT 'sales',
      api_token TEXT
    )""")
    # seed admin if empty
    row = conn.execute("SELECT COUNT(*) c FROM users").fetchone()
    if row and row["c"] == 0:
        conn.execute("INSERT INTO users (name,email,pwd_hash,role) VALUES (?,?,?,?)",
                     ("Admin", "admin@example.com", generate_password_hash("admin"), "admin"))
    conn.commit(); conn.close()

# -------------- Auth setup --------------
login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.name = row["name"]
        self.email = row["email"]
        self.role = row["role"]
        self.api_token = row["api_token"]

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return User(row) if row else None

def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                flash("Sem permissão.", "danger")
                return redirect(url_for("dashboard"))
            return fn(*a, **kw)
        return wrapper
    return deco

# -------------- Filters & utils --------------
def _norm(s):
    s = str(s or "").strip()
    s = ''.join(ch for ch in unicodedata.normalize('NFKD', s) if not unicodedata.combining(ch))
    return s.lower()

@app.template_filter()
def dmy(iso):
    try:
        return datetime.date.fromisoformat(str(iso)).strftime("%d/%m/%Y")
    except Exception:
        return ""

def apply_sorting(base_sql, allowed, default_col, default_dir="asc"):
    sort = request.args.get("sort", default_col)
    direction = request.args.get("dir", default_dir).lower()
    if sort not in allowed: sort = default_col
    if direction not in ("asc","desc"): direction = default_dir
    return f"{base_sql} ORDER BY {sort} {direction}", sort, direction

# -------------- Login routes --------------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        pwd = request.form.get("password","")
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE LOWER(email)=?", (email,)).fetchone()
        conn.close()
        if row and check_password_hash(row["pwd_hash"], pwd):
            login_user(User(row))
            flash("Bem-vindo!", "success")
            return redirect(url_for("dashboard"))
        flash("Credenciais inválidas.", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sessão encerrada.", "success")
    return redirect(url_for("login"))

@app.route("/")
def root():
    return redirect(url_for("dashboard"))

# ---- Dashboard geral ----
@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    cfg = load_settings(conn)
    eff = conn.execute("""SELECT reg_status_effective AS k, COUNT(*) c
                          FROM company_status_view WHERE reg_status_effective IS NOT NULL
                          GROUP BY reg_status_effective""").fetchall()
    eff_map = {r["k"]: r["c"] for r in eff}
    base = conn.execute("SELECT reg_status_base AS k, COUNT(*) c FROM companies GROUP BY reg_status_base").fetchall()
    base_map = {r["k"]: r["c"] for r in base}
    by_stage = conn.execute("SELECT contact_stage AS k, COUNT(*) c FROM contacts GROUP BY contact_stage").fetchall()
    stg_map = {r["k"]: r["c"] for r in by_stage}
    overdue = conn.execute("""
        SELECT t.*, co.name AS company_name, ct.name AS contact_name
        FROM tasks t JOIN companies co ON co.id=t.company_id
        LEFT JOIN contacts ct ON ct.id=t.contact_id
        WHERE IFNULL(t.done,0)=0 AND IFNULL(t.due_date,'')<>'' AND date(t.due_date) < date('now')
        ORDER BY date(t.due_date) ASC LIMIT 30""").fetchall()
    next7 = conn.execute("""
        SELECT t.*, co.name AS company_name, ct.name AS contact_name
        FROM tasks t JOIN companies co ON co.id=t.company_id
        LEFT JOIN contacts ct ON ct.id=t.contact_id
        WHERE IFNULL(t.done,0)=0 AND IFNULL(t.due_date,'')<>'' AND date(t.due_date) BETWEEN date('now') AND date('now','+7 day')
        ORDER BY date(t.due_date) ASC LIMIT 30""").fetchall()
    total_companies = conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
    total_contacts = conn.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"]
    conn.close()

    eff_labels = ["Ativo","Inativo"]
    eff_counts = [int(eff_map.get("Ativo",0)), int(eff_map.get("Inativo",0))]
    base_labels = cfg["company_base_statuses"]
    base_counts = [int(base_map.get(x,0)) for x in base_labels]
    contact_labels = cfg["contact_stages"]
    contact_counts = [int(stg_map.get(x,0)) for x in contact_labels]

    kpis = {"empresas": int(total_companies), "contatos": int(total_contacts),
            "ativos": int(eff_map.get("Ativo",0)), "inativos": int(eff_map.get("Inativo",0))}

    return render_template("dashboard.html",
                           eff_labels=eff_labels, eff_counts=eff_counts,
                           base_labels=base_labels, base_counts=base_counts,
                           contact_labels=contact_labels, contact_counts=contact_counts,
                           overdue=overdue, next7=next7, kpis=kpis)

# ---- Dashboard Empresas ----
@app.route("/dashboard_companies")
@login_required
def dashboard_companies():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
    mapeados = conn.execute("SELECT COUNT(DISTINCT co.id) c FROM companies co JOIN contacts ct ON ct.company_id=co.id").fetchone()["c"]
    acionados = conn.execute("""SELECT COUNT(DISTINCT co.id) c FROM companies co JOIN contacts ct ON ct.company_id=co.id
                                WHERE ct.contact_stage <> 'Contato Inicial'""").fetchone()["c"]
    retorno_pos = conn.execute("""SELECT COUNT(DISTINCT co.id) c FROM companies co JOIN contacts ct ON ct.company_id=co.id
                                  WHERE ct.contact_stage IN ('Reunião Marcada','Projeto Ganho')""").fetchone()["c"]
    potencial_imediato = conn.execute("""SELECT COUNT(DISTINCT co.id) c FROM companies co JOIN contacts ct ON ct.company_id=co.id
                                         WHERE ct.contact_stage IN ('Marcar Reunião','Reunião Marcada','Acompanhar')""").fetchone()["c"]
    by_cat = conn.execute("SELECT IFNULL(category,'') k, COUNT(*) c FROM companies GROUP BY IFNULL(category,'') ORDER BY c DESC").fetchall()
    by_sub = conn.execute("SELECT IFNULL(subcategory,'') k, COUNT(*) c FROM companies GROUP BY IFNULL(subcategory,'') ORDER BY c DESC LIMIT 20").fetchall()
    conn.close()

    def perc(part, base): return 0 if not base else round(100*part/base,1)
    kpi = {
        "total": total,
        "mapeados": mapeados, "mapeados_pct": perc(mapeados, total),
        "acionados": acionados, "acionados_pct": perc(acionados, mapeados or total),
        "retorno_pos": retorno_pos, "retorno_pos_pct": perc(retorno_pos, acionados or total),
        "potencial_imediato": potencial_imediato, "potencial_imediato_pct": perc(potencial_imediato, acionados or total),
    }
    return render_template("dashboard_companies.html",
                           kpi=kpi,
                           cat_labels=[r["k"] or "(sem categoria)" for r in by_cat],
                           cat_counts=[int(r["c"]) for r in by_cat],
                           sub_labels=[r["k"] or "(sem subcategoria)" for r in by_sub],
                           sub_counts=[int(r["c"]) for r in by_sub])

# ---- Settings ----
@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    conn = get_db()
    cfg = load_settings(conn)
    if request.method == "POST":
        base = [x.strip() for x in request.form.get("company_base_statuses","").split(",") if x.strip()]
        stages = [x.strip() for x in request.form.get("contact_stages","").split(",") if x.strip()]
        inactive = [x.strip() for x in request.form.get("inactive_stages","").split(",") if x.strip()]
        if base: cfg["company_base_statuses"] = base
        if stages: cfg["contact_stages"] = stages
        if inactive: cfg["inactive_stages"] = inactive
        save_settings(conn, cfg); rebuild_company_status_view(conn, cfg)
        flash("Configurações salvas.", "success")
        return redirect(url_for("settings"))
    return render_template("settings.html", cfg=cfg)

@app.route("/settings/wipe", methods=["POST"])
@login_required
def settings_wipe():
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM tasks")
        conn.execute("DELETE FROM contacts")
        conn.execute("DELETE FROM companies")
        conn.execute("DELETE FROM opportunities")
        conn.execute("DELETE FROM activity_log")
    rebuild_company_status_view(conn, load_settings(conn))
    flash("Base limpa.", "warning")
    return redirect(url_for("settings"))

# -------------------- Board (simples) --------------------
@app.route("/board")
@login_required
def board():
    conn=get_db()
    cfg = load_settings(conn)
    stages=cfg["contact_stages"]
    columns={}
    for s in stages:
        rows=conn.execute("""SELECT ct.id, ct.name, co.name AS company_name
                             FROM contacts ct JOIN companies co ON co.id=ct.company_id
                             WHERE ct.contact_stage=? ORDER BY ct.priority, co.name, ct.name""",(s,)).fetchall()
        columns[s]=rows or []
    conn.close()
    return render_template("board.html", columns=columns, stages=stages)

@app.route("/contact/<int:cid>/move", methods=["POST"])
@login_required
def contact_move(cid):
    data=request.get_json(silent=True) or {}; new_stage=data.get("stage","")
    cfg = load_settings(get_db())
    if new_stage not in cfg["contact_stages"]:
        return jsonify({"ok":False,"error":"stage inválido"}),400
    conn=get_db()
    with conn:
        conn.execute("UPDATE contacts SET contact_stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_stage,cid))
        company = conn.execute("SELECT company_id FROM contacts WHERE id=?", (cid,)).fetchone()
        log_event(conn, 'contact_stage_drag', company["company_id"] if company else None, cid, f"Estágio → <b>{new_stage}</b>")
    return jsonify({"ok":True})

# -------------------- Export simples --------------------
@app.route('/export/companies.xlsx')
@login_required
def export_companies_xlsx():
    q = request.args.get('q',''); sort=request.args.get('sort','c.name'); direction=request.args.get('dir','asc')
    sql = """SELECT c.*, v.reg_status_effective
             FROM companies c LEFT JOIN company_status_view v ON v.id=c.id"""
    params=[]; where=[]
    if q:
        like=f"%{q}%"; where.append("(c.name LIKE ? OR c.category LIKE ? OR c.subcategory LIKE ? OR c.city LIKE ?)"); params += [like,like,like,like]
    if where: sql += " WHERE "+ " AND ".join(where)
    sql += f" ORDER BY {sort} {direction}"
    conn=get_db(); rows=conn.execute(sql, params).fetchall(); conn.close()
    df = pd.DataFrame([dict(r) for r in rows])
    buf = BytesIO(); df.to_excel(buf, index=False); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='companies.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# -------------------- Templates mínimos --------------------
# (Deixe os templates da sua pasta /templates atuais; este app.py só garante que o servidor roda sem erros.)

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
