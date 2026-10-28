#!/bin/sh
# Build and check in a disposable directory, leaving the running app untouched.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CHECK_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/jarvis-native-check.XXXXXX")
trap 'rm -rf "$CHECK_ROOT"' EXIT HUP INT TERM
export JARVIS_DESKTOP_APP_PATH="$CHECK_ROOT/Jarvis Desktop.app"
export JARVIS_SWIFT_MODULE_CACHE="${JARVIS_SWIFT_MODULE_CACHE:-$CHECK_ROOT/module-cache}"
sh native/build_desktop.sh
"$JARVIS_DESKTOP_APP_PATH/Contents/MacOS/JarvisDesktop" --check-island
swiftc -target arm64-apple-macosx27.0 -parse-as-library \
  -module-cache-path "$JARVIS_SWIFT_MODULE_CACHE" \
  native/IslandCore.swift native/IslandCheck.swift -o "$CHECK_ROOT/IslandCheck"
swiftc -target arm64-apple-macosx27.0 -parse-as-library \
  -module-cache-path "$JARVIS_SWIFT_MODULE_CACHE" \
  native/IslandCore.swift native/IslandMotion.swift native/MiniOrb.swift \
  native/IslandText.swift native/IslandLayers.swift native/IslandViews.swift native/Island.swift \
  native/OrbMetal.swift native/AnnouncementCheck.swift -o "$CHECK_ROOT/AnnouncementCheck" \
  -framework AppKit -framework MetalKit
"$CHECK_ROOT/AnnouncementCheck"
"$CHECK_ROOT/IslandCheck"
swiftc -target arm64-apple-macosx27.0 -parse-as-library \
  -module-cache-path "$JARVIS_SWIFT_MODULE_CACHE" \
  native/IslandCore.swift native/IslandMotion.swift native/MiniOrb.swift \
  native/OrbMetal.swift native/OrbCheck.swift -o "$CHECK_ROOT/OrbCheck" \
  -framework AppKit -framework MetalKit
"$CHECK_ROOT/OrbCheck"
swiftc -target arm64-apple-macosx27.0 -parse-as-library \
  -module-cache-path "$JARVIS_SWIFT_MODULE_CACHE" \
  native/IslandCore.swift native/IslandMotion.swift native/IslandLayers.swift \
  native/IslandLayerCheck.swift -o "$CHECK_ROOT/IslandLayerCheck" -framework AppKit
"$CHECK_ROOT/IslandLayerCheck"
swiftc -target arm64-apple-macosx27.0 -parse-as-library \
  -module-cache-path "$JARVIS_SWIFT_MODULE_CACHE" \
  native/DeliveryPreferences.swift native/TaskWatcher.swift native/TaskWatcherCheck.swift -o "$CHECK_ROOT/TaskWatcherCheck"
"$CHECK_ROOT/TaskWatcherCheck"
swiftc -target arm64-apple-macosx27.0 -parse-as-library \
  -module-cache-path "$JARVIS_SWIFT_MODULE_CACHE" \
  native/BackgroundDelivery.swift native/BackgroundDeliveryCheck.swift -o "$CHECK_ROOT/BackgroundDeliveryCheck"
"$CHECK_ROOT/BackgroundDeliveryCheck"
swiftc -target arm64-apple-macosx27.0 -parse-as-library \
  -module-cache-path "$JARVIS_SWIFT_MODULE_CACHE" \
  native/DeliveryPreferences.swift native/DeliveryPreferencesCheck.swift -o "$CHECK_ROOT/DeliveryPreferencesCheck"
"$CHECK_ROOT/DeliveryPreferencesCheck"
swiftc -target arm64-apple-macosx27.0 -parse-as-library \
  -module-cache-path "$JARVIS_SWIFT_MODULE_CACHE" \
  native/HandsFreePolicy.swift native/HandsFreeCheck.swift -o "$CHECK_ROOT/HandsFreeCheck"
"$CHECK_ROOT/HandsFreeCheck"
export JARVIS_VOICE_APP_PATH="$CHECK_ROOT/Jarvis Voice.app"
sh native/build_voice.sh
"$JARVIS_VOICE_APP_PATH/Contents/MacOS/JarvisVoice" --voice-lock-protocol
