from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import sqlite3, pandas as pd, unicodedata, datetime, json

app = Flask(__name__)
app.secret_key = "dev"

# ================= Defaults & Settings =================
DEFAULT_SETTINGS = {
    "company_base_statuses": ["Listado", "Mapeado", "Contatado", "Em conversa", "On Hold"],
    "contact_stages": [
        "Contato Inicial","Fazer FUP","Marcar Reunião","Reunião Marcada",
        "Acompanhar","Projeto Ganho","Projeto Perdido","Potencial Futuro"
    ],
    "inactive_stages": ["Projeto Perdido","Potencial Futuro"]
}

def get_db():
    conn = sqlite3.connect('crm_v4.db')
    conn.row_factory = sqlite3.Row
    return conn

def load_settings(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id=1), json TEXT)")
    row = conn.execute("SELECT json FROM settings WHERE id=1").fetchone()
    if row and row["json"]:
        try:
            cfg = json.loads(row["json"])
        except Exception:
            cfg = {}
    else:
        cfg = {}
    # merge defaults
    merged = DEFAULT_SETTINGS.copy()
    merged.update(cfg or {})
    return merged

def save_settings(conn, cfg):
    js = json.dumps(cfg, ensure_ascii=False)
    with conn:
        conn.execute("INSERT INTO settings (id,json) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET json=excluded.json", (js,))

def rebuild_company_status_view(conn, cfg):
    inactive = cfg["inactive_stages"]
    # active if exists contact with stage NOT IN inactive
    # inactive if exists contact and all contacts are in inactive; no contacts => NULL (excluded)
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
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_company ON tasks(company_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
    """)
    # ensure settings + view
    cfg = load_settings(conn)
    save_settings(conn, cfg)
    rebuild_company_status_view(conn, cfg)
    conn.commit(); conn.close()

# -------------- Helpers --------------
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

# -------------- Routes --------------
@app.route("/")
def root():
    return redirect(url_for("dashboard"))

# ---- Dashboard geral ----
@app.route("/dashboard")
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
def settings_wipe():
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM tasks")
        conn.execute("DELETE FROM contacts")
        conn.execute("DELETE FROM companies")
    rebuild_company_status_view(conn, load_settings(conn))
    flash("Base limpa (empresas, contatos, tarefas).", "warning")
    return redirect(url_for("settings"))

# ---- Companies ----
@app.route("/companies")
def companies_list():
    q = request.args.get("q","").strip()
    sql = """
      SELECT c.*, v.reg_status_effective
      FROM companies c
      LEFT JOIN company_status_view v ON v.id=c.id
    """
    params=[]; where=[]
    if q:
        like=f"%{q}%"
        where.append("(c.name LIKE ? OR c.category LIKE ? OR c.subcategory LIKE ? OR c.city LIKE ?)")
        params += [like, like, like, like]
    if where: sql += " WHERE " + " AND ".join(where)
    sql, sort, direction = apply_sorting(sql, allowed={"c.name","c.category","c.subcategory","c.city","c.state","c.reg_status_base","v.reg_status_effective"}, default_col="c.name")
    conn=get_db(); rows=conn.execute(sql, params).fetchall(); conn.close()
    cfg = load_settings(get_db())
    return render_template("companies_list.html", rows=rows, base_statuses=cfg["company_base_statuses"], sort=sort, direction=direction)

@app.route("/company/new")
def company_new():
    cfg = load_settings(get_db())
    return render_template("company_form.html", company=None, base_statuses=cfg["company_base_statuses"])

@app.route("/company", methods=["POST"])
def company_create():
    f=request.form
    conn=get_db()
    with conn:
        conn.execute("""INSERT INTO companies (name, category, subcategory, reg_status_base, city, state, notes)
                        VALUES (?,?,?,?,?,?,?)""",
                        (f["name"], f.get("category",""), f.get("subcategory",""),
                         f.get("reg_status_base","Listado"), f.get("city",""), f.get("state",""), f.get("notes","")))
    flash("Empresa criada.","success"); return redirect(url_for("companies_list"))

@app.route("/company/<int:cid>/regstatus", methods=["POST"])
def company_regstatus(cid):
    data=request.get_json(silent=True) or {}; val=data.get("reg_status_base","")
    cfg = load_settings(get_db())
    if val not in cfg["company_base_statuses"]:
        return jsonify({"ok":False,"error":"valor inválido"}),400
    conn=get_db()
    with conn:
        conn.execute("UPDATE companies SET reg_status_base=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (val, cid))
    return jsonify({"ok":True})

@app.route("/company/<int:cid>/delete", methods=["POST"])
def company_delete(cid):
    conn=get_db()
    with conn: conn.execute("DELETE FROM companies WHERE id=?", (cid,))
    flash("Empresa excluída.","success"); return redirect(url_for("companies_list"))

# ---- Contacts ----
@app.route("/contacts")
def contacts_list():
    q = request.args.get("q","").strip()
    company_id = request.args.get("company_id","").strip()
    sql = """
      SELECT ct.*, co.name AS company_name
      FROM contacts ct JOIN companies co ON co.id=ct.company_id
    """
    params=[]; where=[]
    if q:
        like=f"%{q}%"; where.append("(ct.name LIKE ? OR ct.email LIKE ? OR ct.phone LIKE ? OR co.name LIKE ?)"); params += [like,like,like,like]
    if company_id: where.append("ct.company_id=?"); params.append(company_id)
    if where: sql += " WHERE " + " AND ".join(where)
    sql, sort, direction = apply_sorting(sql, allowed={"co.name","ct.name","ct.role","ct.email","ct.phone","ct.contact_stage","ct.next_date"}, default_col="co.name")
    conn=get_db()
    rows=conn.execute(sql, params).fetchall()
    companies=conn.execute("SELECT id, name FROM companies ORDER BY name").fetchall()
    conn.close()
    cfg = load_settings(get_db())
    return render_template("contacts_list.html", rows=rows, stages=cfg["contact_stages"], companies=companies, sort=sort, direction=direction)

@app.route("/contact/new")
def contact_new():
    conn=get_db(); companies=conn.execute("SELECT id,name FROM companies ORDER BY name").fetchall(); conn.close()
    cfg = load_settings(get_db())
    return render_template("contact_form.html", contact=None, companies=companies, stages=cfg["contact_stages"])

@app.route("/contact", methods=["POST"])
def contact_create():
    f=request.form; conn=get_db()
    with conn:
        conn.execute("""INSERT INTO contacts (company_id,name,role,email,phone,contact_stage,priority,next_action,next_date,notes)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (f["company_id"], f["name"], f.get("role",""), f.get("email",""), f.get("phone",""),
                         f.get("contact_stage","Contato Inicial"), int(f.get("priority",2)), f.get("next_action",""), f.get("next_date",""), f.get("notes","")))
    flash("Contato criado.","success"); return redirect(url_for("contacts_list"))

