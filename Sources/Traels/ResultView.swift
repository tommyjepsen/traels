import SwiftUI

enum Band {
    case real, uncertain, ai
    init(score: Double) { self = score < 35 ? .real : (score <= 65 ? .uncertain : .ai) }
    var color: Color {
        switch self {
        case .real: return Palette.real
        case .uncertain: return Palette.unsure
        case .ai: return Palette.ai
        }
    }
}

struct CountingText: View, Animatable {
    var value: Double
    var animatableData: Double { get { value } set { value = newValue } }
    var body: some View { Text("\(Int(value.rounded()))") }
}

/// Half circle from "Likely real" (left) to "Likely AI" (right). `value` is animated 0...100,
/// so the fill, the dot and its colour all move together while the number counts up.
struct ArcGauge: View, Animatable {
    var value: Double
    var animatableData: Double { get { value } set { value = newValue } }
    private let line: CGFloat = 14

    var body: some View {
        let v = min(max(value, 0), 100)
        let color = Band(score: v).color
        GeometryReader { geo in
            let r = min(geo.size.width / 2, geo.size.height) - line / 2 - 4
            let center = CGPoint(x: geo.size.width / 2, y: geo.size.height - line / 2 - 4)
            let angle = Double.pi * (1 - v / 100)
            let dot = CGPoint(x: center.x + r * cos(angle), y: center.y - r * sin(angle))
            ZStack {
                HalfCircle(center: center, radius: r)
                    .stroke(Palette.tile, style: StrokeStyle(lineWidth: line, lineCap: .round))
                HalfCircle(center: center, radius: r)
                    .trim(from: 0, to: v / 100)
                    .stroke(color, style: StrokeStyle(lineWidth: line, lineCap: .round))
                Circle()
                    .fill(color)
                    .overlay(Circle().strokeBorder(Palette.window, lineWidth: 4))
                    .frame(width: 26, height: 26)
                    .position(dot)
            }
        }
        .accessibilityElement()
        .accessibilityLabel("Score")
        .accessibilityValue("\(Int(v.rounded())) out of 100")
    }
}

private struct HalfCircle: Shape {
    var center: CGPoint
    var radius: CGFloat
    func path(in rect: CGRect) -> Path {
        Path { p in
            p.addArc(center: center, radius: radius, startAngle: .degrees(180), endAngle: .degrees(0), clockwise: false)
        }
    }
}

struct MiniBar: View {
    var score: Double
    var body: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.tile2)
                Capsule().fill(Band(score: score).color)
                    .frame(width: max(6, g.size.width * min(max(score, 0), 100) / 100))
            }
        }
        .frame(height: 6)
    }
}

struct CheckedRow: View {
    var preview: Preview

    var body: some View {
        HStack(spacing: 10) {
            Group {
                if let img = preview.image, preview.kind == .image || preview.kind == .video {
                    Image(nsImage: img).resizable().aspectRatio(contentMode: .fill)
                } else {
                    Image(systemName: preview.icon).font(.system(size: 13)).foregroundStyle(Palette.mute)
                }
            }
            .frame(width: 44, height: 30)
            .background(Palette.tile2)
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
            Text(preview.kind == .text ? (preview.snippet.map { "\u{201C}\($0)\u{201D}" } ?? preview.title) : preview.title)
                .lineLimit(1).truncationMode(preview.kind == .text ? .tail : .middle)
        }
        .font(.caption)
        .foregroundStyle(Palette.mute)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Checked: \(preview.title)")
    }
}

struct ResultView: View {
    @EnvironmentObject var app: AppState
    var result: AnalysisResult
    var preview: Preview
    @State private var shown: Double = 0
    @State private var rowsShown = false

    var band: Band { Band(score: result.score) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    CheckedRow(preview: preview)

                    ZStack(alignment: .bottom) {
                        ArcGauge(value: shown).frame(width: 300, height: 160)
                        VStack(spacing: 2) {
                            CountingText(value: shown)
                                .font(.system(size: 58, weight: .bold))
                                .tracking(-2.5)
                                .monospacedDigit()
                                .foregroundStyle(Band(score: shown).color)
                            Text(result.headline)
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(Palette.ink)
                        }
                        .padding(.bottom, 4)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 14)

                    HStack { Text("Likely real"); Spacer(); Text("Likely AI") }
                        .font(.caption2).foregroundStyle(Palette.mute)
                        .frame(width: 300)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 4)

                    Text(result.summary)
                        .font(.callout).foregroundStyle(Palette.mute)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 14)

                    if !groups.isEmpty {
                        detectors.padding(.top, 18)
                    }

                    if let line = factsLine {
                        Text(line)
                            .font(.caption).foregroundStyle(Palette.mute)
                            .textSelection(.enabled)
                            .padding(.top, 10)
                    }
                }
                .padding(.horizontal, 36)
                .padding(.top, 36)
            }

            HStack(spacing: 16) {
                Text("Detectors can be wrong. Treat this as a hint, not proof.")
                Spacer()
                Button { app.reset() } label: {
                    Label("Check another", systemImage: "arrow.counterclockwise")
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(Palette.ink)
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .background(Palette.tile, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.defaultAction)
            }
            .font(.caption).foregroundStyle(Palette.mute)
            .padding(.horizontal, 36).padding(.top, 14).padding(.bottom, 28)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Palette.window)
        .onAppear {
            shown = 0
            rowsShown = false
            withAnimation(.easeOut(duration: 1.2)) { shown = result.score }
            withAnimation(.easeOut(duration: 0.9).delay(0.35)) { rowsShown = true }
        }
    }

    private var detectors: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(groups.enumerated()), id: \.offset) { i, g in
                Text(g.title)
                    .font(.caption2).foregroundStyle(Palette.mute)
                    .padding(.top, i == 0 ? 0 : 12).padding(.bottom, 6)
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                    ForEach(g.models) { m in
                        GridRow {
                            Text(m.name).font(.callout).foregroundStyle(Palette.ink).lineLimit(1)
                                .help(m.detail ?? "")
                                .frame(maxWidth: .infinity, alignment: .leading)
                            if m.inconclusive == true {
                                Text("Can't tell · background sound")
                                    .font(.caption).foregroundStyle(Palette.mute)
                                    .help(m.detail ?? "")
                                    .gridCellColumns(2)
                                    .gridCellAnchor(.trailing)
                            } else {
                                MiniBar(score: rowsShown ? m.score : 0).frame(width: 90)
                                CountingText(value: rowsShown ? m.score : 0)
                                    .font(.callout.weight(.semibold)).monospacedDigit()
                                    .foregroundStyle(Palette.ink)
                                    .frame(width: 28, alignment: .trailing)
                            }
                        }
                    }
                }
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .background(Palette.tile, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    /// Detectors grouped by what they looked at or listened to, in the same groups the score is combined over.
    private var groups: [(title: String, models: [ModelScore])] {
        let order = ["Picture", "Voice", "Music", "Text"]
        let byGroup = Dictionary(grouping: result.models ?? []) { m -> String in
            switch app.models.first(where: { $0.id == m.id })?.kind {
            case "image", "video": return "Picture"
            case "audio": return "Voice"
            case "music": return "Music"
            case "text": return "Text"
            default: return "Other"
            }
        }
        return (order + ["Other"]).compactMap { k in byGroup[k].map { (k, $0) } }
    }

    private var factsLine: String? {
        var parts = (result.facts ?? []).map { "\($0.label): \($0.value)" }
        if let s = result.seconds { parts.append(String(format: "Checked on this Mac in %.1f s", s)) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}
