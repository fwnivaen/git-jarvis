#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
APP="${JARVIS_VOICE_APP_PATH:-.local/Jarvis Voice.app}"
MODULE_CACHE="${JARVIS_SWIFT_MODULE_CACHE:-.local/swift-module-cache}"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-voice-build.XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT HUP INT TERM
mkdir -p "$APP/Contents/MacOS" "$MODULE_CACHE"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>JarvisVoice</string>
<key>CFBundleIdentifier</key><string>local.jarvis.voice</string>
<key>CFBundleName</key><string>Jarvis Voice</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSUIElement</key><true/>
<key>LSMinimumSystemVersion</key><string>27.0</string>
<key>NSMicrophoneUsageDescription</key><string>Jarvis uses your microphone only while Mic is enabled.</string>
<key>NSSpeechRecognitionUsageDescription</key><string>Jarvis transcribes speech on this Mac to answer your questions.</string>
</dict></plist>
PLIST
cp native/JarvisVoice.swift "$BUILD_DIR/main.swift"
swiftc -target arm64-apple-macosx27.0 -module-cache-path "$MODULE_CACHE" "$BUILD_DIR/main.swift" native/VoiceLock.swift -o "$APP/Contents/MacOS/JarvisVoice" -framework AppKit -framework AVFoundation -framework Speech
codesign --force --sign - "$APP" >/dev/null 2>&1
