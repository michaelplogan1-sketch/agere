import Foundation
import PDFKit
import Vision
import AppKit

// Local PDF extraction. Source contents never become commands or network requests.
let args=CommandLine.arguments
if args.count != 2 { fputs("usage: agere-ocr input.pdf\n",stderr);exit(2) }
guard let document=PDFDocument(url:URL(fileURLWithPath:args[1])) else {fputs("Cannot open PDF\n",stderr);exit(2)}
var pages=[[String:Any]]()
for index in 0..<document.pageCount {
    guard let page=document.page(at:index) else {continue}
    let native=page.string ?? ""
    if native.trimmingCharacters(in:.whitespacesAndNewlines).count >= 80 {
        pages.append(["page":index+1,"method":"pdfkit_native","text":native,"regions":[],"confidence":NSNull()])
        continue
    }
    let image=page.thumbnail(of:NSSize(width:1800,height:2400),for:.mediaBox)
    var rect=NSRect(origin:.zero,size:image.size)
    guard let cg=image.cgImage(forProposedRect:&rect,context:nil,hints:nil) else {throw NSError(domain:"render",code:1)}
    let request=VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US"]
    try VNImageRequestHandler(cgImage:cg,options:[:]).perform([request])
    var regions=[[String:Any]]();var parts=[String]()
    for observation in request.results ?? [] {
        guard let c=observation.topCandidates(1).first else {continue}
        let box=observation.boundingBox
        regions.append(["text":c.string,"confidence":c.confidence,"box":[box.origin.x,box.origin.y,box.width,box.height]])
        parts.append(c.string)
    }
    pages.append(["page":index+1,"method":"apple_vision_accurate","text":parts.joined(separator:"\n"),"regions":regions,
                  "native_text":native,"coordinate_system":"normalized, bottom-left origin","confidence":NSNull()])
}
let result:[String:Any] = ["schema":"agere-ocr/1","engine":"macOS PDFKit + Vision","pages":pages,
                           "clinical_correctness_verified":false,"source_path":args[1]]
let data=try JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys])
FileHandle.standardOutput.write(data)
