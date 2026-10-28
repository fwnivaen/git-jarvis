#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
APP="${JARVIS_DESKTOP_APP_PATH:-.local/Jarvis Desktop.app}"
MODULE_CACHE="${JARVIS_SWIFT_MODULE_CACHE:-.local/swift-module-cache}"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$MODULE_CACHE"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>JarvisDesktop</string>
<key>CFBundleIdentifier</key><string>local.fwnivaen.jarvis.desktop</string>
<key>CFBundleName</key><string>Jarvis Voice</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSUIElement</key><true/>
<key>LSMinimumSystemVersion</key><string>27.0</string>
<key>NSMicrophoneUsageDescription</key><string>Jarvis listens while you hold push-to-talk or explicitly enable hands-free listening for this session.</string>
<key>NSSpeechRecognitionUsageDescription</key><string>Jarvis transcribes your speech on this Mac.</string>
</dict></plist>
PLIST
swiftc -target arm64-apple-macosx27.0 -parse-as-library \
  -module-cache-path "$MODULE_CACHE" \
  native/IslandCore.swift native/IslandMotion.swift native/MiniOrb.swift \
  native/IslandText.swift native/IslandLayers.swift native/IslandViews.swift native/Island.swift native/OrbMetal.swift \
  native/IslandDemo.swift native/LiveTextEvidence.swift native/Sounds.swift native/Hotkeys.swift \
  native/Conversation.swift native/ModernSpeech.swift native/HandsFreePolicy.swift native/HandsFree.swift native/SpeechModelInstaller.swift \
  native/TranscriberSmoke.swift \
  native/VoiceLock.swift native/MicrophoneCheck.swift \
  native/TaskWatcher.swift native/BackgroundDelivery.swift native/DeliveryPreferences.swift native/JarvisDesktop.swift \
  -o "$APP/Contents/MacOS/JarvisDesktop" \
  -framework AppKit -framework AVFoundation -framework Speech -framework Carbon -framework MetalKit
codesign --force --sign - "$APP" >/dev/null
