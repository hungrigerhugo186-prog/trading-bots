#!/usr/bin/env bash
# Schreibt Protokolle, Zustand und Bericht ins Repo. Mehrere Jobs koennen gleichzeitig
# speichern, deshalb wird bei einem Zusammenstoss neu geholt und noch einmal versucht.
set -u
# Sicherheitsnetz: steht irgendwo etwas, das wie ein Key oder eine Kontonummer aussieht,
# wird NICHTS gespeichert und der Job schlaegt fehl.
python scripts/pruefe_upload.py || exit 1
git config user.name "trading-bots"
git config user.email "actions@users.noreply.github.com"
git add logs/ state/ reports/bericht.md 2>/dev/null || true
if git diff --cached --quiet; then
  echo "Nichts Neues zu speichern."
  exit 0
fi
git commit -m "${1:-Protokoll} $(date -u +%F)"
for i in 1 2 3 4 5 6; do
  if git pull --rebase --autostash && git push; then
    echo "Gespeichert."
    exit 0
  fi
  sleep $((RANDOM % 20 + 5))
done
echo "Speichern fehlgeschlagen."
exit 1
