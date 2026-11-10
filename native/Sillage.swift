import Cocoa
import WebKit
import UniformTypeIdentifiers

final class SillageApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKDownloadDelegate, WKScriptMessageHandler, NSMenuItemValidation {
    var window: NSWindow!
    var webView: WKWebView!
    var process: Process?
    var sessionURL: URL!
    var root: URL!
    var dataDirectory: URL!
    var origin: String = ""
    var timer: Timer?
    var startedAt = Date()
    var logHandle: FileHandle?
    var downloads: [WKDownload] = []
    var conversionBusy = false
    var audioSelected = false
    var selectedFormat = "wav"

    func applicationDidFinishLaunching(_ notification: Notification) {
        // 1. Données dans ~/Library/Application Support/Sillage (jamais dans l'app : elle est en lecture seule)
        let resourceURL = Bundle.main.resourceURL ?? Bundle.main.bundleURL
        let externalRoot = Bundle.main.bundleURL.deletingLastPathComponent()
        guard let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            fail("Impossible d’ouvrir le dossier de Sillage.", detail: "Dossier Application Support introuvable.")
            return
        }
        dataDirectory = appSupport.appendingPathComponent("Sillage")
        do {
            try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        } catch {
            fail("Impossible d’ouvrir le dossier de Sillage.", detail: error.localizedDescription)
            return
        }

