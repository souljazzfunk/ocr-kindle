// Usage: livetext <image> : VisionKit ImageAnalyzer (Live Text), prints the transcript.
// Reads vertical Japanese, which VNRecognizeTextRequest returns no lines for (英語教育幻想, macOS 14.4, 2026-10-12).
import AppKit
import Foundation
import VisionKit

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path) else {
  FileHandle.standardError.write("cannot read \(path)\n".data(using: .utf8)!)
  exit(2)
}
var configuration = ImageAnalyzer.Configuration([.text])
configuration.locales = ["ja-JP", "en-US"]
do {
  let analysis = try await ImageAnalyzer().analyze(image, orientation: .up, configuration: configuration)
  print(analysis.transcript)
} catch {
  FileHandle.standardError.write("\(error)\n".data(using: .utf8)!)
  exit(1)
}
