import AppKit
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    static let shared = AppState()

    enum Phase {
        case idle
        case analyzing(Preview, Double?, String)
        case result(AnalysisResult, Preview)
        case noDetector(MediaKind)
        case failed(String)
    }

    let worker = WorkerClient()
    @Published var models: [ModelInfo] = []
    @Published var enabled: Set<String>
    @Published var downloads: [String: Double] = [:]   // -1 = indeterminate
    @Published var downloadMessages: [String: String] = [:]
    @Published var modelErrors: [String: String] = [:]
    @Published var phase: Phase = .idle
    @Published var showPasteSheet = false
    @Published var recommendedRun: (done: Int, total: Int, current: String)?

    private var analysisTask: Task<Void, Never>?
    private var pendingURL: URL?
    private let defaultsKey = "enabledModels"

    init() {
        enabled = Set(UserDefaults.standard.stringArray(forKey: "enabledModels") ?? [])
        worker.onReady = { [weak self] in Task { await self?.refreshModels() } }
    }

    func startWorker() {
        if worker.status == .idle { worker.start() }
    }

    func restartWorker() {
        analysisTask?.cancel()
        phase = .idle
        worker.restart()
    }

    private func persist() {
        UserDefaults.standard.set(Array(enabled).sorted(), forKey: defaultsKey)
    }

    var isBusy: Bool { if case .analyzing = phase { return true }; return false }

    // MARK: Models

    func refreshModels() async {
        do {
            let ev = try await worker.request(["cmd": "models"])
            if let m = ev.models { models = m }
            if let url = pendingURL { pendingURL = nil; handle(url: url) }
        } catch {
            // surfaced through worker status / log
        }
    }

    func isEnabled(_ m: ModelInfo) -> Bool { enabled.contains(m.id) && m.installed }

    func setEnabled(_ m: ModelInfo, _ on: Bool) {
        modelErrors[m.id] = nil
        if !on {
            enabled.remove(m.id); persist(); return
        }
        if m.installed {
            enabled.insert(m.id); persist(); return
        }
        Task { await download(m) }
    }

    func download(_ m: ModelInfo) async {
        guard downloads[m.id] == nil else { return }
        downloads[m.id] = -1
        modelErrors[m.id] = nil
        do {
            _ = try await worker.request(["cmd": "download", "model": m.id]) { [weak self] f, msg in
                guard let self else { return }
                self.downloads[m.id] = f ?? -1
                if let msg { self.downloadMessages[m.id] = msg }
            }
            await refreshModels()
            enabled.insert(m.id); persist()
        } catch {
            modelErrors[m.id] = error.localizedDescription
        }
        downloads[m.id] = nil
        downloadMessages[m.id] = nil
    }

    var recommended: [ModelInfo] { models.filter { $0.recommended == true } }
    var hasEnabledModel: Bool { models.contains { isEnabled($0) } }
    var recommendedMissingMB: Double {
        recommended.filter { !$0.installed }.reduce(0) { $0 + ($1.sizeMB ?? 0) }
    }

    /// Downloads (if needed) and enables every recommended model, one after another.
    func enableRecommended() async {
        guard recommendedRun == nil else { return }
        let list = recommended
        for (i, m) in list.enumerated() {
            recommendedRun = (i, list.count, m.name)
            if m.installed { enabled.insert(m.id); persist() } else { await download(m) }
        }
        recommendedRun = nil
    }

    func removeFiles(_ m: ModelInfo) async {
        modelErrors[m.id] = nil
        do {
            _ = try await worker.request(["cmd": "delete", "model": m.id])
            enabled.remove(m.id); persist()
            await refreshModels()
        } catch {
            modelErrors[m.id] = error.localizedDescription
        }
    }

    func activeModels(for kind: MediaKind) -> [ModelInfo] {
        models.filter { $0.installed && enabled.contains($0.id) && kind.modelKinds.contains($0.kind) }
    }

    // MARK: Input

    func handle(providers: [NSItemProvider]) {
        guard !isBusy else { return }
        Task {
            if let p = await InputLoader.prepare(providers: providers) { analyze(p) } else { unsupported() }
        }
    }

    func handlePasteboard() {
        guard !isBusy else { return }
        Task {
            if let p = await InputLoader.prepare(pasteboard: .general) { analyze(p) } else { unsupported() }
        }
    }

    func handle(url: URL) {
        guard !isBusy else { return }
        Task {
            if let p = await InputLoader.prepare(url: url) { analyze(p) } else { unsupported() }
        }
    }

    /// Like handle(url:), but waits for the engine's model list if the app was launched by opening a file.
    func open(url: URL) {
        if models.isEmpty { pendingURL = url; startWorker() } else { handle(url: url) }
    }

    func handle(text: String) {
        guard !isBusy, let p = InputLoader.prepare(text: text) else { return }
        analyze(p)
    }

    private func unsupported() {
        phase = .failed("Traels can check images, video, audio and text. This item isn't one of those.")
    }

    func reset() {
        analysisTask?.cancel()
        phase = .idle
    }

    func analyze(_ input: Prepared) {
        if input.remote == nil, activeModels(for: input.kind).isEmpty { phase = .noDetector(input.kind); return }
        phase = .analyzing(input.preview, nil, input.remote == nil ? "Preparing…" : "Downloading from \(input.preview.title)…")
        analysisTask?.cancel()
        analysisTask = Task {
            do {
                var input = input
                if let remote = input.remote {
                    guard let fetched = try await fetch(remote, preview: input.preview) else { return }
                    input = fetched
                }
                let active = activeModels(for: input.kind)
                guard !active.isEmpty else { phase = .noDetector(input.kind); return }
                var payload: [String: Any] = ["cmd": "analyze", "kind": input.kind.rawValue, "models": active.map(\.id)]
                if let t = input.text { payload["text"] = t }
                if let u = input.fileURL { payload["path"] = u.path }
                if let s = input.source { payload["source"] = ["host": s.host, "platform": s.platform] }
                let preview = input.preview
                phase = .analyzing(preview, nil, "Preparing…")
                let ev = try await worker.request(payload) { [weak self] f, msg in
                    guard let self, case .analyzing = self.phase else { return }
                    self.phase = .analyzing(preview, f, msg ?? "Analyzing…")
                }
                guard !Task.isCancelled else { return }
                if let r = ev.result { phase = .result(r, preview) } else { phase = .failed("The engine returned no result.") }
            } catch is CancellationError {
                // reset() already moved us on
            } catch {
                guard !Task.isCancelled else { return }
                phase = .failed(error.localizedDescription)
            }
        }
    }

    /// Has the worker download a pasted or dropped link. nil if cancelled meanwhile.
    private func fetch(_ url: URL, preview: Preview) async throws -> Prepared? {
        let payload: [String: Any] = ["cmd": "fetch", "url": url.absoluteString,
                                      "video_sites": UserDefaults.standard.bool(forKey: "videoSites")]
        let ev = try await worker.request(payload) { [weak self] f, msg in
            guard let self, case .analyzing = self.phase else { return }
            self.phase = .analyzing(preview, f, msg ?? "Downloading…")
        }
        guard !Task.isCancelled else { return nil }
        guard let file = ev.file, let p = InputLoader.prepare(fetched: file) else {
            throw WorkerError(message: "That link didn't lead to an image, video or audio file.")
        }
        return p
    }
}
