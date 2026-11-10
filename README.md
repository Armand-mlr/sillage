# Sillage

Une application locale pour macOS : transformer l’audio de vidéos YouTube en WAV ou MP3, à l’unité ou par playlist.

## Fonctionnalités

- **Vidéo** : lien YouTube, conversion WAV/MP3, écoute et enregistrement.
- **Playlist YouTube** : lecture de la liste, sélection, téléchargement séquentiel et dossier numéroté.
- **Import Musique** : export XML ou texte de Musique sur Mac, ou liste `Artiste | Titre | Version` ; recherche de propositions sur YouTube.
- Choix des correspondances, lien manuel, arrêt/reprise et historique local.
- Interface française, thème clair/sombre de macOS et fenêtre native.

## Installer sur Mac (utilisateur)

1. Télécharge `Sillage-<version>-arm64.dmg` (Apple Silicon) ou `-x86_64.dmg` (Intel) depuis les Releases.
2. Ouvre le DMG et glisse **Sillage** dans **Applications**.
3. Lance Sillage. Rien d’autre à installer : Python, yt-dlp, FFmpeg et Node.js sont inclus.

Si la version n’est pas notarisée, macOS bloque le premier lancement :
- macOS 15 et plus récent : ouvre Sillage une fois, puis **Réglages Système → Confidentialité et sécurité → Ouvrir quand même**.
- macOS 13 et 14 : **clic droit sur Sillage → Ouvrir → Ouvrir**.

Historique et audios : `~/Library/Application Support/Sillage`. Journal : `sillage.log` dans ce dossier.

## Créer le DMG (développeur)

Prérequis sur le Mac de build : outils Apple (`xcode-select --install`) et Python 3.10+ installé depuis python.org (les Python Homebrew sont compilés pour ta version de macOS et peuvent refuser de tourner sur des Mac plus anciens).

```sh
zsh "Construire le DMG.command"
```

Résultat : `dist/Sillage-<version>-<arch>.dmg`. Le DMG cible l’architecture du Mac de build : fais-le sur un Mac Apple Silicon pour `arm64`, sur un Mac Intel pour `x86_64`.

Pour une diffusion sans avertissement Gatekeeper (compte Apple Developer requis) :

```sh
xcrun notarytool store-credentials sillage   # une seule fois
export SILLAGE_SIGN_ID="Developer ID Application: Prénom Nom (TEAMID)"
export SILLAGE_NOTARY_PROFILE=sillage
zsh "Construire le DMG.command"
```

yt-dlp évolue souvent pour suivre YouTube : quand les téléchargements cassent, mets à jour la version dans `requirements.txt` et reconstruis le DMG.

## Développement

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py --open          # version navigateur
zsh "Construire Sillage.command"           # fenêtre native qui utilise .venv
```

Une seule instance peut utiliser le même historique. Dans le navigateur, le choix natif du dossier n’est pas disponible.

## Windows

Installateur : `Sillage-<version>-Windows-Setup.exe` (Windows 10/11 64 bits). Il installe Sillage pour l'utilisateur, sans droits administrateur. Au premier lancement, SmartScreen peut afficher un avertissement : **Informations complémentaires → Exécuter quand même**.

Construction : sur GitHub, onglet **Actions → Installateur Windows → Run workflow**, puis télécharge l'artefact. Pousser un tag `v*` l'ajoute aussi à la Release. Fichiers : `windows/` et `.github/workflows/windows.yml`. Données : `%LOCALAPPDATA%\Sillage`.

## Utiliser les imports

**Playlist YouTube** : colle le lien, lis la playlist, choisis les vidéos, le format et le dossier, puis lance le téléchargement. Un bilan texte accompagne les fichiers. Maximum 500 entrées ; les Mix automatiques et contenus nécessitant une connexion ne sont pas pris en charge.

**Musique sur Mac** : sélectionne une playlist, puis Fichier → Bibliothèque → Exporter la playlist → XML (ou texte). Importe ce fichier dans Sillage, lance les recherches et vérifie les propositions. Exporte une seule playlist, pas la bibliothèque entière. Limite : 2 Mo / 500 titres. L’export contient les métadonnées, pas l’audio Apple Music. Sillage ne se connecte pas au compte Apple Music.

Une liste manuelle utilise une ligne par morceau :

```text
Artiste | Titre
Artiste | Titre | Extended Mix
```

Les correspondances sont des propositions fondées sur les métadonnées, pas une reconnaissance audio. Les versions incertaines restent décochées ; même une correspondance probable mérite vérification.

## Qualité et usage

Le WAV est du PCM 16 bits : il évite une nouvelle compression avec perte, mais ne restaure pas les informations perdues dans la source YouTube. Le MP3 est encodé à 320 kb/s. Fréquence et canaux de la source sont conservés en WAV ; une source multicanal occupe beaucoup d’espace. Ce n’est pas une garantie de qualité studio ou un remplacement de fichiers provenant des artistes et boutiques musicales.

Utilise l’application pour les contenus que tu es autorisé à télécharger. La disponibilité d’une vidéo ne vaut pas autorisation de réutilisation. YouTube peut changer ou refuser certaines opérations.

## Données locales

`~/Library/Application Support/Sillage` contient les audios, historiques et fichiers de session. Les imports produisent aussi une copie indépendante dans le dossier choisi : prévoir de l’espace pour les deux copies. Rien de cela n’est livré dans ce dépôt ; `.gitignore` exclut ces fichiers. Ne force pas leur ajout à Git.

Internet sert aux téléchargements, recherches et miniatures YouTube. La conversion s’effectue sur le Mac. Le serveur écoute uniquement sur `127.0.0.1`, avec une clé de session, un cookie et des contrôles d’origine. Aucun service d’IA ni clé d’API n’est nécessaire pour utiliser l’app.

## Architecture et vérification

- `native/Sillage.swift` : fenêtre Cocoa/WKWebView et dialogues macOS.
- `web/` : interface HTML/CSS/JavaScript.
- `server.py` : serveur local et bibliothèque.
- `worker.py`, `audio.py` : récupération avec yt-dlp et conversion FFmpeg.
- `imports.py`, `import_worker.py`, `batches.py` : lecture des exports, recherche, file de traitement et reprise.

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

Les tests couvrent les parseurs, les conversions FFmpeg, les accès au serveur, l’annulation, la reprise et les exports sans écrasement. Ils ne téléchargent pas de vidéos YouTube.

## Dépendances et distribution

[yt-dlp](https://github.com/yt-dlp/yt-dlp), [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) et [FFmpeg](https://ffmpeg.org/) conservent leurs licences respectives. Leurs binaires ne sont pas inclus dans ce dépôt. Aucune licence de réutilisation du code propre à Sillage n’a encore été choisie : rendre ce dépôt public ne suffira pas à en faire un projet sous licence libre.
