#!/bin/zsh
set -e
cd -- "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
if [[ ! -x .venv/bin/python ]]; then
  print "Crée d’abord l’environnement : python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read "?Appuie sur Entrée pour fermer."
  exit 1
fi
print "Sillage va s’ouvrir dans ton navigateur. Garde cette fenêtre ouverte."
print "Pour arrêter Sillage, appuie sur Ctrl+C."
exec .venv/bin/python server.py --open
