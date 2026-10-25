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
        register(Self.pushToTalk, key: UInt32(kVK_Space), modifiers: UInt32(controlKey | shiftKey))
        register(Self.cancel, key: UInt32(kVK_Escape), modifiers: UInt32(optionKey))
        register(Self.mute, key: UInt32(kVK_ANSI_M), modifiers: UInt32(optionKey | cmdKey))
    }

    deinit {
        for item in registrations.values { UnregisterEventHotKey(item) }
        if let handler { RemoveEventHandler(handler) }
    }

    func borrowPlainEscape(_ active: Bool) {
        if active { register(Self.plainEscape, key: UInt32(kVK_Escape), modifiers: 0) }
        else if let item = registrations.removeValue(forKey: Self.plainEscape) { UnregisterEventHotKey(item) }
    }

    private func register(_ id: UInt32, key: UInt32, modifiers: UInt32) {
        guard handler != nil, registrations[id] == nil else { return }
        var reference: EventHotKeyRef?
        let result = RegisterEventHotKey(key, modifiers,
                                         EventHotKeyID(signature: Self.signature, id: id),
                                         GetApplicationEventTarget(), 0, &reference)
        if result == noErr, let reference { registrations[id] = reference }
    }
}
