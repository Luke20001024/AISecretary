import AppKit
import ApplicationServices
import Darwin
import Foundation

private let permissionDenied: Int32 = 77
private let eventCreationFailed: Int32 = 70

private enum CopyMethod: String {
    case accessibilitySelection = "selected-text"
    case accessibilityMenu = "menu"
    case keyboardFallback = "keyboard"
}

private func requestAccessibilityIfNeeded() -> Bool {
    let promptKey = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
    let options = [promptKey: true] as CFDictionary
    return AXIsProcessTrustedWithOptions(options)
}

private func requestedTargetPID() -> pid_t? {
    guard let flagIndex = CommandLine.arguments.firstIndex(of: "--pid"),
          CommandLine.arguments.indices.contains(flagIndex + 1),
          let parsed = Int32(CommandLine.arguments[flagIndex + 1]),
          parsed > 1 else {
        return nil
    }
    return pid_t(parsed)
}

private func requestedShortcutNumber() -> Int? {
    guard let flagIndex = CommandLine.arguments.firstIndex(of: "--trigger-shortcut"),
          CommandLine.arguments.indices.contains(flagIndex + 1),
          let parsed = Int(CommandLine.arguments[flagIndex + 1]),
          (1...3).contains(parsed) else {
        return nil
    }
    return parsed
}

private func activateTarget(_ targetPID: pid_t?) -> Bool {
    guard let targetPID else {
        return true
    }
    guard let application = NSRunningApplication(processIdentifier: targetPID) else {
        return false
    }

    if !application.isActive {
        application.activate(options: [.activateIgnoringOtherApps])
    }

    var attempts = 0
    while attempts < 30 {
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == targetPID {
            return true
        }
        usleep(20_000)
        attempts += 1
    }
    return NSWorkspace.shared.frontmostApplication?.processIdentifier == targetPID
}

private func waitForTriggerModifiersToRelease() -> Bool {
    var attempts = 0
    while attempts < 75 {
        let flags = CGEventSource.flagsState(.hidSystemState)
        if !flags.contains(.maskControl) {
            return true
        }
        usleep(20_000)
        attempts += 1
    }
    return !CGEventSource.flagsState(.hidSystemState).contains(.maskControl)
}

private func attributeValue(
    _ element: AXUIElement,
    _ attribute: CFString
) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, attribute, &value) == .success else {
        return nil
    }
    return value
}

private func stringAttribute(
    _ element: AXUIElement,
    _ attribute: CFString
) -> String? {
    return attributeValue(element, attribute) as? String
}

private func boolAttribute(
    _ element: AXUIElement,
    _ attribute: CFString
) -> Bool? {
    return attributeValue(element, attribute) as? Bool
}

private func children(of element: AXUIElement) -> [AXUIElement] {
    return attributeValue(element, kAXChildrenAttribute as CFString)
        as? [AXUIElement] ?? []
}

private func normalizedMenuTitle(_ value: String) -> String {
    return value
        .replacingOccurrences(of: "…", with: "")
        .replacingOccurrences(of: "...", with: "")
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .lowercased()
}

private func copyMenuItem(in element: AXUIElement, depth: Int = 0) -> AXUIElement? {
    guard depth <= 8 else {
        return nil
    }

    let role = stringAttribute(element, kAXRoleAttribute as CFString)
    let title = normalizedMenuTitle(
        stringAttribute(element, kAXTitleAttribute as CFString) ?? ""
    )
    let commandCharacter = (
        stringAttribute(element, kAXMenuItemCmdCharAttribute as CFString) ?? ""
    ).lowercased()
    let exactCopyTitles: Set<String> = ["copy", "复制", "拷贝"]

    if role == (kAXMenuItemRole as String),
       (exactCopyTitles.contains(title) || commandCharacter == "c"),
       boolAttribute(element, kAXEnabledAttribute as CFString) != false {
        return element
    }

    for child in children(of: element) {
        if let item = copyMenuItem(in: child, depth: depth + 1) {
            return item
        }
    }
    return nil
}

private func nonemptySelectedText(
    in element: AXUIElement,
    depth: Int = 0
) -> String? {
    guard depth <= 4 else {
        return nil
    }

    if let selected = stringAttribute(
        element,
        kAXSelectedTextAttribute as CFString
    ), !selected.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        return selected
    }

    if let focusedValue = attributeValue(
        element,
        kAXFocusedUIElementAttribute as CFString
    ), CFGetTypeID(focusedValue) == AXUIElementGetTypeID() {
        let focused = focusedValue as! AXUIElement
        if let selected = nonemptySelectedText(in: focused, depth: depth + 1) {
            return selected
        }
    }

    for child in children(of: element) {
        if let selected = nonemptySelectedText(in: child, depth: depth + 1) {
            return selected
        }
    }
    return nil
}

private func copyAccessibilitySelection(for targetPID: pid_t?) -> Bool {
    guard let targetPID else {
        return false
    }
    let application = AXUIElementCreateApplication(targetPID)
    guard let selected = nonemptySelectedText(in: application) else {
        return false
    }

    let pasteboard = NSPasteboard.general
    pasteboard.clearContents()
    return pasteboard.setString(selected, forType: .string)
}

