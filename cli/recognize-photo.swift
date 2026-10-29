// Local OCR only. Vision coordinates have a bottom-left origin.
import Foundation
import Vision
import ImageIO
let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
let supported = (try? request.supportedRecognitionLanguages()) ?? ["en-US"]
request.recognitionLanguages = ["en-US", "ru-RU"].filter { supported.contains($0) }
request.customWords = ["ЦИАН", "CIAN", "cian.ru", "циан"]
try VNImageRequestHandler(url: url).perform([request])
let rows: [[String: Any]] = (request.results ?? []).compactMap { observation in
    guard let text = observation.topCandidates(1).first else { return nil }
    let r = observation.boundingBox
    return ["text": text.string, "confidence": text.confidence,
            "x": r.minX, "y": 1 - r.maxY, "width": r.width, "height": r.height]
}
let data = try JSONSerialization.data(withJSONObject: rows)
print(String(decoding: data, as: UTF8.self))
