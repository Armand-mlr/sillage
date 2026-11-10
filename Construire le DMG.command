#!/bin/zsh
# Fabrique dist/Sillage-<version>-<arch>.dmg : une app autonome (Python, yt-dlp, FFmpeg et Node embarqués).
# L'utilisateur final n'a RIEN à installer.
#
# Signature optionnelle (recommandée pour une diffusion publique) :
#   export SILLAGE_SIGN_ID="Developer ID Application: Prénom Nom (TEAMID)"
#   export SILLAGE_NOTARY_PROFILE="sillage"   # créé une fois avec : xcrun notarytool store-credentials sillage
set -euo pipefail
cd -- "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

VERSION=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" native/Info.plist)
ARCH=$(uname -m)                                   # arm64 (Apple Silicon) ou x86_64 (Intel)
NODE_ARCH=$([[ $ARCH == arm64 ]] && print arm64 || print x64)
NODE_MAJOR=24
PY=${SILLAGE_PYTHON:-python3}
SIGN_ID=${SILLAGE_SIGN_ID:--}                      # "-" = signature locale (ad hoc)
WORK="${TMPDIR:-/tmp/}sillage-build"
APP="$WORK/Sillage.app"
DMG="$PWD/dist/Sillage-$VERSION-$ARCH.dmg"

step() { print "\n\033[1m▸ $1\033[0m"; }

step "Vérifications"
xcrun --find swiftc >/dev/null || { print "Installe les outils Apple : xcode-select --install"; exit 1; }
"$PY" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ requis"'
rm -rf "$WORK" build dist/sillage-backend
mkdir -p "$WORK" dist

step "Environnement de build Python"
"$PY" -m venv "$WORK/venv"
"$WORK/venv/bin/python" -m pip install -q --upgrade pip
"$WORK/venv/bin/python" -m pip install -q -r requirements.txt pyinstaller

step "Moteur Python autonome (PyInstaller)"
"$WORK/venv/bin/pyinstaller" --noconfirm --clean --distpath "$WORK/pyi" --workpath "$WORK/pyi-build" sillage-backend.spec

step "Node.js $NODE_MAJOR ($NODE_ARCH) pour le solveur YouTube"
BASE="https://nodejs.org/dist/latest-v$NODE_MAJOR.x"
LINE=$(curl -fsSL "$BASE/SHASUMS256.txt" | grep "darwin-$NODE_ARCH.tar.gz$")
NODE_SHA=${LINE%% *}; NODE_TGZ=${LINE##* }
curl -fsSL "$BASE/$NODE_TGZ" -o "$WORK/$NODE_TGZ"
print "$NODE_SHA  $WORK/$NODE_TGZ" | shasum -a 256 -c -
tar -xzf "$WORK/$NODE_TGZ" -C "$WORK"
NODE_DIR="$WORK/${NODE_TGZ%.tar.gz}"

step "Fenêtre native (Swift)"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/bin"
cp native/Info.plist "$APP/Contents/Info.plist"
cp native/Sillage.icns "$APP/Contents/Resources/"
print -n 'APPL????' > "$APP/Contents/PkgInfo"
xcrun swiftc -O -target "$ARCH-apple-macos13.0" native/Sillage.swift -o "$APP/Contents/MacOS/Sillage"

step "Assemblage de Sillage.app"
ditto "$WORK/pyi/sillage-backend" "$APP/Contents/Resources/sillage-backend"
ditto "$NODE_DIR/bin/node" "$APP/Contents/Resources/bin/node"
cp "$NODE_DIR/LICENSE" "$APP/Contents/Resources/bin/NODE-LICENSE"
xattr -cr "$APP"

step "Signature ($([[ $SIGN_ID == - ]] && print 'locale, ad hoc' || print "$SIGN_ID"))"
SIGN=(codesign --force --sign "$SIGN_ID")
[[ $SIGN_ID != - ]] && SIGN+=(--options runtime --timestamp)
# De l'intérieur vers l'extérieur : chaque binaire, puis l'exécutable qui en a besoin, puis l'app.
find "$APP/Contents/Resources" -type f | while read -r f; do
  if file -b "$f" | grep -q "Mach-O"; then "${SIGN[@]}" --entitlements native/entitlements.plist "$f" 2>/dev/null || "${SIGN[@]}" "$f"; fi
done
"${SIGN[@]}" --entitlements native/entitlements.plist "$APP/Contents/Resources/sillage-backend/sillage-backend"
xattr -cr "$APP"
"${SIGN[@]}" --entitlements native/entitlements.plist "$APP"
codesign --verify --deep --verbose=2 "$APP"

step "Image disque"
STAGE="$WORK/dmg"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Sillage.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -quiet -volname "Sillage" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG"

if [[ $SIGN_ID != - ]]; then
  codesign --force --sign "$SIGN_ID" --timestamp "$DMG"
  if [[ -n ${SILLAGE_NOTARY_PROFILE:-} ]]; then
    step "Notarisation Apple (quelques minutes)"
    xcrun notarytool submit "$DMG" --keychain-profile "$SILLAGE_NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
    spctl --assess --type open --context context:primary-signature -v "$DMG"
  fi
fi

print "\n✅ $DMG ($(du -h "$DMG" | cut -f1))"
if [[ $SIGN_ID == - ]]; then
  print "⚠️  Non notarisée : sur un autre Mac, macOS demandera une autorisation au premier lancement (voir README)."
fi
