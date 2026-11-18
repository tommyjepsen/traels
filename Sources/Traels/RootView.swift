import SwiftUI
import UniformTypeIdentifiers

struct RootView: View {
    @EnvironmentObject var app: AppState

    var body: some View {
        EngineGate(worker: app.worker)
            .background(Color(nsColor: .windowBackgroundColor))
            .background(WindowTagger())
            .sheet(isPresented: $app.showPasteSheet) { PasteTextSheet().environmentObject(app) }
    }
}

/// Shows setup / error states until the worker is ready, then the main content.
struct EngineGate: View {
    @ObservedObject var worker: WorkerClient

    var body: some View {
        switch worker.status {
        case .ready:
            MainContent()
        case .uvMissing:
            UVMissingCard()
        case .backendMissing:
            MessageCard(symbol: "exclamationmark.triangle", title: "Engine files not found",
                        message: "Traels couldn't find its analysis backend folder.", showRetry: true)
        case .stopped(let msg):
            MessageCard(symbol: "exclamationmark.triangle", title: "The local engine stopped",
                        message: msg + (worker.latestLog.isEmpty ? "" : "\n\n" + worker.latestLog), showRetry: true)
        case .idle, .starting:
            SetupView(worker: worker)
        }
    }
}

struct SetupView: View {
    @ObservedObject var worker: WorkerClient
    var body: some View {
        VStack(spacing: 16) {
            ProgressView().controlSize(.large)
            Text("Setting up the local engine… (first launch only)")
                .font(.headline)
            Text("This can take a few minutes while the dependencies are installed.")
                .font(.subheadline).foregroundStyle(.secondary)
            if !worker.latestLog.isEmpty {
                Text(worker.latestLog)
                    .font(.caption.monospaced())
                    .foregroundStyle(.tertiary)
                    .lineLimit(2).truncationMode(.middle)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct MessageCard: View {
    @EnvironmentObject var app: AppState
    var symbol: String
    var title: String
    var message: String
    var showRetry = false
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: symbol).font(.system(size: 40)).foregroundStyle(.secondary)
            Text(title).font(.title3.weight(.semibold))
            Text(message).foregroundStyle(.secondary).multilineTextAlignment(.center)
                .textSelection(.enabled)
            if showRetry { Button("Try Again") { app.restartWorker() }.controlSize(.large) }
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct UVMissingCard: View {
    @EnvironmentObject var app: AppState
    private let cmd = "curl -LsSf https://astral.sh/uv/install.sh | sh"
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "terminal").font(.system(size: 40)).foregroundStyle(.secondary)
            Text("uv is required").font(.title3.weight(.semibold))
            Text("Traels runs its detectors with uv, a fast Python package manager. Install it with:")
                .foregroundStyle(.secondary).multilineTextAlignment(.center)
            Text(cmd).font(.callout.monospaced()).textSelection(.enabled)
                .padding(10)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            Text("or  brew install uv").font(.callout.monospaced()).foregroundStyle(.secondary)
            HStack {
                Button("Copy Command") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(cmd, forType: .string)
                }
                Button("Check Again") { app.restartWorker() }.buttonStyle(.borderedProminent)
            }
            .controlSize(.large)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct MainContent: View {
    @EnvironmentObject var app: AppState
    @State private var targeted = false

    var body: some View {
        Group {
            switch app.phase {
            case .idle: DropZone(targeted: targeted)
            case .analyzing(let p, let f, let m): AnalyzingView(preview: p, fraction: f, message: m)
            case .result(let r, let p): ResultView(result: r, preview: p)
            case .noDetector(let k): NoDetectorCard(kind: k)
            case .failed(let m): MessageCard(symbol: "exclamationmark.triangle", title: "Couldn't check that", message: m)
                    .overlay(alignment: .bottom) {
                        Button("Check Another") { app.reset() }.controlSize(.large).padding(.bottom, 40)
                    }
            }
        }
        .animation(.smooth(duration: 0.3), value: phaseKey)
        .onDrop(of: InputLoader.dropTypes, isTargeted: $targeted) { providers in
            app.handle(providers: providers)
            return true
        }
        .onPasteCommand(of: InputLoader.dropTypes) { providers in
            app.handle(providers: providers)
        }
    }

    private var phaseKey: Int {
        switch app.phase {
        case .idle: return 0
        case .analyzing: return 1
        case .result: return 2
        case .noDetector: return 3
        case .failed: return 4
        }
    }
}

struct DropZone: View {
    @EnvironmentObject var app: AppState
    var targeted: Bool
    @AppStorage("settingsTab") private var settingsTab = "models"

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(headline)
                .font(.system(size: 34, weight: .semibold))
                .tracking(-0.9)
                .lineSpacing(2)
                .foregroundStyle(Palette.faint)
                .fixedSize(horizontal: false, vertical: true)
                .environment(\.openURL, OpenURLAction { _ in
                    app.showPasteSheet = true
                    return .handled
                })

            Spacer(minLength: 24)

            if !app.models.isEmpty && !app.hasEnabledModel {
                FirstRunCard().padding(.bottom, 16)
            }

            HStack(spacing: 8) {
                KindTile(kind: .image, targeted: targeted) { FilePicker.choose { app.handle(url: $0) } }
                KindTile(kind: .video, targeted: targeted) { FilePicker.choose { app.handle(url: $0) } }
                KindTile(kind: .audio, targeted: targeted) { FilePicker.choose { app.handle(url: $0) } }
                KindTile(kind: .text, targeted: targeted) { app.showPasteSheet = true }
            }

            HStack {
                Text("⌘V to paste · ⌘O to choose")
                Spacer()
                Label("Runs on this Mac", systemImage: "lock")
                Text("·")
                SettingsLink { Label("Settings", systemImage: "gearshape") }
                    .buttonStyle(.plain)
                    .simultaneousGesture(TapGesture().onEnded { settingsTab = "models" })
                    .help("Choose which detectors to use (⌘,)")
            }
            .font(.caption)
            .foregroundStyle(Palette.mute)
            .padding(.top, 14)
        }
        .padding(.horizontal, 36)
        .padding(.top, 40)
        .padding(.bottom, 28)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Palette.window)
        .animation(.easeOut(duration: 0.15), value: targeted)
    }

