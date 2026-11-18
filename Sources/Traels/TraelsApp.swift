import SwiftUI
import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        PasteMonitor.install()
    }
    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { AppState.shared.worker.stop() }
    }
    /// Files dropped on the Dock icon or opened with "Open With → Traels".
    func application(_ application: NSApplication, open urls: [URL]) {
        guard let url = urls.first else { return }
        MainActor.assumeIsolated { AppState.shared.open(url: url) }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct TraelsApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var app = AppState.shared

    var body: some Scene {
        WindowGroup("Traels", id: "main") {
            RootView()
                .environmentObject(app)
                .frame(minWidth: 480, idealWidth: 520, minHeight: 600, idealHeight: 640)
                .onAppear { app.startWorker() }
        }
        .defaultSize(width: 520, height: 640)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(after: .newItem) {
                Button("Choose File…") { FilePicker.choose { app.handle(url: $0) } }
                    .keyboardShortcut("o")
                Button("Paste Text…") { app.showPasteSheet = true }
                    .keyboardShortcut("t", modifiers: [.command, .shift])
            }
        }

        Settings {
            SettingsView().environmentObject(app)
        }
    }
}

enum FilePicker {
    @MainActor
    static func choose(_ done: @escaping (URL) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = "Choose an image, video, audio or text file to check"
        if panel.runModal() == .OK, let url = panel.url { done(url) }
    }
}

/// Handles ⌘V anywhere in the main window unless a text field is being edited.
enum PasteMonitor {
    nonisolated(unsafe) private static var token: Any?
    static func install() {
        guard token == nil else { return }
        token = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            guard event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command,
                  event.charactersIgnoringModifiers == "v",
                  let w = event.window ?? NSApp.keyWindow,
                  w.identifier?.rawValue == "traels.main",
                  w.attachedSheet == nil,
                  !(w.firstResponder is NSText) else { return event }
            MainActor.assumeIsolated { AppState.shared.handlePasteboard() }
            return nil
        }
    }
}

struct WindowTagger: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let v = NSView()
        DispatchQueue.main.async { v.window?.identifier = NSUserInterfaceItemIdentifier("traels.main") }
        return v
    }
    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async { nsView.window?.identifier = NSUserInterfaceItemIdentifier("traels.main") }
    }
}
