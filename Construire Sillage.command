#!/bin/zsh
set -e
cd -- "${0:A:h}"
if ! xcrun --find swiftc >/dev/null 2>&1; then
  print "Installe les outils Apple avec xcode-select --install, puis relance ce fichier."
  exit 1
fi
mkdir -p Sillage.app/Contents/MacOS Sillage.app/Contents/Resources
cp native/Info.plist Sillage.app/Contents/Info.plist
cp native/Sillage.icns Sillage.app/Contents/Resources/Sillage.icns
print -n APPL'????' > Sillage.app/Contents/PkgInfo
xcrun swiftc -target "$(uname -m)-apple-macos13.0" -O native/Sillage.swift -o Sillage.app/Contents/MacOS/Sillage
codesign --force --deep --sign - Sillage.app
print "Application construite : Sillage.app. Garde-la dans ce dossier."
