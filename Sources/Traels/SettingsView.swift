import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var app: AppState
    @AppStorage("settingsTab") private var tab = "models"

    var body: some View {
        TabView(selection: $tab) {
            ModelsTab().tabItem { Label("Models", systemImage: "cpu") }.tag("models")
            GeneralTab(worker: app.worker).tabItem { Label("General", systemImage: "gearshape") }.tag("general")
        }
        .frame(width: 560, height: 520)
        .onAppear { app.startWorker() }
    }
}

private let kindSections: [(kind: String, title: String)] = [
    ("image", "Images"), ("video", "Video"), ("audio", "Voice & Audio"), ("music", "Music"), ("text", "Text"),
]

struct ModelsTab: View {
    @EnvironmentObject var app: AppState

    var body: some View {
        if app.models.isEmpty {
            VStack(spacing: 10) {
                ProgressView()
                Text(app.worker.status == .ready ? "No models available." : "Waiting for the local engine…")
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            Form {
                ForEach(kindSections, id: \.kind) { sec in
                    let items = app.models.filter { $0.kind == sec.kind }
                    if !items.isEmpty || sec.kind == "video" {
                        Section(sec.title) {
                            if sec.kind == "video" {
                                Text("Videos are checked frame by frame with your image detectors, and their soundtrack with your voice and music detectors.")
                                    .font(.callout).foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            ForEach(items) { ModelRow(model: $0) }
                        }
                    }
                }
            }
            .formStyle(.grouped)
        }
    }
}

struct ModelRow: View {
    @EnvironmentObject var app: AppState
    var model: ModelInfo

    var body: some View {
        let progress = app.downloads[model.id]
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(model.name).font(.body.weight(.medium))
                        if model.recommended == true {
                            Text("Recommended").font(.caption2.weight(.medium))
                                .padding(.horizontal, 6).padding(.vertical, 1)
                                .background(Color.accentColor.opacity(0.15), in: Capsule())
                                .foregroundStyle(Color.accentColor)
                        }
                    }
                    Text(meta).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if progress != nil {
                    ProgressView().controlSize(.small)
                } else {
                    Toggle("", isOn: Binding(get: { app.isEnabled(model) },
                                              set: { app.setEnabled(model, $0) }))
                        .labelsHidden().toggleStyle(.switch)
                }
            }
            if let d = model.description {
                Text(d).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if let p = progress {
                VStack(alignment: .leading, spacing: 2) {
                    if p >= 0 { ProgressView(value: min(p, 1)) } else { ProgressView().progressViewStyle(.linear) }
                    Text(app.downloadMessages[model.id] ?? "Downloading…").font(.caption).foregroundStyle(.secondary)
                }
            }
            if let e = app.modelErrors[model.id] {
                Label(e, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.red).textSelection(.enabled)
            }
            if model.installed && !app.isEnabled(model) && progress == nil {
                Button("Remove files", role: .destructive) { Task { await app.removeFiles(model) } }
                    .buttonStyle(.link).font(.caption)
            }
        }
        .padding(.vertical, 3)
    }

    private var meta: String {
        var parts: [String] = []
        if let l = model.license { parts.append(l) }
        if let s = model.sizeMB { parts.append(s >= 1000 ? String(format: "%.1f GB", s / 1000) : "\(Int(s)) MB") }
        parts.append(model.installed ? "Downloaded" : "Not downloaded")
        return parts.joined(separator: " · ")
    }
}

struct GeneralTab: View {
    @ObservedObject var worker: WorkerClient
    @EnvironmentObject var app: AppState
    @State private var showLog = false
    @AppStorage("videoSites") private var videoSites = false

    var body: some View {
        Form {
            Section("Local engine") {
                LabeledContent("Status") {
                    HStack(spacing: 6) {
                        Circle().fill(statusColor).frame(width: 8, height: 8)
                        Text(statusText)
                    }
                }
                LabeledContent("uv") { Text(worker.uvPath ?? "Not found").textSelection(.enabled).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle) }
                LabeledContent("Backend") { Text(worker.backendPath ?? "Not found").textSelection(.enabled).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle) }
                HStack {
                    Button("Show Log…") { showLog = true }
                    Button("Restart Worker") { app.restartWorker() }
                }
            }
            Section("Links") {
                Toggle(isOn: $videoSites) {
                    Text("Video sites")
                    Text("Check links from YouTube, TikTok, X, Instagram and other video sites. Uses yt-dlp, which is set up (about 40 MB) the first time. Downloading may go against a site's terms, and these sites re-compress uploads, which makes fakes harder to spot.")
                }
                Text("Links to images, videos and web pages from other sites always work.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Section("Privacy") {
                Text("Analysis runs entirely on this Mac. The network is only used to download a model once when you turn it on, and to download a link you paste or drop. Nothing you check is uploaded.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .sheet(isPresented: $showLog) { LogSheet(worker: worker) }
    }

    private var statusText: String {
        switch worker.status {
        case .idle: return "Not started"
        case .starting: return "Starting…"
        case .ready: return "Running"
        case .uvMissing: return "uv not installed"
        case .backendMissing: return "Backend missing"
        case .stopped(let m): return m
        }
    }
    private var statusColor: Color {
        switch worker.status {
        case .ready: return .green
        case .starting, .idle: return .orange
        default: return .red
        }
    }
}

struct LogSheet: View {
    @ObservedObject var worker: WorkerClient
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Engine log").font(.headline)
            ScrollView {
                Text(worker.logLines.isEmpty ? "No output yet." : worker.logLines.joined(separator: "\n"))
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            }
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
            HStack {
                Button("Copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(worker.logLines.joined(separator: "\n"), forType: .string)
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 520, height: 360)
    }
}
