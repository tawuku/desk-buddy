// JARVIS boot key: hold Space for 3 seconds to boot JARVIS.
//
// A listen-only keyboard tap (it never blocks or changes a key press): when
// Space is held down for HOLD_SECONDS with no other key in between, it runs
// boot.sh once, which starts the pet + JARVIS and plays the boot intro.
// Runs at login as the com.jarvis.boot LaunchAgent. Needs macOS's Input
// Monitoring permission (System Settings -> Privacy & Security -> Input
// Monitoring -> bootkey); without it, it asks, then retries every 10s.
//
// Build: swiftc -O -o bootkey bootkey.swift
import Cocoa
import CoreGraphics

let HOLD_SECONDS = 3.0
let SPACE_KEYCODE: Int64 = 49
let bootScript = CommandLine.arguments.count > 1 ? CommandLine.arguments[1]
    : URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent().appendingPathComponent("boot.sh").path

var tap: CFMachPort?
var spaceDown = false
var holdTimer: Timer?

func log(_ msg: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    FileHandle.standardOutput.write("[bootkey \(stamp)] \(msg)\n".data(using: .utf8)!)
}

func runBoot() {
    log("Space held \(HOLD_SECONDS)s -> \(bootScript)")
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/bin/bash")
    p.arguments = [bootScript]
    do { try p.run() } catch { log("couldn't run boot script: \(error)") }
}

func cancelHold() {
    holdTimer?.invalidate()
    holdTimer = nil
}

let callback: CGEventTapCallBack = { _, type, event, _ in
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let tap = tap { CGEvent.tapEnable(tap: tap, enable: true) }
        return Unmanaged.passUnretained(event)
    }
    let code = event.getIntegerValueField(.keyboardEventKeycode)
    let repeating = event.getIntegerValueField(.keyboardEventAutorepeat) != 0
    if code == SPACE_KEYCODE {
        if type == .keyDown && !repeating && !spaceDown {
            spaceDown = true
            cancelHold()
            holdTimer = Timer.scheduledTimer(withTimeInterval: HOLD_SECONDS, repeats: false) { _ in
                holdTimer = nil
                if spaceDown { runBoot() }
            }
        } else if type == .keyUp {
            spaceDown = false
            cancelHold()
        }
    } else if type == .keyDown {
        // Any other key: that's typing, not a boot gesture.
        spaceDown = false
        cancelHold()
    }
    return Unmanaged.passUnretained(event)
}

func installTap() -> Bool {
    let mask = CGEventMask((1 << CGEventType.keyDown.rawValue) | (1 << CGEventType.keyUp.rawValue))
    guard let t = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                                    eventsOfInterest: mask, callback: callback, userInfo: nil) else { return false }
    tap = t
    let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, t, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
    CGEvent.tapEnable(tap: t, enable: true)
    return true
}

if !CGPreflightListenEventAccess() {
    log("asking for Input Monitoring permission")
    _ = CGRequestListenEventAccess()
}
while !installTap() {
    log("no Input Monitoring permission yet -- allow bootkey in System Settings > Privacy & Security > Input Monitoring")
    Thread.sleep(forTimeInterval: 10)
}
log("ready: hold Space for \(Int(HOLD_SECONDS))s to boot JARVIS")
CFRunLoopRun()