    /// "Drop anything." in ink, the rest faint, with "paste of text" as an underlined link to the paste sheet.
    private var headline: AttributedString {
        var lead = AttributedString(targeted ? "Let go." : "Drop anything.")
        lead.foregroundColor = targeted ? Color.accentColor : Palette.ink
        var before = AttributedString(" An image, a video, a voice note, a link or a ")
        before.foregroundColor = Palette.faint
        var link = AttributedString("paste of text")
        link.foregroundColor = Palette.ink
        link.underlineStyle = .single
        link.link = URL(string: "traels://paste")
        var after = AttributedString(" — and see how likely it is to be made by AI.")
        after.foregroundColor = Palette.faint
        return lead + before + link + after
    }
}

/// One square in the type strip. Clicking it opens the matching way in (file picker or paste sheet).
private struct KindTile: View {
    var kind: MediaKind
    var targeted: Bool
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Color.clear
                .aspectRatio(1, contentMode: .fit)
                .overlay {
                    Image(systemName: kind.symbol)
                        .font(.system(size: 22, weight: .light))
                        .foregroundStyle(targeted ? Color.accentColor : Palette.ink.opacity(hovering ? 0.9 : 0.7))
                }
                .background(targeted ? Color.accentColor.opacity(0.12) : Palette.tile.opacity(hovering ? 1 : 0.8),
                            in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help(kind == .text ? "Paste text" : "Choose a file")
        .accessibilityLabel(kind == .text ? "Paste text" : "Choose \(kind.label) file")
    }
}

/// Warm neutrals from the "Words only" design, with dark-mode counterparts.
enum Palette {
    static let window = dynamic(light: 0xFBFAF8, dark: 0x1C1B1A)
    static let tile = dynamic(light: 0xEFECE7, dark: 0x2A2826)
    static let ink = dynamic(light: 0x1F1E1C, dark: 0xF2EFEA)
    static let faint = dynamic(light: 0xB3ADA5, dark: 0x6F6A64)
    static let mute = dynamic(light: 0x8A8580, dark: 0x8F8A84)
    static let tile2 = dynamic(light: 0xE6E2DC, dark: 0x34312E)
    static let real = dynamic(light: 0x3F9A62, dark: 0x58B67C)
    static let unsure = dynamic(light: 0xC08A24, dark: 0xD9A23E)
    static let ai = dynamic(light: 0xC8553D, dark: 0xE06B52)

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let hex = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
                           green: CGFloat((hex >> 8) & 0xFF) / 255,
                           blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
        })
    }
}

