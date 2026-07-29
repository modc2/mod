// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "JCodeKit",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "JCodeKit", targets: ["JCodeKit"])
    ],
    targets: [
        .target(
            name: "JCodeKit",
            swiftSettings: [.enableUpcomingFeature("StrictConcurrency")]
        ),
        .testTarget(
            name: "JCodeKitTests",
            dependencies: ["JCodeKit"]
        ),
    ]
)