private func pressCopyMenuItem(for targetPID: pid_t?) -> Bool {
    guard let targetPID else {
        return false
    }
    _ = activateTarget(targetPID)

    let application = AXUIElementCreateApplication(targetPID)
    guard let menuBarValue = attributeValue(
        application,
        kAXMenuBarAttribute as CFString
    ) else {
        return false
    }
    let menuBar = menuBarValue as! AXUIElement
    let editTitles: Set<String> = ["edit", "编辑"]

    for menuBarItem in children(of: menuBar) {
        let title = normalizedMenuTitle(
            stringAttribute(menuBarItem, kAXTitleAttribute as CFString) ?? ""
        )
        guard editTitles.contains(title) else {
            continue
        }

        if let item = copyMenuItem(in: menuBarItem),
           AXUIElementPerformAction(item, kAXPressAction as CFString) == .success {
            return true
        }

        // Some applications expose the Edit menu's children only while it is open.
        _ = AXUIElementPerformAction(menuBarItem, kAXPressAction as CFString)
        usleep(40_000)
        if let item = copyMenuItem(in: menuBarItem),
           AXUIElementPerformAction(item, kAXPressAction as CFString) == .success {
            return true
        }
    }
    return false
}

private func postCommandCopy(to targetPID: pid_t?) -> Bool {
    guard waitForTriggerModifiersToRelease() else {
        return false
    }
    _ = activateTarget(targetPID)

    guard let source = CGEventSource(stateID: .hidSystemState),
          let commandDown = CGEvent(
              keyboardEventSource: source,
              virtualKey: 55,
              keyDown: true
          ),
          let copyDown = CGEvent(
              keyboardEventSource: source,
              virtualKey: 8,
              keyDown: true
          ),
          let copyUp = CGEvent(
              keyboardEventSource: source,
              virtualKey: 8,
              keyDown: false
          ),
          let commandUp = CGEvent(
              keyboardEventSource: source,
              virtualKey: 55,
              keyDown: false
          ) else {
        return false
    }

    commandDown.flags = .maskCommand
    copyDown.flags = .maskCommand
    copyUp.flags = .maskCommand
    commandUp.flags = []

    let events = [commandDown, copyDown, copyUp, commandUp]
    for event in events {
        if let targetPID {
            event.postToPid(targetPID)
        } else {
            event.post(tap: .cghidEventTap)
        }
        usleep(25_000)
    }
    return true
}

private func performCopy(to targetPID: pid_t?) -> CopyMethod? {
    if copyAccessibilitySelection(for: targetPID) {
        return .accessibilitySelection
    }
    if pressCopyMenuItem(for: targetPID) {
        return .accessibilityMenu
    }
    if postCommandCopy(to: targetPID) {
        return .keyboardFallback
    }
    return nil
}

private func triggerServiceShortcut(
    _ shortcutNumber: Int,
    targetPID: pid_t?
) -> Bool {
    let numberKeyCodes: [Int: CGKeyCode] = [1: 18, 2: 19, 3: 20]
    guard let numberKeyCode = numberKeyCodes[shortcutNumber],
          waitForTriggerModifiersToRelease() else {
        return false
    }
    _ = activateTarget(targetPID)

    guard let source = CGEventSource(stateID: .hidSystemState),
          let controlDown = CGEvent(
              keyboardEventSource: source,
              virtualKey: 59,
              keyDown: true
          ),
          let numberDown = CGEvent(
              keyboardEventSource: source,
              virtualKey: numberKeyCode,
              keyDown: true
          ),
          let numberUp = CGEvent(
              keyboardEventSource: source,
              virtualKey: numberKeyCode,
              keyDown: false
          ),
          let controlUp = CGEvent(
              keyboardEventSource: source,
              virtualKey: 59,
              keyDown: false
          ) else {
        return false
    }

    controlDown.flags = .maskControl
    numberDown.flags = .maskControl
    numberUp.flags = .maskControl
    controlUp.flags = []

    for event in [controlDown, numberDown, numberUp, controlUp] {
        event.post(tap: .cghidEventTap)
        usleep(30_000)
    }
    return true
}

@main
private struct MementoSelectionCopy {
    static func main() {
        guard requestAccessibilityIfNeeded() else {
            FileHandle.standardError.write(
                Data("MEMENTO_ACCESSIBILITY_REQUIRED\n".utf8)
            )
            exit(permissionDenied)
        }

        if CommandLine.arguments.contains("--check") {
            exit(EXIT_SUCCESS)
        }

        if let shortcutNumber = requestedShortcutNumber() {
            guard triggerServiceShortcut(
                shortcutNumber,
                targetPID: requestedTargetPID()
            ) else {
                FileHandle.standardError.write(
                    Data("MEMENTO_SHORTCUT_TRIGGER_FAILED\n".utf8)
                )
                exit(eventCreationFailed)
            }
            FileHandle.standardOutput.write(
                Data("MEMENTO_SHORTCUT_TRIGGERED=\(shortcutNumber)\n".utf8)
            )
            exit(EXIT_SUCCESS)
        }

        guard let method = performCopy(to: requestedTargetPID()) else {
            FileHandle.standardError.write(
                Data("MEMENTO_COPY_FAILED\n".utf8)
            )
            exit(eventCreationFailed)
        }

        FileHandle.standardOutput.write(
            Data("MEMENTO_COPY_METHOD=\(method.rawValue)\n".utf8)
        )

        exit(EXIT_SUCCESS)
    }
}
