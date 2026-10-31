// Native scene classifications are suggestions, not object boxes or counts.
import Foundation
import Vision
import ImageIO
import CoreGraphics
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    throw NSError(domain: "RedcnPhoto", code: 1)
}
var regions: [[Double]] = [[0,0,1,1]]
for y in [0.0, 0.25, 0.5] {
    for x in [0.0, 0.25, 0.5] { regions.append([x,y,0.5,0.5]) }
}
var rows: [[String: Any]] = []
for region in regions {
    let rect = CGRect(x: region[0] * Double(image.width), y: region[1] * Double(image.height), width: region[2] * Double(image.width), height: region[3] * Double(image.height))
    guard let tile = image.cropping(to: rect) else { continue }
    let request = VNClassifyImageRequest()
    try VNImageRequestHandler(cgImage: tile).perform([request])
    for result in (request.results ?? []).prefix(35) where result.confidence >= 0.12 {
        rows.append(["label": result.identifier, "confidence": result.confidence, "region": region])
    }
}
let data = try JSONSerialization.data(withJSONObject: rows)
print(String(decoding: data, as: UTF8.self))
