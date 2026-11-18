import AppKit
import AVFoundation
import UniformTypeIdentifiers

struct Preview {
    var title: String
    var image: NSImage?
    var kind: MediaKind
    var snippet: String? = nil
    var symbol: String? = nil
    var icon: String { symbol ?? kind.symbol }
}

struct Prepared {
    var kind: MediaKind
    var fileURL: URL?
    var text: String?
    var preview: Preview
    /// A web link still to be downloaded by the worker; `kind` is a placeholder until then.
    var remote: URL? = nil
    /// Where a downloaded link came from, and whether that site re-compresses uploads.
    var source: (host: String, platform: Bool)? = nil
}

enum InputLoader {
    static let dropTypes: [UTType] = [.fileURL, .url, .image, .plainText, .text]

    /// The string as a web link, if it is nothing but one http(s) URL.
    static func webURL(_ s: String) -> URL? {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, !t.contains(where: \.isWhitespace), let u = URL(string: t),
              let scheme = u.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = u.host, host.contains(".") else { return nil }
        return u
    }

    static func prepare(remote u: URL) -> Prepared {
        let host = u.host.map { $0.hasPrefix("www.") ? String($0.dropFirst(4)) : $0 } ?? "the web"
        return Prepared(kind: .image, fileURL: nil, text: nil,
                        preview: Preview(title: host, image: nil, kind: .image, symbol: "link"), remote: u)
    }

    static func prepare(fetched f: FetchedFile) -> Prepared? {
        guard let kind = MediaKind(rawValue: f.kind) else { return nil }
        let url = URL(fileURLWithPath: f.path)
        let image: NSImage? = switch kind {
        case .image: NSImage(contentsOf: url)
        case .video: f.thumb.flatMap { NSImage(contentsOfFile: $0) }
        default: nil
        }
        let preview = Preview(title: f.title ?? url.lastPathComponent, image: image, kind: kind)
        return Prepared(kind: kind, fileURL: url, text: nil, preview: preview,
                        source: f.source.map { ($0, f.platform ?? false) })
    }

    // MARK: Detection

    static func detectKind(url: URL) async -> MediaKind? {
        let type = (try? url.resourceValues(forKeys: [.contentTypeKey]))?.contentType
            ?? UTType(filenameExtension: url.pathExtension)
        guard let type else { return nil }
        if type.conforms(to: .image) { return .image }
        if type.conforms(to: .audio) { return .audio }
        if type.conforms(to: .movie) || type.conforms(to: .video) { return .video }
        if type.conforms(to: .audiovisualContent) {
            let asset = AVURLAsset(url: url)
            if let tracks = try? await asset.loadTracks(withMediaType: .video), !tracks.isEmpty { return .video }
            return .audio
        }
        if type.conforms(to: .plainText) || type.conforms(to: .text) { return .text }
        return nil
    }

    static func prepare(url: URL) async -> Prepared? {
        guard url.isFileURL, let kind = await detectKind(url: url) else { return nil }
        switch kind {
        case .text:
            var enc = String.Encoding.utf8
            guard let s = try? String(contentsOf: url, usedEncoding: &enc) else { return nil }
            return prepare(text: s, title: url.lastPathComponent)
        case .image:
            return Prepared(kind: .image, fileURL: url, text: nil,
                            preview: Preview(title: url.lastPathComponent, image: NSImage(contentsOf: url), kind: .image))
        case .video:
            let frame = await firstFrame(url: url)
            return Prepared(kind: .video, fileURL: url, text: nil,
                            preview: Preview(title: url.lastPathComponent,
                                             image: frame ?? NSWorkspace.shared.icon(forFile: url.path), kind: .video))
        default:
            return Prepared(kind: kind, fileURL: url, text: nil,
                            preview: Preview(title: url.lastPathComponent,
                                             image: NSWorkspace.shared.icon(forFile: url.path), kind: kind))
        }
    }

    static func firstFrame(url: URL) async -> NSImage? {
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        gen.appliesPreferredTrackTransform = true
        gen.maximumSize = CGSize(width: 320, height: 320)
        guard let cg = try? await gen.image(at: .zero).image else { return nil }
        return NSImage(cgImage: cg, size: NSSize(width: cg.width, height: cg.height))
    }

