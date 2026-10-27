import AppKit
import AVFoundation
import Speech
import Accelerate
import CoreAudio
import AudioToolbox

let pollSeconds = 0.25
let leaseSeconds = 10.0
let bufferFrames: AVAudioFrameCount = 1024
let spectrumFrames = 512
let spectrumLog2 = vDSP_Length(9)
let spectrumBands = 24
let spectrumMinimumHz = 80.0
let spectrumMaximumHz = 8000.0
let spectrumFloorDB = -70.0
let spectrumCeilingDB = -12.0
let meterPublishSeconds = 0.1
let meterRMSGain = 4.0
let deviceUIDArgument = "--device"
let audioDevicesArgument = "--devices"
let voiceLockProtocolArgument = "--voice-lock-protocol"
let voiceLockProtocol = "recording-owner-v1"
func deviceString(_ device: AudioDeviceID, _ selector: AudioObjectPropertySelector) -> String? {
    var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    let value = UnsafeMutablePointer<CFString?>.allocate(capacity: 1)
    value.initialize(to: nil)
    defer { value.deinitialize(count: 1); value.deallocate() }
    var size = UInt32(MemoryLayout<CFString?>.size)
    guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, value) == noErr else { return nil }
    return value.pointee.map { $0 as String }
}
func inputDevices() -> [(AudioDeviceID, String, String)] {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else { return [] }
    var devices = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &devices) == noErr else { return [] }
    return devices.compactMap { device in
        var input = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams, mScope: kAudioDevicePropertyScopeInput, mElement: kAudioObjectPropertyElementMain)
        var bytes: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(device, &input, 0, nil, &bytes) == noErr, bytes > 0,
              let uid = deviceString(device, kAudioDevicePropertyDeviceUID),
              let name = deviceString(device, kAudioObjectPropertyName) else { return nil }
        return (device, uid, name)
    }
}
let args = CommandLine.arguments
if args.contains(voiceLockProtocolArgument) {
    print(voiceLockProtocol)
    exit(0)
}
if args.contains(audioDevicesArgument) {
    let value = inputDevices().map { ["id": $0.1, "name": $0.2] }
    if let data = try? JSONSerialization.data(withJSONObject: value), let text = String(data: data, encoding: .utf8) { print(text) }
    exit(0)
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
 guard let index = args.firstIndex(of: "--session"), args.count > index + 1 else { exit(2) }
let directory = URL(fileURLWithPath: args[index + 1])
let heartbeat = directory.appendingPathComponent("heartbeat")
let stateFile = directory.appendingPathComponent("state.json")
var sequence = 0
var engine: AVAudioEngine?
var task: SFSpeechRecognitionTask?
var request: SFSpeechAudioBufferRecognitionRequest?
var lastStatus = "starting"
var lastText = ""
var audioMetadata: [String: Any]?
var lastMeterPublish = Date.distantPast
func publish(_ status: String, _ text: String = "") {
    lastStatus = status
    lastText = text
    sequence += 1
    var value: [String: Any] = ["status": status, "text": text, "sequence": sequence]
    if let metadata = audioMetadata { value["audio"] = metadata }
    if let bytes = try? JSONSerialization.data(withJSONObject: value) {
        try? bytes.write(to: stateFile, options: .atomic)
    }
}
final class SpectrumMeter {
    let setup = vDSP_create_fftsetup(spectrumLog2, FFTRadix(kFFTRadix2))!
    var window = [Float](repeating: 0, count: spectrumFrames)
    init() { vDSP_hann_window(&window, vDSP_Length(spectrumFrames), Int32(vDSP_HANN_NORM)) }
    deinit { vDSP_destroy_fftsetup(setup) }
    func measure(_ buffer: AVAudioPCMBuffer) -> [String: Any]? {
        guard let samples = buffer.floatChannelData?[0], Int(buffer.frameLength) >= spectrumFrames else { return nil }
        let length = spectrumFrames / 2
        var real = [Float](repeating: 0, count: length)
        var imaginary = [Float](repeating: 0, count: length)
        var power = [Float](repeating: 0, count: length)
        var squared = 0.0
        for i in 0..<spectrumFrames {
            let value = Double(samples[i]); squared += value * value
            if i.isMultiple(of: 2) { real[i / 2] = samples[i] * window[i] }
            else { imaginary[i / 2] = samples[i] * window[i] }
        }
        real.withUnsafeMutableBufferPointer { r in
            imaginary.withUnsafeMutableBufferPointer { i in
                var split = DSPSplitComplex(realp: r.baseAddress!, imagp: i.baseAddress!)
                vDSP_fft_zrip(setup, &split, 1, spectrumLog2, FFTDirection(FFT_FORWARD))
                vDSP_zvmags(&split, 1, &power, 1, vDSP_Length(length))
            }
        }
        let rate = buffer.format.sampleRate
        let high = min(spectrumMaximumHz, rate / 2)
        let values = (0..<spectrumBands).map { band -> Double in
            let lowHz = spectrumMinimumHz * pow(high / spectrumMinimumHz, Double(band) / Double(spectrumBands))
            let highHz = spectrumMinimumHz * pow(high / spectrumMinimumHz, Double(band + 1) / Double(spectrumBands))
            let start = max(1, min(length - 1, Int(lowHz * Double(spectrumFrames) / rate)))
            let end = max(start + 1, min(length, Int(highHz * Double(spectrumFrames) / rate)))
            let peak = power[start..<end].max() ?? 0
            let db = 10 * log10(max(Double(peak) / Double(spectrumFrames * spectrumFrames), 1e-12))
            return max(0, min(1, (db - spectrumFloorDB) / (spectrumCeilingDB - spectrumFloorDB)))
        }
        return ["bands": values, "rms": min(1, sqrt(squared / Double(spectrumFrames)) * meterRMSGain),
                "source": "native-microphone", "sample_rate": rate, "timestamp": Date().timeIntervalSince1970 * 1000]
    }
}
@MainActor func finish() {
    engine?.stop()
    request?.endAudio()
    task?.cancel()
    app.terminate(nil)
}
func startListening() {
    guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
          recognizer.supportsOnDeviceRecognition else {
        publish("error", "Mac speech recognition is not available offline. Enable English Dictation in System Settings, then try Mic again.")
        return
    }
    let audio = AVAudioEngine()
    let input = audio.inputNode
    // Preserve the recognizer's existing echo reduction before selecting its input.
    try? input.setVoiceProcessingEnabled(true)
    if let selectedIndex = args.firstIndex(of: deviceUIDArgument), args.count > selectedIndex + 1 {
        guard let selected = inputDevices().first(where: { $0.1 == args[selectedIndex + 1] }) else {
            publish("error", "The selected microphone is unavailable. Choose another input and try Mic again."); return
        }
        var device = selected.0
        let selectedStatus: OSStatus = input.withAudioUnit { unit in
            guard let unit else { return kAudio_ParamError }
            return AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &device, UInt32(MemoryLayout<AudioDeviceID>.size))
        }
        guard selectedStatus == noErr else {
            publish("error", "The selected microphone could not be opened. Choose the system default and try again."); return
        }
    }
    let format = input.outputFormat(forBus: 0)
    guard format.sampleRate > 0, format.channelCount > 0 else {
        publish("error", "No microphone input is available. Choose an input in System Settings → Sound.")
        return
    }
    let session = SFSpeechAudioBufferRecognitionRequest()
    session.requiresOnDeviceRecognition = true
    session.shouldReportPartialResults = true
    let meter = SpectrumMeter()
    do {
        try input.installAudioTap(onBus: 0, bufferSize: bufferFrames, format: format) { buffer, _ in
        let copy = AVAudioPCMBuffer(copying: buffer)
        session.append(copy)
        if let metadata = meter.measure(copy) {
            DispatchQueue.main.async {
                audioMetadata = metadata
                if Date().timeIntervalSince(lastMeterPublish) >= meterPublishSeconds {
                    lastMeterPublish = Date(); publish(lastStatus, lastText)
                }
            }
        }
    }
    } catch {
        publish("error", "The Mac microphone tap could not start. Check the selected input and try Mic again.")
        return
    }
    engine = audio
    request = session
    task = recognizer.recognitionTask(with: session) { result, error in
        DispatchQueue.main.async {
            if let result = result {
                publish(result.isFinal ? "final" : "listening", result.bestTranscription.formattedString)
            } else if let error = error as NSError? {
                publish("error", "Mac speech recognition stopped (\(error.domain) \(error.code)). Click Mic to retry.")
            }
        }
    }
    do { try audio.start(); publish("listening") }
    catch { publish("error", "The Mac microphone could not start. Check microphone permission in System Settings.") }
}
func authorizeSpeech() {
    publish("permission", "Allow Speech Recognition for Jarvis Voice in the macOS permission dialog.")
    SFSpeechRecognizer.requestAuthorization { status in
        DispatchQueue.main.async {
            if status == .authorized { startListening() }
            else { publish("error", "Allow Jarvis Voice under System Settings → Privacy & Security → Speech Recognition.") }
        }
    }
}
let watchdog = Timer.scheduledTimer(withTimeInterval: pollSeconds, repeats: true) { _ in
    let stopped = FileManager.default.fileExists(atPath: directory.appendingPathComponent("stop").path)
    let attributes = try? FileManager.default.attributesOfItem(atPath: heartbeat.path)
    let date = attributes?[.modificationDate] as? Date ?? .distantPast
    if stopped || Date().timeIntervalSince(date) > leaseSeconds {
        MainActor.assumeIsolated { finish() }
    }
}
// Hold ownership in the recording process, not the Python launcher. A server
// restart must not let the desktop helper open audio before this process exits.
guard let microphoneOwnership = VoiceLock() else {
    publish("error", "Another Jarvis voice session is using the microphone.")
    exit(1)
}
publish("permission", "Allow Microphone access for Jarvis Voice in the macOS permission dialog.")
AVCaptureDevice.requestAccess(for: .audio) { allowed in
    DispatchQueue.main.async {
        if allowed { authorizeSpeech() }
        else { publish("error", "Allow Jarvis Voice under System Settings → Privacy & Security → Microphone.") }
    }
}
app.run()
