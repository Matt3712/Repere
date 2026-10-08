// App state regression checks: no UI, process, device, or network is started.
extension RepereModel {
    static func checkSessionStates() {
        func fixture() -> RepereModel {
            let model = RepereModel()
            model.devices = [Phone(id: "test", name: "Test phone", version: "18.4")]
            model.selectedID = "test"
            model.latitude = "48,8566"
            model.longitude = "-2,35"
            return model
        }
        func start(_ model: RepereModel) {
            model.operation = .simulate
            model.activeID = "test"
            model.restorationNeeded = true
        }
        let initial = fixture()
        precondition(!initial.canApply, "Preparation must precede a simulation")
        initial.receive(BridgeEvent(kind: "prepared", message: nil, devices: nil), action: .prepare, udid: "test")
        precondition(initial.canApply, "Prepared target and valid coordinates should permit apply")
        initial.latitude = "nan"
        precondition(!initial.canApply, "Non-finite coordinates must disable apply")

        let setupFailure = fixture()
        start(setupFailure)
        setupFailure.receive(BridgeEvent(kind: "unchanged", message: "No set attempted", devices: nil), action: .simulate, udid: "test")
        setupFailure.receive(BridgeEvent(kind: "error", message: "Connection failed", devices: nil), action: .simulate, udid: "test")
        setupFailure.completed(.simulate, code: 1, diagnostic: "")
        precondition(!setupFailure.restorationNeeded && setupFailure.activeID == nil && !setupFailure.isBusy)
        precondition(setupFailure.canEditTarget && setupFailure.statusIsError, "Setup failure must allow preparation again")

        let uncertain = fixture()
        start(uncertain)
        uncertain.receive(BridgeEvent(kind: "error", message: "Set outcome unknown; clear failed", devices: nil), action: .simulate, udid: "test")
        uncertain.completed(.simulate, code: 1, diagnostic: "")
        precondition(uncertain.restorationNeeded && uncertain.activeID == "test")
        precondition(!uncertain.canEditTarget && uncertain.canRestore, "Uncertain simulation must retain a restoration path")

        let canceled = fixture()
        start(canceled)
        canceled.receive(BridgeEvent(kind: "unchanged", message: "Canceled before set", devices: nil), action: .simulate, udid: "test")
        canceled.completed(.simulate, code: 0, diagnostic: "")
        precondition(!canceled.statusIsError && !canceled.restorationNeeded, "Cancel before set is not a failed restoration")

        let stopped = fixture()
        start(stopped)
        stopped.receive(BridgeEvent(kind: "ready", message: "Request sent", devices: nil), action: .simulate, udid: "test")
        precondition(stopped.simulationReady && stopped.restorationNeeded)
        stopped.receive(BridgeEvent(kind: "cleared", message: "Stop request sent", devices: nil), action: .simulate, udid: "test")
        stopped.completed(.simulate, code: 0, diagnostic: "")
        precondition(!stopped.restorationNeeded && !stopped.statusIsError && stopped.activeID == nil)
        let route = fixture()
        route.destinationMode = .route
        route.routeKind = .direct
        route.startLatitude = "48.8566"; route.startLongitude = "2.3522"
        route.endLatitude = "48.8584"; route.endLongitude = "2.2945"
        route.calculateRoute()
        precondition(route.routePoints.count == 2 && route.routeDistance > 4000)
        route.receive(BridgeEvent(kind: "prepared", message: nil, devices: nil), action: .prepare, udid: "test")
        route.routeMinutes = "20"
        precondition(route.requestedDuration == 1200 && abs(route.effectiveSpeed - route.routeDistance / 1200 * 3.6) < 0.000001)
        precondition(route.canApply && route.routeTimingError == nil)
        route.routeMinutes = "0.05"
        precondition(!route.canApply && route.routeTimingError != nil, "Impossible requested speed must be rejected")
        route.routeMinutes = "nan"
        precondition(!route.canApply && route.requestedDuration == nil)
        route.routeMinutes = "1,5"
        precondition(route.requestedDuration == 90)
        route.routeMinutes = "1441"
        precondition(!route.canApply)
        route.routeMinutes = "20"
        route.connectionType = "Network"
        precondition(!route.prepared && !route.canApply, "USB preparation does not prepare Wi-Fi")
        route.connectionType = "USB"
        route.startLatitude = "48.8567"
        precondition(route.routePoints.isEmpty && !route.canApply, "Endpoint edits invalidate the calculated path")
        route.calculateRoute()
        precondition(route.canApply)
        route.routeKind = .road
        precondition(route.routePoints.isEmpty && !route.canApply, "Changing route type invalidates geometry")

        let moving = fixture()
        moving.destinationMode = .route
        moving.operation = .route
        moving.activeID = "test"
        moving.restorationNeeded = true
        moving.routeMinutes = "10"
        moving.receive(BridgeEvent(kind: "ready", message: nil, devices: nil), action: .route, udid: "test")
        moving.receive(BridgeEvent(kind: "route_progress", message: nil, devices: nil,
                                  latitude: 48.85, longitude: 2.35, distance_m: 250, total_m: 1000, paused: true), action: .route, udid: "test")
        precondition(moving.simulationReady && moving.routePaused && moving.routeFraction == 0.25 && moving.traveler != nil)
        precondition(!moving.canEditTarget && moving.canRestore && moving.routeDuration == "8 min")
        moving.receive(BridgeEvent(kind: "route_finished", message: nil, devices: nil), action: .route, udid: "test")
        precondition(moving.routeFinished && moving.routeFraction == 1 && moving.restorationNeeded, "Arrival must hold B until stopped")
        moving.receive(BridgeEvent(kind: "error", message: "Clear failed", devices: nil), action: .route, udid: "test")
        moving.completed(.route, code: 1, diagnostic: "")
        precondition(moving.restorationNeeded && moving.canRestore && !moving.canEditTarget)
        moving.connectionType = "Network"
        moving.changeConnection()
        precondition(moving.activeID == "test" && moving.restorationNeeded, "Recovery can switch transport without switching phone")
        moving.receive(BridgeEvent(kind: "cleared", message: nil, devices: nil), action: .clear, udid: "test")
        moving.completed(.clear, code: 0, diagnostic: "")
        precondition(!moving.restorationNeeded && moving.traveler == nil && moving.canEditTarget)

        let routeSetupFailure = fixture()
        routeSetupFailure.operation = .route
        routeSetupFailure.activeID = "test"
        routeSetupFailure.restorationNeeded = true
        routeSetupFailure.receive(BridgeEvent(kind: "unchanged", message: nil, devices: nil), action: .route, udid: "test")
        routeSetupFailure.completed(.route, code: 0, diagnostic: "")
        precondition(!routeSetupFailure.restorationNeeded && routeSetupFailure.canEditTarget)
        let random = fixture()
        random.destinationMode = .random
        precondition(Operation.random.isSimulation && !random.canApply)
        random.receive(BridgeEvent(kind: "prepared", message: nil, devices: nil), action: .prepare, udid: "test")
        precondition(random.canApply && random.randomRadius == 200 && random.randomSpeed == 5)
        for radius in ["9.99", "10000.01", "-200", "nan", "inf", ""] {
            random.randomRadiusText = radius
            precondition(!random.canApply && random.randomValidationError != nil, "Invalid random radius must disable launch")
        }
        for radius in ["10", "200,5", "10000"] {
            random.randomRadiusText = radius
            precondition(random.canApply, "Boundary radii and decimal commas must be supported")
        }
        random.randomRadiusText = "200"
        for speed in [0.0, 151.0, Double.nan, Double.infinity] {
            random.randomSpeed = speed
            precondition(!random.canApply && random.randomValidationError != nil)
        }
        for speed in [1.0, 5.0, 150.0] { random.randomSpeed = speed; precondition(random.canApply) }
        random.randomSpeed = 5
        random.latitude = "91"
        precondition(!random.canApply && random.randomCenter == nil)
        random.choose(CLLocationCoordinate2D(latitude: -33.8688, longitude: -0.00000001), name: "Circle center")
        precondition(random.canApply && random.randomCenter?.latitude == -33.8688 && random.startLatitude.isEmpty)

        // Keep negative scientific notation attached to its flag for argparse.
        random.longitude = "-1e-8"
        let randomArguments = random.commandArguments(.random, udid: "test", coordinate: random.coordinate)
        precondition(randomArguments.first == "random" && randomArguments.contains("--udid"))
        precondition(randomArguments.contains("--latitude=-33.8688") && randomArguments.contains("--longitude=-1e-08"))
        precondition(randomArguments.contains("--radius=200.0") && randomArguments.contains("--speed=5.0"))
        precondition(!randomArguments.contains("--route-file") && !randomArguments.contains("--duration-seconds"))

        random.operation = .random
        random.activeID = "test"
        random.restorationNeeded = true
        precondition(!random.canPauseMovement && !random.canApply && random.canRestore && !random.canEditTarget)
        random.receive(BridgeEvent(kind: "random_started", message: nil, devices: nil,
                                  latitude: -33.8688, longitude: -1e-8, distance_m: 0, paused: false), action: .random, udid: "test")
        precondition(!random.simulationReady, "Only ready can establish that the first set was sent")
        random.receive(BridgeEvent(kind: "ready", message: nil, devices: nil), action: .random, udid: "test")
        precondition(random.simulationReady && random.canPauseMovement)
        random.routeDistance = 50
        random.routeFinished = true
        random.receive(BridgeEvent(kind: "random_progress", message: nil, devices: nil,
                                  latitude: -33.8687, longitude: 0.00001, distance_m: 1250, total_m: 50, paused: false), action: .random, udid: "test")
        precondition(random.randomTraveled == 1250 && random.routeDistance == 50 && random.routeTraveled == 0)
        precondition(random.canPauseMovement && !random.statusDetail.contains("%") && !random.statusDetail.contains("restant"), "An infinite random session has no arrival percentage")
        let lastTraveler = random.traveler
        random.receive(BridgeEvent(kind: "random_progress", message: nil, devices: nil,
                                  latitude: 91, longitude: Double.nan, distance_m: Double.infinity), action: .random, udid: "test")
        precondition(random.traveler == lastTraveler && random.randomTraveled == 1250, "Invalid progress must not corrupt the marker or distance")
        random.receive(BridgeEvent(kind: "random_progress", message: nil, devices: nil, distance_m: 3), action: .random, udid: "test")
        precondition(random.randomTraveled == 1250, "Cumulative distance cannot go backwards")

        random.routePausePending = true; random.requestedPause = true
        precondition(!random.canPauseMovement)
        random.receive(BridgeEvent(kind: "random_progress", message: nil, devices: nil, paused: false), action: .random, udid: "test")
        precondition(random.routePausePending, "An old running update cannot acknowledge a pending pause")
        random.receive(BridgeEvent(kind: "random_progress", message: nil, devices: nil, paused: true), action: .random, udid: "test")
        precondition(random.routePaused && !random.routePausePending && random.canPauseMovement && random.statusTitle.contains("pause"))
        random.routePausePending = true; random.requestedPause = false
        random.receive(BridgeEvent(kind: "random_progress", message: nil, devices: nil, paused: false), action: .random, udid: "test")
        precondition(!random.routePaused && !random.routePausePending && random.canPauseMovement)
        random.stopping = true
        random.statusTitle = "Arrêt demandé…"
        random.receive(BridgeEvent(kind: "random_progress", message: nil, devices: nil, paused: false), action: .random, udid: "test")
        precondition(!random.canPauseMovement && random.statusTitle == "Arrêt demandé…")
        random.choose(CLLocationCoordinate2D(latitude: 1, longitude: 1))
        precondition(random.randomCenter?.latitude == -33.8688, "An active random session locks its center")

        random.receive(BridgeEvent(kind: "error", message: "Connection lost; clear failed", devices: nil), action: .random, udid: "test")
        random.completed(.random, code: 1, diagnostic: "")
        precondition(random.restorationNeeded && random.activeID == "test" && !random.canEditTarget && random.canRestore)
        precondition(!random.simulationReady && !random.canPauseMovement && !random.canApply)
        random.receive(BridgeEvent(kind: "error", message: "Clear still failed", devices: nil), action: .clear, udid: "test")
        random.completed(.clear, code: 1, diagnostic: "")
        precondition(random.restorationNeeded && random.activeID == "test", "Failed recovery must keep the random session locked")
        random.receive(BridgeEvent(kind: "cleared", message: nil, devices: nil), action: .clear, udid: "test")
        random.completed(.clear, code: 0, diagnostic: "")
        precondition(!random.restorationNeeded && random.activeID == nil && random.traveler == nil && random.randomTraveled == 0)
        precondition(random.canEditTarget && random.canApply)

        let randomCanceled = fixture()
        randomCanceled.destinationMode = .random
        randomCanceled.receive(BridgeEvent(kind: "prepared", message: nil, devices: nil), action: .prepare, udid: "test")
        randomCanceled.operation = .random
        randomCanceled.activeID = "test"
        randomCanceled.restorationNeeded = true
        randomCanceled.receive(BridgeEvent(kind: "unchanged", message: "Stopped before set", devices: nil), action: .random, udid: "test")
        randomCanceled.completed(.random, code: 0, diagnostic: "")
        precondition(!randomCanceled.restorationNeeded && !randomCanceled.statusIsError && randomCanceled.canEditTarget && !randomCanceled.prepared)

        let eventIsolation = fixture()
        eventIsolation.receive(BridgeEvent(kind: "random_started", message: nil, devices: nil,
                                           latitude: 1, longitude: 1, distance_m: 123, paused: true), action: .route, udid: "test")
        precondition(eventIsolation.traveler == nil && eventIsolation.randomTraveled == 0 && !eventIsolation.routePaused)
        eventIsolation.receive(BridgeEvent(kind: "route_finished", message: nil, devices: nil), action: .random, udid: "test")
        precondition(!eventIsolation.routeFinished, "Random sessions never consume route arrival events")
        print("21 app-state scenarios passed (fixed, route, random, duration, transport, recovery). No hardware or UI was used.")
    }
}

@main struct ModelChecks {
    static func main() { RepereModel.checkSessionStates() }
}
