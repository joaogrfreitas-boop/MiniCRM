# -*- coding: utf-8 -*-
"""
Robust launcher for Mini-CRM on Windows/macOS/Linux.
- Ensures working directory is the folder of this file (SQLite path ok)
- Initializes DB
- Opens browser automatically
"""
import os, sys, threading, time, webbrowser

def set_cwd():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base)

def open_browser():
    time.sleep(1.5)
    try:
        webbrowser.open("http://127.0.0.1:5000")
    except Exception:
        pass

def main():
    set_cwd()
    try:
        from app import app, init_db  # app.py must be in the same folder
    except Exception as e:
        print("Erro ao importar app.py:", e)
        print("Verifique se as dependências estão instaladas e se app.py está na mesma pasta.")
        sys.exit(1)
    init_db()
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)

if __name__ == "__main__":
    main()
