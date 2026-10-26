import AppKit
import Carbon

final class Hotkeys {
    static let signature: OSType = 0x4A525653 // JRVS
    static let pushToTalk: UInt32 = 1
    static let cancel: UInt32 = 2
    static let mute: UInt32 = 3
    static let plainEscape: UInt32 = 4

    var pressed: (UInt32) -> Void = { _ in }
    var released: (UInt32) -> Void = { _ in }
    private var handler: EventHandlerRef?
    private var registrations: [UInt32: EventHotKeyRef] = [:]
    var isPushToTalkAvailable: Bool { registrations[Self.pushToTalk] != nil }
    var isMuteAvailable: Bool { registrations[Self.mute] != nil }
    private(set) var pushToTalkStatus: OSStatus = OSStatus(eventNotHandledErr)
    private(set) var cancelStatus: OSStatus = OSStatus(eventNotHandledErr)
    private(set) var muteStatus: OSStatus = OSStatus(eventNotHandledErr)

    init() {
        var types = [EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
                     EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased))]
        let callback: EventHandlerUPP = { _, event, userData in
            guard let event, let userData else { return noErr }
            let owner = Unmanaged<Hotkeys>.fromOpaque(userData).takeUnretainedValue()
            var identifier = EventHotKeyID()
            let status = GetEventParameter(event, UInt32(kEventParamDirectObject),
                                           UInt32(typeEventHotKeyID), nil,
                                           MemoryLayout<EventHotKeyID>.size, nil, &identifier)
            guard status == noErr, identifier.signature == Hotkeys.signature else { return noErr }
            if GetEventKind(event) == UInt32(kEventHotKeyPressed) { owner.pressed(identifier.id) }
            else { owner.released(identifier.id) }
            return noErr
        }
        let installed = InstallEventHandler(GetApplicationEventTarget(), callback, types.count, &types,
                                            Unmanaged.passUnretained(self).toOpaque(), &handler)
        guard installed == noErr else { return }
        pushToTalkStatus = register(Self.pushToTalk, key: UInt32(kVK_Space),
                                    modifiers: UInt32(controlKey | shiftKey),
                                    options: OptionBits(kEventHotKeyExclusive))
        cancelStatus = register(Self.cancel, key: UInt32(kVK_Escape), modifiers: UInt32(optionKey))
        muteStatus = register(Self.mute, key: UInt32(kVK_ANSI_M),
                              modifiers: UInt32(optionKey | cmdKey),
                              options: OptionBits(kEventHotKeyExclusive))
    }

    deinit {
        for item in registrations.values { UnregisterEventHotKey(item) }
        if let handler { RemoveEventHandler(handler) }
    }

    func borrowPlainEscape(_ active: Bool) {
        if active { register(Self.plainEscape, key: UInt32(kVK_Escape), modifiers: 0) }
        else if let item = registrations.removeValue(forKey: Self.plainEscape) { UnregisterEventHotKey(item) }
    }

    func checkEventHandler() -> OSStatus {
        func send(_ kind: UInt32) -> OSStatus {
            var event: EventRef?
            let created = CreateEvent(nil, OSType(kEventClassKeyboard), kind,
                                      GetCurrentEventTime(), 0, &event)
            guard created == noErr, let event else { return created }
            defer { ReleaseEvent(event) }
            var identifier = EventHotKeyID(signature: Self.signature, id: Self.pushToTalk)
            let parameter = SetEventParameter(event, UInt32(kEventParamDirectObject),
                                              UInt32(typeEventHotKeyID),
                                              MemoryLayout<EventHotKeyID>.size, &identifier)
            guard parameter == noErr else { return parameter }
            return SendEventToEventTarget(event, GetApplicationEventTarget())
        }
        let pressed = send(UInt32(kEventHotKeyPressed))
        return pressed == noErr ? send(UInt32(kEventHotKeyReleased)) : pressed
    }

    @discardableResult
    private func register(_ id: UInt32, key: UInt32, modifiers: UInt32,
                          options: OptionBits = 0) -> OSStatus {
        guard handler != nil else { return OSStatus(eventNotHandledErr) }
        guard registrations[id] == nil else { return noErr }
        var reference: EventHotKeyRef?
        let result = RegisterEventHotKey(key, modifiers,
                                         EventHotKeyID(signature: Self.signature, id: id),
                                         GetApplicationEventTarget(), options, &reference)
        if result == noErr, let reference { registrations[id] = reference }
        return result
    }
}