        sessionURL = dataDirectory.appendingPathComponent("native-session-\(ProcessInfo.processInfo.processIdentifier).json")
        createMenu()

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1060, height: 800), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Sillage"
        window.titlebarAppearsTransparent = true
        window.setFrameAutosaveName("SillageWindow")
        window.minSize = NSSize(width: 390, height: 600)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        window.backgroundColor = .windowBackgroundColor

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.userContentController.add(self, name: "sillage")
        config.mediaTypesRequiringUserActionForPlayback = []

        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        window.contentView = webView
        webView.loadHTMLString("<html><body style='background:Canvas;color:CanvasText;color-scheme:light dark;font:22px -apple-system;text-align:center;padding-top:30vh'>sillage.<p style='font-size:13px;color:#8b96a8'>Ton studio se prépare…</p></body></html>", baseURL: nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        // 2. Moteur : version autonome embarquée (DMG) en priorité, sinon .venv à côté de l'app (développement)
        let bundledBackend = resourceURL.appendingPathComponent("sillage-backend/sillage-backend")
        let devPython = externalRoot.appendingPathComponent(".venv/bin/python")
        let executable: URL
        var arguments: [String]
        if FileManager.default.isExecutableFile(atPath: bundledBackend.path) {
            executable = bundledBackend
            arguments = []
            root = bundledBackend.deletingLastPathComponent()
        } else if FileManager.default.isExecutableFile(atPath: devPython.path) {
            executable = devPython
            arguments = [externalRoot.appendingPathComponent("server.py").path]
            root = externalRoot
        } else {
            fail("Sillage est incomplet.", detail: "Le moteur est introuvable dans l’application. Télécharge à nouveau Sillage.dmg.")
            return
        }
        arguments += ["--session-file", sessionURL.path, "--data-dir", dataDirectory.path]

        let logURL = dataDirectory.appendingPathComponent("sillage.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        logHandle = try? FileHandle(forWritingTo: logURL)

        let task = Process()
        task.executableURL = executable
        task.arguments = arguments
        task.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PYTHONUNBUFFERED"] = "1"
        // Node embarqué : yt-dlp en a besoin pour YouTube, inutile de demander à l'utilisateur de l'installer
        let bundledNode = resourceURL.appendingPathComponent("bin/node")
        if FileManager.default.isExecutableFile(atPath: bundledNode.path) {
            environment["SILLAGE_NODE"] = bundledNode.path
        }
        task.environment = environment
        task.standardOutput = logHandle
        task.standardError = logHandle
        task.terminationHandler = { [weak self] task in
            DispatchQueue.main.async {
                guard let self = self, self.process != nil else { return }
                self.timer?.invalidate()
                self.fail("Sillage s’est arrêté.", detail: "Relance l’application. Le détail est disponible dans le journal de Sillage.")
            }
        }
        process = task

        do { try task.run() }
        catch { fail("Impossible de lancer Sillage.", detail: error.localizedDescription); return }

        startedAt = Date()
        timer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in self?.checkReady() }
    }

    func createMenu() {
        let menu = NSMenu()
        func section(_ title: String) -> NSMenu {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let submenu = NSMenu(title: title)
            item.submenu = submenu
            menu.addItem(item)
            return submenu
        }
        let appMenu = section("Sillage")
        appMenu.addItem(withTitle: "À propos de Sillage", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Masquer Sillage", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quitter Sillage", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let file = section("Fichier")
        file.addItem(withTitle: "Nouvelle conversion", action: #selector(newConversion), keyEquivalent: "n")
        file.addItem(withTitle: "Importer une playlist YouTube", action: #selector(importPlaylist), keyEquivalent: "")
        file.addItem(withTitle: "Importer une playlist Musique…", action: #selector(importMusic), keyEquivalent: "i")
        file.addItem(withTitle: "Choisir le dossier d’import…", action: #selector(chooseDirectory), keyEquivalent: "")
        file.addItem(NSMenuItem.separator())
        file.addItem(withTitle: "Saisir un lien", action: #selector(focusURL), keyEquivalent: "l")
        file.addItem(withTitle: "Convertir", action: #selector(convert), keyEquivalent: "\r")
        file.addItem(withTitle: "Annuler la conversion", action: #selector(cancelConversion), keyEquivalent: ".")
        file.addItem(NSMenuItem.separator())
        file.addItem(withTitle: "Enregistrer l’audio…", action: #selector(saveAudio), keyEquivalent: "s")
        let folder = file.addItem(withTitle: "Afficher les fichiers audio", action: #selector(showFiles), keyEquivalent: "o")
        folder.keyEquivalentModifierMask = [.command, .shift]
        let edit = section("Édition")
        edit.addItem(withTitle: "Annuler", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Couper", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copier", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Coller", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Tout sélectionner", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let format = section("Format")
        format.addItem(withTitle: "WAV · Pour le montage", action: #selector(chooseWav), keyEquivalent: "")
        format.addItem(withTitle: "MP3 · Pour l’écoute", action: #selector(chooseMp3), keyEquivalent: "")
        let view = section("Présentation")
        view.addItem(withTitle: "Écouter ou mettre en pause", action: #selector(playAudio), keyEquivalent: "")
        view.addItem(NSMenuItem.separator())
        view.addItem(withTitle: "Agrandir", action: #selector(zoomIn), keyEquivalent: "+")
        view.addItem(withTitle: "Réduire", action: #selector(zoomOut), keyEquivalent: "-")
        view.addItem(withTitle: "Taille réelle", action: #selector(resetZoom), keyEquivalent: "0")
        let help = section("Aide")
        help.addItem(withTitle: "Aide de Sillage", action: #selector(showHelp), keyEquivalent: "?")
        NSApp.mainMenu = menu
        NSApp.helpMenu = help
    }

    func command(_ name: String) { webView?.evaluateJavaScript("window.sillageCommand?.('\(name)')", completionHandler: nil) }
    @objc func newConversion() { command("new") }
    @objc func importPlaylist() { command("playlist") }
    @objc func importMusic() { command("importFile") }
    @objc func focusURL() { command("focus") }
    @objc func convert() { command("convert") }
    @objc func cancelConversion() { command("cancel") }
    @objc func saveAudio() { command("save") }
    @objc func playAudio() { command("play") }
    @objc func chooseWav() { command("wav") }
    @objc func chooseMp3() { command("mp3") }
    @objc func showHelp() { command("help") }
    @objc func zoomIn() { webView.pageZoom = min(2, webView.pageZoom + 0.1) }
    @objc func zoomOut() { webView.pageZoom = max(1, webView.pageZoom - 0.1) }
    @objc func resetZoom() { webView.pageZoom = 1 }

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        switch item.action {
        case #selector(newConversion), #selector(focusURL), #selector(convert), #selector(importMusic), #selector(chooseDirectory): return !conversionBusy
        case #selector(cancelConversion): return conversionBusy
        case #selector(saveAudio), #selector(playAudio): return audioSelected && !conversionBusy
        case #selector(chooseWav): item.state = selectedFormat == "wav" ? .on : .off; return !conversionBusy
        case #selector(chooseMp3): item.state = selectedFormat == "mp3" ? .on : .off; return !conversionBusy
        case #selector(zoomIn): return webView != nil && webView.pageZoom < 1.99
        case #selector(zoomOut): return webView != nil && webView.pageZoom > 1.01
        default: return true
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              let url = message.frameInfo.request.url,
              url.scheme == "http", url.host == "127.0.0.1", url.port == URL(string: origin)?.port,
              let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "paste":
            guard !conversionBusy, let value = NSPasteboard.general.string(forType: .string) else { return }
            if let data = try? JSONSerialization.data(withJSONObject: [value]), let json = String(data: data, encoding: .utf8) {
                webView.evaluateJavaScript("window.sillagePaste?.(\(json)[0])", completionHandler: nil)
            }
        case "files": showFiles()
        case "importFile": chooseImportFile()
        case "chooseDirectory": chooseDirectory()
        case "openDirectory":
            if let path = body["path"] as? String {
                let url = URL(fileURLWithPath: path).standardizedFileURL
                var isDirectory: ObjCBool = false
                if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue {
                    NSWorkspace.shared.open(url)
                }
            }
        case "youtube":
            if let value = body["url"] as? String, let url = URL(string: value),
               url.scheme == "https", ["www.youtube.com", "youtube.com", "youtu.be"].contains(url.host ?? ""),
               url.user == nil, url.password == nil, url.port == nil {
                NSWorkspace.shared.open(url)
            }
        case "state":
            conversionBusy = body["busy"] as? Bool ?? false
            audioSelected = body["canSave"] as? Bool ?? false
            selectedFormat = body["format"] as? String == "mp3" ? "mp3" : "wav"
        default: break
        }
    }

    @objc func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "Sillage", .applicationVersion: "1.2", .credits: NSAttributedString(string: "Le son, simplement.\nVidéos, playlists et imports Musique.\nConversion audio locale avec yt-dlp et FFmpeg.")])
    }
    @objc func showFiles() {
        if let downloadsURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first {
            NSWorkspace.shared.open(downloadsURL)
        }
    }
    func deliver(_ function: String, _ value: Any) {
        if let data = try? JSONSerialization.data(withJSONObject: [value]), let json = String(data: data, encoding: .utf8) {
            webView.evaluateJavaScript("window.\(function)?.(\(json)[0])", completionHandler: nil)
        }
    }

    func chooseImportFile() {
        guard !conversionBusy, window.attachedSheet == nil else { return }
        let panel = NSOpenPanel()
        panel.title = "Importer une playlist de Musique"
        panel.message = "Choisis un export XML ou texte. Les titres seront recherchés sur YouTube."
        panel.prompt = "Importer"
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = ["xml", "txt", "tsv", "csv"].compactMap { UTType(filenameExtension: $0) }
        panel.beginSheetModal(for: window) { [weak self] result in
            guard let self = self, result == .OK, let url = panel.url else { return }
            do {
                let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
                guard let size = attributes[.size] as? NSNumber, size.intValue <= 2 * 1024 * 1024 else {
                    self.deliver("sillageImportError", "Le fichier doit faire 2 Mo maximum."); return
                }
                let data = try Data(contentsOf: url)
                self.deliver("sillageImportFile", ["filename": url.lastPathComponent, "content": data.base64EncodedString()])
            } catch {
                self.deliver("sillageImportError", "Impossible de lire ce fichier. Choisis-le à nouveau.")
            }
        }
    }

    @objc func chooseDirectory() {
        guard !conversionBusy, window.attachedSheet == nil else { return }
        let panel = NSOpenPanel()
        panel.title = "Choisir le dossier des audios"
        panel.message = "Sillage y créera un sous-dossier pour chaque import."
        panel.prompt = "Choisir ce dossier"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        panel.beginSheetModal(for: window) { [weak self] result in
            guard let self = self, result == .OK, let url = panel.url else { return }
            self.deliver("sillageDirectory", url.path)
        }
    }

    func checkReady() {
        if let data = try? Data(contentsOf: sessionURL),
           let session = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let urlString = session["url"] as? String, let url = URL(string: urlString),
           let localOrigin = session["origin"] as? String {
            origin = localOrigin
            timer?.invalidate()
            webView.load(URLRequest(url: url))
        } else if Date().timeIntervalSince(startedAt) > 90 {
            timer?.invalidate()
            fail("Le démarrage prend trop de temps.", detail: "Relance Sillage. Si le problème persiste, consulte le journal dans ~/Library/Application Support/Sillage/sillage.log.")
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" { decisionHandler(.allow); return }
        if url.host == "127.0.0.1", url.scheme == "http", url.port == URL(string: origin)?.port {
            decisionHandler(action.shouldPerformDownload || url.path.hasPrefix("/api/download/") ? .download : .allow)
        } else { decisionHandler(.cancel) }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        downloads.append(download)
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.title = "Enregistrer ton audio"
        panel.nameFieldStringValue = suggestedFilename
        panel.canCreateDirectories = true
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        if suggestedFilename.lowercased().hasSuffix(".wav") { panel.allowedContentTypes = [.wav] }
        else if suggestedFilename.lowercased().hasSuffix(".mp3") { panel.allowedContentTypes = [.mp3] }
        panel.beginSheetModal(for: window) { result in
            completionHandler(result == .OK ? panel.url : nil)
        }
    }

    func downloadDidFinish(_ download: WKDownload) { downloads.removeAll { $0 === download } }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        downloads.removeAll { $0 === download }
        if (error as NSError).code != NSURLErrorCancelled {
            let alert = NSAlert()
            alert.messageText = "L’enregistrement n’a pas abouti."
            alert.informativeText = "Ton audio reste disponible dans Sillage. Réessaie de le télécharger."
            alert.beginSheetModal(for: window)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if !downloads.isEmpty || conversionBusy {
            let alert = NSAlert()
            alert.messageText = "Un traitement est encore en cours."
            alert.informativeText = "Quitter Sillage l’interrompt. Les audios terminés restent disponibles ; un import groupé pourra être repris."
            alert.addButton(withTitle: "Rester dans Sillage")
            alert.addButton(withTitle: "Quitter")
            if alert.runModal() == .alertFirstButtonReturn { return .terminateCancel }
        }
        return .terminateNow
    }
    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        let running = process
        process = nil
        running?.terminationHandler = nil
        if running?.isRunning == true {
            running?.terminate()
            running?.waitUntilExit()
        }
        if let sessionURL { try? FileManager.default.removeItem(at: sessionURL) }
        try? logHandle?.close()
    }

    func fail(_ message: String, detail: String) {
        let alert = NSAlert()
        alert.messageText = message
        alert.informativeText = detail
        alert.addButton(withTitle: "Fermer")
        alert.runModal()
        NSApp.terminate(nil)
    }
}

let application = NSApplication.shared
let delegate = SillageApp()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
