import Foundation

enum MediaKind: String, CaseIterable {
    case image, video, audio, text

    var label: String { rawValue }
    var symbol: String {
        switch self {
        case .image: return "photo"
        case .video: return "film"
        case .audio: return "waveform"
        case .text: return "text.alignleft"
        }
    }
    /// Model kinds that can analyse this media kind.
    var modelKinds: [String] {
        switch self {
        case .image: return ["image"]
        case .text: return ["text"]
        case .audio: return ["audio", "music"]
        case .video: return ["video", "image", "audio"]
        }
    }
}

struct ModelInfo: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var kind: String
    var repo: String?
    var license: String?
    var sizeMB: Double?
    var installed: Bool
    var description: String?
    var recommended: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name, kind, repo, license, installed, description, recommended
        case sizeMB = "size_mb"
    }
}

struct ModelScore: Codable, Identifiable {
    var id: String
    var name: String
    var score: Double
    var verdict: String?
    var detail: String?
    /// The detector couldn't judge this input (e.g. a voice model hearing music underneath).
    var inconclusive: Bool?
}

struct Fact: Codable, Identifiable {
    var label: String
    var value: String
    var id: String { label }
}

struct AnalysisResult: Codable {
    var kind: String?
    var score: Double
    var verdict: String?
    var headline: String
    var summary: String
    var models: [ModelScore]?
    var facts: [Fact]?
    var seconds: Double?
}

/// A link downloaded by the worker's `fetch` command.
struct FetchedFile: Decodable {
    var path: String
    var kind: String
    var title: String?
    var source: String?
    var platform: Bool?
    var thumb: String?
}

struct WorkerEvent: Decodable {
    var id: String?
    var type: String
    var version: String?
    var fraction: Double?
    var message: String?
    var models: [ModelInfo]?
    var result: AnalysisResult?
    var file: FetchedFile?
}
