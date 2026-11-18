import Foundation
import SwiftUI

enum WorkerStatus: Equatable {
    case idle, starting, ready, uvMissing, backendMissing
    case stopped(String)
}

struct WorkerError: LocalizedError {
    var message: String
    var errorDescription: String? { message }
}

@MainActor
final class WorkerClient: ObservableObject {
    @Published private(set) var status: WorkerStatus = .idle
    @Published private(set) var logLines: [String] = []
    @Published private(set) var uvPath: String?
    @Published private(set) var backendPath: String?

    var latestLog: String { logLines.last(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }) ?? "" }
    var onReady: (() -> Void)?

    private struct Pending {
        var progress: ((Double?, String?) -> Void)?
        var cont: CheckedContinuation<WorkerEvent, Error>
    }

    private var process: Process?
    private var stdinHandle: FileHandle?
    private var stdoutBuf = Data()
    private var stderrBuf = Data()
    private var pending: [String: Pending] = [:]
    private var readyWaiters: [CheckedContinuation<Void, Error>] = []
    private var nextID = 1
    private var generation = 0

    // MARK: Locating things

    nonisolated static func findUV() -> URL? {
        let fm = FileManager.default
        let home = NSHomeDirectory()
        for p in ["/opt/homebrew/bin/uv", "/usr/local/bin/uv", "\(home)/.local/bin/uv", "\(home)/.cargo/bin/uv"]
        where fm.isExecutableFile(atPath: p) {
            return URL(fileURLWithPath: p)
        }
        let sh = Process()
        sh.executableURL = URL(fileURLWithPath: "/bin/zsh")
        sh.arguments = ["-lc", "command -v uv"]
        let out = Pipe()
        sh.standardOutput = out
        sh.standardError = FileHandle.nullDevice
        do {
            try sh.run()
            let data = out.fileHandleForReading.readDataToEndOfFile()
            sh.waitUntilExit()
            let s = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            if let line = s.split(separator: "\n").last, fm.isExecutableFile(atPath: String(line)) {
                return URL(fileURLWithPath: String(line))
            }
        } catch {}
        return nil
    }

    nonisolated static func backendDir() -> URL? {
        let fm = FileManager.default
        func ok(_ u: URL) -> Bool { fm.fileExists(atPath: u.appendingPathComponent("worker.py").path) }
        if let r = Bundle.main.resourceURL?.appendingPathComponent("backend"), ok(r) { return r }
        var starts: [URL] = []
        if let exe = Bundle.main.executableURL { starts.append(exe.deletingLastPathComponent()) }
        starts.append(URL(fileURLWithPath: #filePath).deletingLastPathComponent())
        starts.append(URL(fileURLWithPath: fm.currentDirectoryPath))
        for start in starts {
            var dir = start
            for _ in 0..<10 {
                let cand = dir.appendingPathComponent("backend")
                if ok(cand) { return cand }
                let parent = dir.deletingLastPathComponent()
                if parent == dir { break }
                dir = parent
            }
        }
        return nil
    }

    // MARK: Lifecycle

    func start() {
        stop()
        generation += 1
        let gen = generation
        logLines = []
        guard let uv = Self.findUV() else { status = .uvMissing; return }
        uvPath = uv.path
        guard let backend = Self.backendDir() else { status = .backendMissing; return }
        backendPath = backend.path
        status = .starting

        let p = Process()
        p.executableURL = uv
        p.arguments = ["run", "--script", backend.appendingPathComponent("worker.py").path]
        p.currentDirectoryURL = backend
        var env = ProcessInfo.processInfo.environment
        env["HF_HUB_OFFLINE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"
        env["TRAELS_UV"] = uv.path  // the worker runs backend/video_sites.py with it
        let extra = [uv.deletingLastPathComponent().path, "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
        env["PATH"] = (extra + [(env["PATH"] ?? "")]).joined(separator: ":")
        p.environment = env

        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
        p.standardInput = inPipe
        p.standardOutput = outPipe
        p.standardError = errPipe

        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil; return }
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.ingest(d, stdout: true, gen: gen) } }
        }
        errPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil; return }
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.ingest(d, stdout: false, gen: gen) } }
        }
        p.terminationHandler = { [weak self] proc in
            let code = proc.terminationStatus
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.terminated(code: code, gen: gen) } }
        }
        do {
            try p.run()
            process = p
            stdinHandle = inPipe.fileHandleForWriting
        } catch {
            status = .stopped("Could not start the worker: \(error.localizedDescription)")
        }
    }

    func stop() {
        generation += 1 // invalidate callbacks of the old process
        if let p = process, p.isRunning { p.terminate() }
        process = nil
        try? stdinHandle?.close()
        stdinHandle = nil
        stdoutBuf = Data(); stderrBuf = Data()
        failAll(WorkerError(message: "The local engine was stopped."))
        if status == .ready || status == .starting { status = .idle }
    }

    func restart() { start() }

    private func terminated(code: Int32, gen: Int) {
        guard gen == generation else { return }
        process = nil
        stdinHandle = nil
        let msg = "The local engine stopped unexpectedly (exit code \(code))."
        status = .stopped(msg)
        failAll(WorkerError(message: msg))
    }

    private func failAll(_ error: Error) {
        let p = pending
        pending = [:]
        for (_, v) in p { v.cont.resume(throwing: error) }
        let w = readyWaiters
        readyWaiters = []
        for c in w { c.resume(throwing: error) }
    }

    // MARK: Reading

    private func ingest(_ data: Data, stdout: Bool, gen: Int) {
        guard gen == generation else { return }
        if stdout { stdoutBuf.append(data) } else { stderrBuf.append(data) }
        while true {
            let buf = stdout ? stdoutBuf : stderrBuf
            guard let nl = buf.firstIndex(of: 0x0A) else { break }
            let lineData = buf[buf.startIndex..<nl]
            let rest = Data(buf[buf.index(after: nl)...])
            if stdout { stdoutBuf = rest } else { stderrBuf = rest }
            let line = String(decoding: lineData, as: UTF8.self)
            if stdout { handleStdout(line) } else { appendLog(line) }
        }
    }

    private func appendLog(_ line: String) {
        let t = line.trimmingCharacters(in: .newlines)
        guard !t.isEmpty else { return }
        logLines.append(t)
        if logLines.count > 200 { logLines.removeFirst(logLines.count - 200) }
    }

    private func handleStdout(_ line: String) {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        guard let ev = try? JSONDecoder().decode(WorkerEvent.self, from: Data(trimmed.utf8)) else {
            appendLog("[stdout] \(trimmed)")
            return
        }
        switch ev.type {
        case "ready":
            status = .ready
            let w = readyWaiters
            readyWaiters = []
            for c in w { c.resume() }
            onReady?()
        case "progress":
            if let id = ev.id { pending[id]?.progress?(ev.fraction, ev.message) }
        case "error":
            if let id = ev.id, let p = pending.removeValue(forKey: id) {
                p.cont.resume(throwing: WorkerError(message: ev.message ?? "Unknown error"))
            } else {
                appendLog("error: \(ev.message ?? "")")
            }
        default: // models, done, result
            if let id = ev.id, let p = pending.removeValue(forKey: id) { p.cont.resume(returning: ev) }
        }
    }

    // MARK: Requests

    private func waitUntilReady() async throws {
        switch status {
        case .ready: return
        case .uvMissing: throw WorkerError(message: "uv is not installed.")
        case .backendMissing: throw WorkerError(message: "The analysis backend could not be found.")
        case .stopped(let m): throw WorkerError(message: m)
        case .idle: start(); if status != .starting { throw WorkerError(message: "The local engine is not available.") }
        case .starting: break
        }
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
            readyWaiters.append(c)
        }
    }

    func request(_ payload: [String: Any], progress: ((Double?, String?) -> Void)? = nil) async throws -> WorkerEvent {
        try await waitUntilReady()
        let id = String(nextID)
        nextID += 1
        var body = payload
        body["id"] = id
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (c: CheckedContinuation<WorkerEvent, Error>) in
                pending[id] = Pending(progress: progress, cont: c)
                write(body)
            }
        } onCancel: {
            Task { @MainActor in self.cancelRequest(id) }
        }
    }

    private func cancelRequest(_ id: String) {
        guard let p = pending.removeValue(forKey: id) else { return }
        let cid = String(nextID)
        nextID += 1
        write(["id": cid, "cmd": "cancel", "target": id])
        p.cont.resume(throwing: CancellationError())
    }

    private func write(_ obj: [String: Any]) {
        guard let h = stdinHandle,
              var data = try? JSONSerialization.data(withJSONObject: obj) else { return }
        data.append(0x0A)
        do { try h.write(contentsOf: data) } catch { appendLog("write failed: \(error.localizedDescription)") }
    }
}