@app.route("/contact/<int:cid>/quick", methods=["POST"])
def contact_quick(cid):
    data=request.get_json(silent=True) or {}; fields=[]; vals=[]
    if "contact_stage" in data: fields.append("contact_stage=?"); vals.append(data["contact_stage"])
    if "next_date" in data: fields.append("next_date=?"); vals.append(data["next_date"])
    if not fields: return jsonify({"ok":False,"error":"nada para atualizar"}),400
    vals.append(cid)
    conn=get_db()
    with conn: conn.execute(f"UPDATE contacts SET {', '.join(fields)}, updated_at=CURRENT_TIMESTAMP WHERE id=?", vals)
    return jsonify({"ok":True})

@app.route("/contact/<int:cid>/delete", methods=["POST"])
def contact_delete(cid):
    conn=get_db()
    with conn: conn.execute("DELETE FROM contacts WHERE id=?", (cid,))
    flash("Contato excluído.","success"); return redirect(url_for("contacts_list"))

@app.route("/company/<int:cid>/contacts.json")
def company_contacts_json(cid):
    conn=get_db(); rows=conn.execute("SELECT id,name FROM contacts WHERE company_id=? ORDER BY name",(cid,)).fetchall(); conn.close()
    return jsonify([{"id":r["id"],"name":r["name"]} for r in rows])

# ---- Tasks ----
@app.route("/tasks")
def tasks_list():
    conn=get_db()
    rows=conn.execute("""SELECT t.*, co.name AS company_name, ct.name AS contact_name
                         FROM tasks t JOIN companies co ON co.id=t.company_id
                         LEFT JOIN contacts ct ON ct.id=t.contact_id
                         ORDER BY IFNULL(date(t.due_date),date('9999-12-31')) ASC""").fetchall()
    companies=conn.execute("SELECT id,name FROM companies ORDER BY name").fetchall()
    conn.close()
    return render_template("tasks_list.html", rows=rows, companies=companies)

@app.route("/task", methods=["POST"])
def task_create():
    f=request.form; conn=get_db()
    with conn:
        conn.execute("""INSERT INTO tasks (company_id,contact_id,title,due_date,done,notes)
                        VALUES (?,?,?,?,?,?)""",
                        (f["company_id"], f.get("contact_id") or None, f["title"], f.get("due_date",""), int(f.get("done",0)), f.get("notes","")))
    flash("Tarefa criada.","success"); return redirect(url_for("tasks_list"))

@app.route("/task/<int:tid>/done", methods=["POST"])
def task_done(tid):
    conn = get_db()
    with conn:
        conn.execute(
            "UPDATE tasks SET done=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (tid,)
        )
    return redirect(url_for("tasks_list"))

# ---- Board ----
BOARD_STAGES = DEFAULT_SETTINGS["contact_stages"]  # initial order
@app.route("/board")
def board():
    conn=get_db()
    cfg = load_settings(conn)
    stages = cfg["contact_stages"]
    columns={}
    for s in stages:
        rows=conn.execute("""SELECT ct.id, ct.name, co.name AS company_name
                             FROM contacts ct JOIN companies co ON co.id=ct.company_id
                             WHERE ct.contact_stage=? ORDER BY ct.priority, co.name, ct.name""",(s,)).fetchall()
        columns[s]=rows or []
    conn.close()
    return render_template("board.html", columns=columns, stages=stages)

