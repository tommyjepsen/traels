// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "Traels",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "Traels", targets: ["Traels"])],
    targets: [
        .executableTarget(name: "Traels", path: "Sources/Traels")
    ],
    swiftLanguageModes: [.v5]
)