struct FirstRunCard: View {
    @EnvironmentObject var app: AppState
    @AppStorage("settingsTab") private var settingsTab = "models"

    var body: some View {
        VStack(spacing: 12) {
            Text("No detectors are switched on yet. Models run on your Mac and are downloaded once.")
                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
            if let run = app.recommendedRun {
                VStack(spacing: 6) {
                    ProgressView(value: overall(run)).frame(maxWidth: 300)
                    Text("\(run.current) (\(run.done + 1) of \(run.total))")
                        .font(.caption).foregroundStyle(.secondary)
                }
            } else {
                HStack(spacing: 10) {
                    Button(buttonTitle) { Task { await app.enableRecommended() } }
                        .buttonStyle(.borderedProminent)
                        .disabled(app.recommended.isEmpty)
                    SettingsLink { Text("Choose in Settings\u{2026}") }
                        .simultaneousGesture(TapGesture().onEnded { settingsTab = "models" })
                }
                .controlSize(.large)
            }
        }
        .padding(16)
        .frame(maxWidth: 460)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private var buttonTitle: String {
        let mb = app.recommendedMissingMB
        if mb <= 0 { return "Turn on recommended" }
        let size = mb >= 1000 ? String(format: "%.1f GB", mb / 1000) : "\(Int(mb)) MB"
        return "Turn on recommended (\u{2248} \(size))"
    }

    private func overall(_ run: (done: Int, total: Int, current: String)) -> Double {
        guard let m = app.recommended.first(where: { $0.name == run.current }) else { return 0 }
        let p = app.downloads[m.id] ?? 0
        return (Double(run.done) + max(0, min(p, 1))) / Double(max(run.total, 1))
    }
}

struct PreviewThumb: View {
    var preview: Preview
    var body: some View {
        Group {
            if let img = preview.image {
                Image(nsImage: img).resizable().aspectRatio(contentMode: .fit)
            } else {
                Image(systemName: preview.icon).font(.system(size: 36, weight: .light))
                    .foregroundStyle(.secondary)
                    .frame(width: 80, height: 80)
            }
        }
        .frame(maxWidth: 160, maxHeight: 120)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(.separator))
    }
}

struct AnalyzingView: View {
    @EnvironmentObject var app: AppState
    var preview: Preview
    var fraction: Double?
    var message: String

    var body: some View {
        VStack(spacing: 20) {
            PreviewThumb(preview: preview)
            Text(preview.title).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            VStack(spacing: 8) {
                if let f = fraction { ProgressView(value: min(max(f, 0), 1)) } else { ProgressView() .progressViewStyle(.linear) }
                Text(message).font(.callout).foregroundStyle(.secondary)
            }
            .frame(maxWidth: 300)
            Button("Cancel") { app.reset() }
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct NoDetectorCard: View {
    @EnvironmentObject var app: AppState
    var kind: MediaKind
    @AppStorage("settingsTab") private var settingsTab = "models"

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "switch.2").font(.system(size: 40, weight: .light)).foregroundStyle(.secondary)
            Text("No \(kind.label) detector enabled").font(.title3.weight(.semibold))
            Text("Turn on at least one \(kind.label) model in Settings. It's downloaded once and then runs entirely on your Mac.")
                .foregroundStyle(.secondary).multilineTextAlignment(.center)
            HStack {
                SettingsLink { Text("Open Settings") }
                    .controlSize(.large)
                    .simultaneousGesture(TapGesture().onEnded { settingsTab = "models" })
                Button("Back") { app.reset() }.controlSize(.large)
            }
        }
        .padding(32)
        .frame(maxWidth: 380)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear { settingsTab = "models" }
    }
}

struct PasteTextSheet: View {
    @EnvironmentObject var app: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Paste or type text").font(.headline)
            TextEditor(text: $text)
                .font(.body)
                .scrollContentBackground(.hidden)
                .padding(6)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
                .frame(minWidth: 420, minHeight: 220)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Check") { dismiss(); app.handle(text: text) }
                    .keyboardShortcut(.defaultAction)
                    .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
    }
}
