"""Prueft VOR dem Hochladen, dass nichts Geheimes ins oeffentliche Repo gelangt.

Aufruf: python scripts/pruefe_upload.py

Geprueft wird:
  1. .env steht in .gitignore (und wird, falls git installiert ist, wirklich ignoriert).
  2. In keiner Datei, die hochgeladen wuerde, steht einer der Keys aus der .env.
  3. In keiner Datei steht etwas, das wie ein Alpaca-Key oder eine Kontonummer aussieht.
Die Keys selbst werden dabei nie angezeigt.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".venv", "__pycache__", "data", ".git"}
SKIP_PATHS = {os.path.join("logs", "replay")}
PATTERNS = {
    "sieht aus wie ein Alpaca-Key": re.compile(r"\b(PK|AK|CK)[A-Z0-9]{16,}\b"),
    "sieht aus wie eine Kontonummer": re.compile(r"\bPA[0-9][A-Z0-9]{8,}\b"),
    "Feld account_number": re.compile(r"account_number\W+\w", re.I),
}


def env_values():
    values = []
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    v = line.split("=", 1)[1].strip().strip("'\"")
                    if len(v) >= 8:
                        values.append(v)
    except OSError:
        pass
    return values


def upload_files():
    for folder, dirs, files in os.walk(ROOT):
        rel = os.path.relpath(folder, ROOT)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and os.path.normpath(os.path.join(rel, d)) not in SKIP_PATHS]
        for name in files:
            if name == ".env" or name.endswith(".pyc"):
                continue
            yield os.path.normpath(os.path.join(rel, name))


def main():
    problems = []
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
        ignore = [line.strip() for line in f]
    if ".env" not in ignore:
        problems.append(".env steht NICHT in .gitignore")
    if os.path.isdir(os.path.join(ROOT, ".git")):
        try:
            tracked = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True,
                                     text=True).stdout.strip()
            if tracked:
                problems.append(".env ist bei git angemeldet (git rm --cached .env)")
        except OSError:
            pass
    secrets = env_values()
    count = 0
    for rel in upload_files():
        count += 1
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        if any(s in text for s in secrets):
            problems.append("%s: enthaelt einen Key aus der .env" % rel)
        if rel == os.path.join("scripts", "pruefe_upload.py"):
            continue
        for what, pat in PATTERNS.items():
            if pat.search(text):
                problems.append("%s: %s" % (rel, what))
    print("%d Dateien geprueft, %d Keys aus der .env gesucht." % (count, len(secrets)))
    if problems:
        print("NICHT HOCHLADEN - gefunden:")
        for p in problems:
            print("  - " + p)
        return 1
    print("In Ordnung: .env wird nicht hochgeladen, keine Keys und keine Kontonummern gefunden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