    static func prepare(text: String, title: String = "Pasted text") -> Prepared? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return nil }
        if let u = webURL(t) { return prepare(remote: u) }
        return Prepared(kind: .text, fileURL: nil, text: text, preview: Preview(title: title, image: nil, kind: .text, snippet: t))
    }

    static func prepare(image: NSImage) -> Prepared? {
        guard let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:]) else { return nil }
        return prepare(imageData: png, ext: "png", preview: image)
    }

    static func prepare(imageData data: Data, ext: String = "png", preview: NSImage? = nil) -> Prepared? {
        guard let img = preview ?? NSImage(data: data),
              let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:]) else { return nil }
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("traels", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("\(UUID().uuidString).png")
        guard (try? png.write(to: url)) != nil else { return nil }
        return Prepared(kind: .image, fileURL: url, text: nil,
                        preview: Preview(title: "Pasted image", image: img, kind: .image))
    }

    // MARK: Sources

    static func prepare(providers: [NSItemProvider]) async -> Prepared? {
        for p in providers where p.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
            if let url = await loadURL(p), url.isFileURL, let r = await prepare(url: url) { return r }
        }
        // SwiftUI often hands Finder drops over as the file's content type (e.g. public.jpeg) with no
        // file-url. Ask for the file itself so we keep its name and metadata instead of re-encoding pixels.
        for p in providers {
            for id in p.registeredTypeIdentifiers {
                guard let t = UTType(id), [.image, .movie, .audio, .audiovisualContent, .plainText].contains(where: t.conforms),
                      let url = await loadFile(p, type: id), let r = await prepare(url: url) else { continue }
                return r
            }
        }
        // A link dragged out of a browser: download the original rather than re-encoding pixels.
        for p in providers where p.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
            if let u = await loadURL(p), let web = webURL(u.absoluteString) { return prepare(remote: web) }
        }
        for p in providers where p.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
            if let d = await loadData(p, type: .image), let r = prepare(imageData: d) { return r }
        }
        for p in providers where p.canLoadObject(ofClass: NSString.self) {
            if let s = await loadString(p), let r = prepare(text: s) { return r }
        }
        return nil
    }

    @MainActor
    static func prepare(pasteboard pb: NSPasteboard) async -> Prepared? {
        if let urls = pb.readObjects(forClasses: [NSURL.self],
                                     options: [.urlReadingFileURLsOnly: true]) as? [URL] {
            for u in urls { if let r = await prepare(url: u) { return r } }
        }
        if let urls = pb.readObjects(forClasses: [NSURL.self]) as? [URL],
           let web = urls.lazy.compactMap({ webURL($0.absoluteString) }).first { return prepare(remote: web) }
        if let s = pb.string(forType: .string), let web = webURL(s) { return prepare(remote: web) }
        if let imgs = pb.readObjects(forClasses: [NSImage.self]) as? [NSImage], let img = imgs.first,
           let r = prepare(image: img) { return r }
        if let s = pb.string(forType: .string), let r = prepare(text: s) { return r }
        return nil
    }

    /// Reads the raw public.file-url data; loadObject(ofClass: URL.self) fails for many drag sources,
    /// which would drop us into the image-data path and lose the original file (and its metadata).
    private static func loadURL(_ p: NSItemProvider) async -> URL? {
        if let d = await loadData(p, type: .fileURL),
           let url = URL(dataRepresentation: d, relativeTo: nil) { return url }
        return await withCheckedContinuation { c in
            _ = p.loadObject(ofClass: URL.self) { url, _ in c.resume(returning: url) }
        }
    }
    /// The original file when the source allows in-place access, else a copy under a temp dir with its real name.
    private static func loadFile(_ p: NSItemProvider, type: String) async -> URL? {
        await withCheckedContinuation { c in
            _ = p.loadInPlaceFileRepresentation(forTypeIdentifier: type) { url, inPlace, _ in
                guard let url else { return c.resume(returning: nil) }
                if inPlace { return c.resume(returning: url) }
                let dir = FileManager.default.temporaryDirectory
                    .appendingPathComponent("traels/\(UUID().uuidString)", isDirectory: true)
                let dest = dir.appendingPathComponent(url.lastPathComponent)
                do {
                    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                    try FileManager.default.copyItem(at: url, to: dest)
                    c.resume(returning: dest)
                } catch { c.resume(returning: nil) }
            }
        }
    }
    private static func loadData(_ p: NSItemProvider, type: UTType) async -> Data? {
        await withCheckedContinuation { c in
            p.loadDataRepresentation(forTypeIdentifier: type.identifier) { d, _ in c.resume(returning: d) }
        }
    }
    private static func loadString(_ p: NSItemProvider) async -> String? {
        await withCheckedContinuation { c in
            _ = p.loadObject(ofClass: NSString.self) { obj, _ in c.resume(returning: obj as? String) }
        }
    }
}
