// Run inside a graphical Flow session with an available default audio output.
import QtQuick
import Quickshell
import Quickshell.Services.Pipewire

ShellRoot {
    id: root
    readonly property var sink: Pipewire.ready ? Pipewire.defaultAudioSink : null
    PwObjectTracker { objects: root.sink ? [root.sink] : [] }
    Timer {
        interval: 2000; running: true
        onTriggered: {
            if (!Pipewire.ready || !root.sink || !root.sink.audio ||
                !isFinite(root.sink.audio.volume) || root.sink.audio.volume < 0) {
                console.error("Audio probe failed: no readable default output")
                Qt.exit(1)
                return
            }
            console.log("Audio probe passed:", root.sink.audio.volume, root.sink.audio.muted)
            Qt.quit()
        }
    }
}
