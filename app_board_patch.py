# --- PATCH v4.1.1: add /board and drag move endpoint ---
from flask import jsonify, render_template
from app import app, get_db  # assumes main app is app.py in same folder

@app.route("/board")
def board():
    conn = get_db()
    stages = ["Contato Inicial","Fazer FUP","Marcar Reunião","Reunião Marcada","Acompanhar","Projeto Ganho","Projeto Perdido","Potencial Futuro"]
    columns = {}
    for s in stages:
        rows = conn.execute(
            "SELECT ct.id, ct.name, co.name AS company_name FROM contacts ct JOIN companies co ON co.id=ct.company_id WHERE ct.contact_stage=? ORDER BY ct.priority, co.name, ct.name",
            (s,)
        ).fetchall()
        columns[s] = rows or []
    conn.close()
    return render_template("board.html", columns=columns, stages=stages)

@app.route("/contact/<int:cid>/move", methods=["POST"])
def contact_move(cid):
    from flask import request
    stages = ["Contato Inicial","Fazer FUP","Marcar Reunião","Reunião Marcada","Acompanhar","Projeto Ganho","Projeto Perdido","Potencial Futuro"]
    data = request.get_json(silent=True) or {}
    new_stage = data.get("stage","")
    if new_stage not in stages:
        return jsonify({"ok": False, "error": "stage inválido"}), 400
    conn = get_db()
    with conn:
        conn.execute("UPDATE contacts SET contact_stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_stage, cid))
    return jsonify({"ok": True})