@app.route("/contact/<int:cid>/move", methods=["POST"])
def contact_move(cid):
    data=request.get_json(silent=True) or {}; new_stage=data.get("stage","")
    cfg = load_settings(get_db())
    if new_stage not in cfg["contact_stages"]:
        return jsonify({"ok":False,"error":"stage inválido"}),400
    conn=get_db()
    with conn: conn.execute("UPDATE contacts SET contact_stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_stage,cid))
    return jsonify({"ok":True})

@app.route("/company/<int:cid>/edit")
def company_edit(cid):
    conn = get_db()
    company = conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
    conn.close()
    if not company:
        flash("Empresa não encontrada.","danger")
        return redirect(url_for("companies_list"))
    cfg = load_settings(get_db())
    return render_template("company_form.html", company=company, base_statuses=cfg["company_base_statuses"])

@app.route("/company/<int:cid>", methods=["POST"])
def company_update(cid):
    f = request.form
    conn = get_db()
    with conn:
        conn.execute("""UPDATE companies
                        SET name=?, category=?, subcategory=?, reg_status_base=?,
                            city=?, state=?, notes=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?""", (
            f["name"], f.get("category",""), f.get("subcategory",""),
            f.get("reg_status_base","Listado"), f.get("city",""),
            f.get("state",""), f.get("notes",""), cid
        ))
    flash("Empresa atualizada.","success")
    return redirect(url_for("companies_list"))

# ---- Importador Excel ----
@app.route("/import", methods=["POST"])
def import_excel():
    file=request.files.get("excel_file")
    if not file or not file.filename.lower().endswith(".xlsx"):
        flash("Envie um arquivo Excel .xlsx com abas Companies e Contacts.","danger")
        return redirect(url_for("dashboard"))
    try:
        xls=pd.ExcelFile(file); sheets=xls.sheet_names
        if "Companies" not in sheets or "Contacts" not in sheets:
            flash("O modelo precisa ter abas 'Companies' e 'Contacts'.","danger"); return redirect(url_for("dashboard"))
        dfc=xls.parse("Companies"); dft=xls.parse("Contacts")
        def N(df): df=df.copy(); df.columns=[str(c).strip().lower() for c in df.columns]; return df
        dfc=N(dfc); dft=N(dft)
        conn=get_db(); cfg=load_settings(conn)

        # Companies
        for _,r in dfc.iterrows():
            name=str(r.get("name","")).strip()
            if not name: continue
            category=str(r.get("category","") or ""); subcategory=str(r.get("subcategory","") or "")
            reg_raw=r.get("reg_status_base","Listado")
            # map by case-insensitive match to settings list
            reg_map={_norm(x):x for x in cfg["company_base_statuses"]}
            reg=reg_map.get(_norm(reg_raw), cfg["company_base_statuses"][0])
            city=str(r.get("city","") or ""); state=str(r.get("state","") or ""); notes=str(r.get("notes","") or "")
            cur=conn.execute("SELECT id FROM companies WHERE name=?", (name,)).fetchone()
            if cur:
                conn.execute("""UPDATE companies SET category=?,subcategory=?,reg_status_base=?,city=?,state=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                             (category, subcategory, reg, city, state, notes, cur["id"]))
            else:
                conn.execute("""INSERT INTO companies (name,category,subcategory,reg_status_base,city,state,notes)
                                VALUES (?,?,?,?,?,?,?)""",(name,category,subcategory,reg,city,state,notes))

        # Contacts
        if "company_name" not in dft.columns or "name" not in dft.columns:
            flash("Aba Contacts precisa de 'company_name' e 'name'.","danger"); return redirect(url_for("dashboard"))
        stage_map={_norm(x):x for x in cfg["contact_stages"]}
        for _,r in dft.iterrows():
            comp=str(r.get("company_name","")).strip(); name=str(r.get("name","")).strip()
            if not comp or not name: continue
            company=conn.execute("SELECT id FROM companies WHERE name=?", (comp,)).fetchone()
            if not company:
                conn.execute("INSERT INTO companies (name) VALUES (?)", (comp,))
                company_id=conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            else:
                company_id=company["id"]
            role=str(r.get("role","") or ""); email=str(r.get("email","") or ""); phone=str(r.get("phone","") or "")
            stage=stage_map.get(_norm(r.get("contact_stage","Contato Inicial")), "Contato Inicial")
            try: priority=int(r.get("priority",2) or 2)
            except: priority=2
            next_action=str(r.get("next_action","") or ""); next_date=str(r.get("next_date","") or ""); notes=str(r.get("notes","") or "")
            conn.execute("""INSERT INTO contacts (company_id,name,role,email,phone,contact_stage,priority,next_action,next_date,notes)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (company_id,name,role,email,phone,stage,priority,next_action,next_date,notes))

        rebuild_company_status_view(conn, cfg)  # in case settings changed
        flash("Importação concluída.","success")
    except Exception as e:
        flash(f"Erro ao importar: {e}","danger")
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
