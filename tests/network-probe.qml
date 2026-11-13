// Run in a graphical NixOS session with NetworkManager enabled.
import QtQuick
import Quickshell
import Quickshell.Networking

ShellRoot {
    id: root
    property int attempts: 0
    Timer {
        interval: 500
        running: true
        repeat: true
        onTriggered: {
            root.attempts += 1
            const backend = Networking.backend
            const devicesModel = Networking.devices
            const devices = devicesModel ? devicesModel.values : undefined
            const deviceCount = devices && typeof devices.length === "number" ? devices.length : -1
            if (root.attempts >= 6 && backend === NetworkBackendType.NetworkManager && deviceCount > 0) {
                const states = []
                for (let i = 0; i < deviceCount; i++) {
                    const device = devices[i]
                    states.push(`${DeviceType.toString(device.type)}:${ConnectionState.toString(device.state)}`)
                }
                console.log("Network probe passed:", JSON.stringify({
                    backend: NetworkBackendType.toString(backend),
                    backendValue: backend,
                    connectivity: NetworkConnectivity.toString(Networking.connectivity),
                    connectivityValue: Networking.connectivity,
                    deviceCount,
                    deviceStates: states,
                    attempts: root.attempts
                }))
                Qt.quit()
                return
            }
            if (root.attempts >= 10) {
                console.error("Network probe failed:", JSON.stringify({
                    backend: NetworkBackendType.toString(backend),
                    backendValue: backend,
                    backendType: typeof backend,
                    devicesModelAvailable: !!devicesModel,
                    deviceValuesType: typeof devices,
                    deviceValuesLength: devices && typeof devices.length === "number" ? devices.length : null,
                    deviceModelCount: devicesModel && typeof devicesModel.count === "number" ? devicesModel.count : null,
                    connectivity: NetworkConnectivity.toString(Networking.connectivity),
                    connectivityValue: Networking.connectivity,
                    attempts: root.attempts
                }))
                Qt.exit(1)
            }
        }
    }
}
