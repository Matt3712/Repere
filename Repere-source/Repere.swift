import SwiftUI
import CoreLocation
import WebKit
import AppKit

private let ink = Color(red: 0.94, green: 0.95, blue: 0.97)
private let accent = Color(red: 0.04, green: 0.52, blue: 1.0)
private let canvas = Color(red: 0.075, green: 0.082, blue: 0.096)
private let surface = Color(red: 0.115, green: 0.125, blue: 0.145)
private let raised = Color(red: 0.16, green: 0.175, blue: 0.20)
private let muted = Color(red: 0.62, green: 0.65, blue: 0.70)
private let outline = Color.white.opacity(0.08)

struct Phone: Identifiable, Decodable, Equatable {
    let id: String
    let name: String
    let version: String
    var connection: String? = nil
}

struct BridgeEvent: Decodable {
    let kind: String
    let message: String?
    let devices: [Phone]?
    var latitude: Double? = nil
    var longitude: Double? = nil
    var distance_m: Double? = nil
    var total_m: Double? = nil
    var paused: Bool? = nil
}

struct RoutePoint: Codable, Equatable {
    let latitude: Double
    let longitude: Double
    var coordinate: CLLocationCoordinate2D { CLLocationCoordinate2D(latitude: latitude, longitude: longitude) }
    var valid: Bool { latitude.isFinite && longitude.isFinite && (-90...90).contains(latitude) && (-180...180).contains(longitude) }
    var pair: [Double] { [latitude, longitude] }
    func distance(to other: RoutePoint) -> Double {
        let r = Double.pi / 180
        let dlat = (other.latitude - latitude) * r, dlon = (other.longitude - longitude) * r
        let h = pow(sin(dlat / 2), 2) + cos(latitude * r) * cos(other.latitude * r) * pow(sin(dlon / 2), 2)
        return 6_371_008.8 * 2 * asin(sqrt(min(1, max(0, h))))
    }
}

private struct RoadResponse: Decodable {
    struct Route: Decodable {
        struct Geometry: Decodable { let coordinates: [[Double]] }
        let geometry: Geometry
    }
    let code: String
    let routes: [Route]?
    var points: [RoutePoint]? {
        guard code == "Ok", let values = routes?.first?.geometry.coordinates,
              (2...10000).contains(values.count) else { return nil }
        let points = values.compactMap { pair -> RoutePoint? in
            guard pair.count >= 2 else { return nil }
            let point = RoutePoint(latitude: pair[1], longitude: pair[0])
            return point.valid ? point : nil
        }
        return points.count == values.count ? points : nil
    }
}

enum DestinationMode: String, CaseIterable { case fixed = "Position fixe", route = "Trajet A → B", random = "Aléatoire" }
enum RouteEndpoint: String { case start = "A", end = "B" }
enum RouteKind: String, CaseIterable { case road = "Par la route", direct = "Ligne directe" }
enum RouteTiming: String, CaseIterable { case duration = "Durée", speed = "Vitesse" }

struct SearchPlace {
    let name: String
    let detail: String
    let coordinate: CLLocationCoordinate2D
}

private struct PhotonResponse: Decodable {
    struct Feature: Decodable {
        struct Geometry: Decodable { let coordinates: [Double] }
        struct Properties: Decodable {
            let name: String?
            let city: String?
            let country: String?
            let street: String?
        }
        let geometry: Geometry
        let properties: Properties
        var place: SearchPlace? {
            let values = geometry.coordinates
            guard values.count >= 2, values[0].isFinite, values[1].isFinite,
                  (-180...180).contains(values[0]), (-90...90).contains(values[1]) else { return nil }
            let detail = [properties.street, properties.city, properties.country].compactMap { $0 }.joined(separator: ", ")
            return SearchPlace(name: properties.name ?? properties.city ?? "Lieu", detail: detail,
                               coordinate: CLLocationCoordinate2D(latitude: values[1], longitude: values[0]))
        }
    }
    let features: [Feature]
}

struct JournalEntry: Identifiable {
    let id = UUID()
    let date = Date()
    let message: String
}

enum Operation: String {
    case devices, prepare, simulate, route, random, clear
    var isSimulation: Bool { self == .simulate || self == .route || self == .random }
}

/// stdout is decoded on one reader queue. Its events and termination callback are
/// dispatched to the main queue in order, including multiple JSON lines per read.
final class BridgeProcess {
    let token = UUID()
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let errors = Pipe()
    private let errorGroup = DispatchGroup()
    private let errorLock = NSLock()
    private var errorText = ""

    init(resources: URL, arguments: [String], event: @escaping (BridgeEvent) -> Void,
         finished: @escaping (Int32, String) -> Void) throws {
        let python = resources.appendingPathComponent("runtime/bin/python3")
        let bridge = resources.appendingPathComponent("bridge.py")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.fileExists(atPath: bridge.path) else {
            throw NSError(domain: "Repere", code: 1, userInfo: [NSLocalizedDescriptionKey:
                "Le moteur de connexion manque dans cette copie de Repère. Ouvrez l’application complète générée par le script de construction."])
        }
        process.executableURL = python
        process.arguments = ["-u", bridge.path] + arguments
        process.currentDirectoryURL = resources
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
        try process.run()

        errorGroup.enter()
        DispatchQueue.global(qos: .utility).async { [self] in
            while true {
                let data = errors.fileHandleForReading.availableData
                if data.isEmpty { break }
                let text = String(decoding: data, as: UTF8.self)
                errorLock.lock()
                errorText = String((errorText + text).suffix(6000))
                errorLock.unlock()
            }
            errorGroup.leave()
        }
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            var pending = Data()
            func deliver(_ line: Data) {
                guard !line.isEmpty else { return }
                if let decoded = try? JSONDecoder().decode(BridgeEvent.self, from: line) {
                    DispatchQueue.main.async { event(decoded) }
                } else {
                    let invalid = BridgeEvent(kind: "error", message: "Le moteur a renvoyé une réponse illisible.", devices: nil)
                    DispatchQueue.main.async { event(invalid) }
                }
            }
            while true {
                let chunk = output.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                pending.append(chunk)
                while let newline = pending.firstIndex(of: 10) {
                    deliver(Data(pending[..<newline]))
                    pending.removeSubrange(...newline)
                }
                if pending.count > 1_000_000 {
                    pending.removeAll()
                    DispatchQueue.main.async {
                        event(BridgeEvent(kind: "error", message: "La réponse du moteur dépasse la taille attendue.", devices: nil))
                    }
                }
            }
            deliver(pending)
            process.waitUntilExit()
            errorGroup.wait()
            errorLock.lock()
            let diagnostic = errorText.trimmingCharacters(in: .whitespacesAndNewlines)
            errorLock.unlock()
            let code = process.terminationStatus
            DispatchQueue.main.async { finished(code, diagnostic) }
        }
    }

    func stop() throws {
        try command("stop")
    }
    func command(_ value: String) throws {
        guard process.isRunning else { return }
        try input.fileHandleForWriting.write(contentsOf: Data((value + "\n").utf8))
    }
}

final class RepereModel: ObservableObject {
    @Published var devices: [Phone] = []
    @Published var selectedID = ""
    @Published var latitude = ""
    @Published var longitude = ""
    @Published var placeName = "Choisissez un point sur la carte"
    @Published var searchText = ""
    @Published var searchResults: [SearchPlace] = []
    @Published var searching = false
    @Published var searchMessage = ""
    @Published var statusTitle = "Connectez votre iPhone"
    @Published var statusDetail = "Un câble USB et le mode développeur sont nécessaires."
    @Published var statusIsError = false
    @Published var operation: Operation?
    @Published var activeID: String?
    @Published var restorationNeeded = false
    @Published var simulationReady = false
    @Published var stopping = false
    @Published var journal: [JournalEntry] = []
    @Published var centerRequest = 0
    @Published var showGuide = false
    @Published var connectionType = "USB"
    @Published var destinationMode: DestinationMode = .fixed
    @Published var routeEndpoint: RouteEndpoint = .start
    @Published var startLatitude = "" { didSet { invalidateRoute() } }
    @Published var startLongitude = "" { didSet { invalidateRoute() } }
    @Published var endLatitude = "" { didSet { invalidateRoute() } }
    @Published var endLongitude = "" { didSet { invalidateRoute() } }
    @Published var routeKind: RouteKind = .road { didSet { invalidateRoute() } }
    @Published var routePoints: [RoutePoint] = []
    @Published var routeCalculating = false
    @Published var routeMessage = "Choisissez le départ A puis l’arrivée B sur la carte."
    @Published var routeSpeed = 30.0
    @Published var routeTiming: RouteTiming = .duration
    @Published var routeMinutes = "20"
    @Published var routeDistance = 0.0
    @Published var routeTraveled = 0.0
    @Published var routePaused = false
    @Published var routeFinished = false
    @Published var routePausePending = false
    @Published var randomRadiusText = "200"
    @Published var randomSpeed = 5.0
    @Published var randomTraveled = 0.0
    @Published var traveler: RoutePoint?
    @Published var mapFocus: RoutePoint?
    @Published var routeCenterRequest = 0
    private var routeTask: URLSessionDataTask?
    private var routeRequestID = UUID()
    private var routeCache: [String: [RoutePoint]] = [:]
    private var lastRouteDate = Date.distantPast
    private var routeFile: URL?
    private var requestedPause: Bool?
    private var sessionActivity: NSObjectProtocol?
    let preview = CommandLine.arguments.contains("--preview")
    private var bridge: BridgeProcess?
    private var commandID: UUID?
    private var operationReceivedSuccess = false
    private var operationReceivedError = false
    private var quitRequested = false
    private var search: URLSessionDataTask?
    private var searchCache: [String: [SearchPlace]] = [:]
    private var lastSearchDate = Date.distantPast
    private var searchID = UUID()
    private var preparedIDs = Set<String>()

    var isBusy: Bool { operation != nil }
    var canEditTarget: Bool { !restorationNeeded && operation?.isSimulation != true }
    var selectedPhone: Phone? { devices.first { $0.id == selectedID } }
    var activePhoneName: String { devices.first { $0.id == activeID }?.name ?? "l’iPhone sélectionné" }
    var prepared: Bool { preparedIDs.contains(selectedID + ":" + connectionType) }
    var coordinate: CLLocationCoordinate2D? {
        guard let lat = decimal(latitude), let lon = decimal(longitude),
              (-90...90).contains(lat), (-180...180).contains(lon) else { return nil }
        return CLLocationCoordinate2D(latitude: lat, longitude: lon)
    }
    var coordinateError: String? {
        if latitude.isEmpty && longitude.isEmpty { return nil }
        guard let lat = decimal(latitude), let lon = decimal(longitude) else {
            return "Saisissez deux nombres. La virgule décimale est acceptée."
        }
        if !(-90...90).contains(lat) { return "La latitude doit être comprise entre −90 et 90."
        }
        if !(-180...180).contains(lon) { return "La longitude doit être comprise entre −180 et 180." }
        return nil
    }
    var canApply: Bool {
        guard !preview, prepared, !isBusy, !restorationNeeded, !selectedID.isEmpty else { return false }
        switch destinationMode {
        case .fixed: return coordinate != nil
        case .route: return routePoints.count >= 2 && !routeCalculating && routeTimingError == nil
        case .random: return randomCenter != nil && randomValidationError == nil
        }
    }
    var canRestore: Bool {
        !preview && !stopping && (operation?.isSimulation == true || (!isBusy && (activeID != nil || !selectedID.isEmpty)))
    }
    var routeStart: RoutePoint? { routePoint(startLatitude, startLongitude) }
    var routeEnd: RoutePoint? { routePoint(endLatitude, endLongitude) }
    var randomCenter: RoutePoint? { routePoint(latitude, longitude) }
    var randomRadius: Double? {
        guard let radius = decimal(randomRadiusText), (10...10000).contains(radius) else { return nil }
        return radius
    }
    var randomValidationError: String? {
        guard randomRadius != nil else { return "Saisissez un rayon de 10 à 10 000 mètres." }
        guard randomSpeed.isFinite, (1...150).contains(randomSpeed) else { return "Choisissez une vitesse de 1 à 150 km/h." }
        return nil
    }
    var canPauseMovement: Bool {
        (operation == .route || operation == .random) && simulationReady && !stopping
            && (operation != .route || !routeFinished) && !routePausePending
    }
    var canCalculateRoute: Bool { canEditTarget && !routeCalculating && routeStart != nil && routeEnd != nil }
    var routeFraction: Double { routeDistance > 0 ? min(1, max(0, routeTraveled / routeDistance)) : 0 }
    var requestedDuration: Double? {
        guard let minutes = decimal(routeMinutes), (1.0 / 60...1440).contains(minutes) else { return nil }
        return minutes * 60
    }
    var effectiveSpeed: Double {
        routeTiming == .duration ? routeDistance / (requestedDuration ?? 1) * 3.6 : routeSpeed
    }
    var routeTimingError: String? {
        if routeTiming == .duration && requestedDuration == nil { return "Saisissez une durée comprise entre 1 seconde et 24 heures, en minutes." }
        if !effectiveSpeed.isFinite || effectiveSpeed <= 0 { return "Tracez le trajet pour calculer sa durée et sa vitesse." }
        if effectiveSpeed > 150 {
            let minimumMinutes = ceil(routeDistance / 150 * 3.6 / 60 * 100) / 100
            return "Durée trop courte : choisissez au moins \(String(format: "%.2f", minimumMinutes)) min (150 km/h maximum)."
        }
        if routeTiming == .speed && routeSpeed < 1 { return "Choisissez une vitesse de 1 à 150 km/h." }
        return nil
    }
    var routeDuration: String {
        let seconds = routeTiming == .duration ? (requestedDuration ?? 0) * (1 - routeFraction) : max(0, routeDistance - routeTraveled) / max(1, routeSpeed) * 3.6
        guard seconds.isFinite else { return "—" }
        if seconds < 60 { return "\(Int(ceil(seconds))) s" }
        let minutes = Int(ceil(seconds / 60))
        return minutes >= 60 ? "\(minutes / 60) h \(minutes % 60) min" : "\(minutes) min"
    }
    var connectionLabel: String { connectionType == "Network" ? "Wi-Fi" : "USB" }

    init() {
        if preview {
            statusTitle = "Aperçu de l’interface"
            statusDetail = "Aucune connexion à un iPhone n’est lancée dans ce mode."
        }
    }

    private func decimal(_ text: String) -> Double? {
        let normalized = text.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: ",", with: ".").replacingOccurrences(of: "−", with: "-")
        guard let value = Double(normalized), value.isFinite else { return nil }
        return value
    }

    private func routePoint(_ latitude: String, _ longitude: String) -> RoutePoint? {
        guard let lat = decimal(latitude), let lon = decimal(longitude) else { return nil }
        let point = RoutePoint(latitude: lat, longitude: lon)
        return point.valid ? point : nil
    }

    func changeConnection() {
        guard !isBusy else { return }
        preparedIDs.removeAll()
        if restorationNeeded {
            status("Reconnexion pour restaurer", "Utilisez « Restaurer le GPS » avec la connexion \(connectionLabel) sur le même iPhone.")
            return
        }
        devices = []
        selectedID = ""
        scan()
    }

    private func invalidateRoute() {
        routeRequestID = UUID()
        routeTask?.cancel()
        routeTask = nil
        routeCalculating = false
        routePoints = []
        routeDistance = 0
        routeTraveled = 0
        traveler = nil
        routeFinished = false
        routeMessage = "Choisissez A et B, puis tracez le trajet."
    }

    func swapRoute() {
        guard canEditTarget else { return }
        let lat = startLatitude, lon = startLongitude
        startLatitude = endLatitude; startLongitude = endLongitude
        endLatitude = lat; endLongitude = lon
    }

    private func acceptRoute(_ points: [RoutePoint]) {
        guard points.count >= 2, points.allSatisfy(\.valid) else { return }
        let distance = zip(points, points.dropFirst()).reduce(0.0) { $0 + $1.0.distance(to: $1.1) }
        guard distance >= 1 else { routeMessage = "Choisissez deux points distants d’au moins un mètre."; return }
        routePoints = points
        routeDistance = distance
        routeTraveled = 0
        routeCenterRequest += 1
        routeMessage = routeKind == .road ? "Tracé routier prêt. A et B sont ajustés à la route accessible." : "Tracé direct prêt : il traverse les obstacles éventuels."
    }

    func calculateRoute() {
        guard canCalculateRoute, let a = routeStart, let b = routeEnd else { return }
        guard a.distance(to: b) >= 1 else { routeMessage = "Choisissez deux points distants d’au moins un mètre."; return }
        if routeKind == .direct { acceptRoute([a, b]); return }
        let key = "\(a.longitude),\(a.latitude);\(b.longitude),\(b.latitude)"
        if let points = routeCache[key] { acceptRoute(points); return }
        guard Date().timeIntervalSince(lastRouteDate) >= 1 else {
            routeMessage = "Attendez une seconde avant de recalculer."; return
        }
        let base = ProcessInfo.processInfo.environment["REPERE_ROUTER_URL"] ?? "https://router.project-osrm.org"
        guard var url = URLComponents(string: base), url.scheme == "https", url.host != nil else {
            routeMessage = "L’adresse du service de trajet est invalide."; return
        }
        url.path = url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        url.path = "/" + (url.path.isEmpty ? "" : url.path + "/") + "route/v1/driving/" + key
        url.queryItems = [URLQueryItem(name: "overview", value: "full"), URLQueryItem(name: "geometries", value: "geojson"), URLQueryItem(name: "steps", value: "false")]
        guard let endpoint = url.url else { return }
        var request = URLRequest(url: endpoint, cachePolicy: .returnCacheDataElseLoad, timeoutInterval: 25)
        request.setValue("Repere/1.1 (personal macOS route simulation)", forHTTPHeaderField: "User-Agent")
        lastRouteDate = Date()
        let token = UUID()
        routeRequestID = token
        routeCalculating = true
        routeMessage = "Calcul de l’itinéraire routier…"
        let task = URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            let successful = (response as? HTTPURLResponse)?.statusCode == 200 && error == nil
            let points = successful && (data?.count ?? 0) <= 5_000_000
                ? data.flatMap { try? JSONDecoder().decode(RoadResponse.self, from: $0) }?.points : nil
            DispatchQueue.main.async {
                guard let self, self.routeRequestID == token else { return }
                self.routeCalculating = false
                self.routeTask = nil
                guard let points else {
                    self.routeMessage = "Aucun trajet routier disponible. Rapprochez les points d’une route, réessayez ou choisissez « Ligne directe »."; return
                }
                if self.routeCache.count >= 12 { self.routeCache.removeAll() }
                self.routeCache[key] = points
                self.acceptRoute(points)
            }
        }
        routeTask = task
        task.resume()
    }

    func toggleMovementPause() {
        guard canPauseMovement, let bridge else { return }
        do {
            try bridge.command(routePaused ? "resume" : "pause")
            requestedPause = !routePaused
            routePausePending = true
        } catch { status("Commande de pause non envoyée", error.localizedDescription, error: true) }
    }

    private func removeRouteFile() {
        if let file = routeFile { try? FileManager.default.removeItem(at: file) }
        routeFile = nil
    }

    func choose(_ coordinate: CLLocationCoordinate2D, name: String? = nil, center: Bool = false) {
        guard canEditTarget else { return }
        let lat = String(format: "%.6f", locale: Locale(identifier: "en_US_POSIX"), coordinate.latitude)
        let lon = String(format: "%.6f", locale: Locale(identifier: "en_US_POSIX"), coordinate.longitude)
        if destinationMode == .route {
            if routeEndpoint == .start {
                startLatitude = lat; startLongitude = lon
                routeEndpoint = .end
            } else { endLatitude = lat; endLongitude = lon }
        } else { latitude = lat; longitude = lon; placeName = name ?? "Point choisi sur la carte" }
        mapFocus = RoutePoint(latitude: coordinate.latitude, longitude: coordinate.longitude)
        searchResults = []
        searchMessage = ""
        if center { centerRequest += 1 }
    }

    func findPlace() {
        guard canEditTarget else { return }
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { searchResults = []; return }
        guard !searching else { return }
        if let cached = searchCache[query] { searchResults = cached; return }
        guard Date().timeIntervalSince(lastSearchDate) >= 1 else { return }
        lastSearchDate = Date()
        var url = URLComponents(string: ProcessInfo.processInfo.environment["REPERE_GEOCODER_URL"] ?? "https://photon.komoot.io/api/")!
        url.queryItems = [URLQueryItem(name: "q", value: query), URLQueryItem(name: "limit", value: "5"), URLQueryItem(name: "lang", value: "fr")]
        guard let endpoint = url.url, endpoint.scheme == "https" else { return }
        var request = URLRequest(url: endpoint, cachePolicy: .returnCacheDataElseLoad, timeoutInterval: 20)
        request.setValue("Repere/1.0 (personal macOS location tool)", forHTTPHeaderField: "User-Agent")
        let current = UUID()
        searchID = current
        searching = true
        searchMessage = ""
        searchResults = []
        let lookup = URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            let successful = (response as? HTTPURLResponse)?.statusCode == 200
            let places = successful ? (data.flatMap { try? JSONDecoder().decode(PhotonResponse.self, from: $0) }?.features.compactMap { $0.place } ?? []) : []
            DispatchQueue.main.async {
                guard let self, self.searchID == current else { return }
                self.searching = false
                self.searchResults = places
                if successful && error == nil { self.searchCache[query] = places }
                if places.isEmpty {
                    self.searchMessage = successful && error == nil ? "Aucun lieu trouvé. Essayez une adresse plus précise." : "Recherche indisponible. Vous pouvez cliquer sur la carte ou saisir des coordonnées."
                }
            }
        }
        search = lookup
        lookup.resume()
    }

    func scan() {
        guard !preview, !isBusy, !restorationNeeded else { return }
        status("Recherche de l’iPhone…", "Déverrouillez l’iPhone et acceptez « Faire confiance » si demandé.")
        run(.devices)
    }

    func prepare() {
        guard !preview, !isBusy, !restorationNeeded, !selectedID.isEmpty else { return }
        status("Préparation en cours…", "Gardez l’iPhone déverrouillé. macOS peut demander une autorisation.")
        run(.prepare, udid: selectedID)
    }

    func apply() {
        guard canApply else { return }
        if destinationMode == .route {
            let file = FileManager.default.temporaryDirectory.appendingPathComponent("repere-route-\(UUID().uuidString).json")
            do {
                let data = try JSONEncoder().encode(routePoints)
                guard FileManager.default.createFile(atPath: file.path, contents: data, attributes: [.posixPermissions: 0o600]) else {
                    throw NSError(domain: "Repere", code: 2, userInfo: [NSLocalizedDescriptionKey: "Impossible de préparer le fichier du trajet."])
                }
                routeFile = file
            } catch { status("Trajet indisponible", error.localizedDescription, error: true); return }
        }
        activeID = selectedID
        restorationNeeded = true
        simulationReady = false
        stopping = false
        routeTraveled = 0; randomTraveled = 0; routePaused = false; routeFinished = false; routePausePending = false; requestedPause = nil; traveler = nil
        status("Connexion au service de simulation…", connectionType == "Network" ? "Gardez le Mac allumé et les deux appareils sur le même Wi-Fi." : "Gardez le câble branché pendant la session.")
        if destinationMode == .route { run(.route, udid: selectedID) }
        else if destinationMode == .random { run(.random, udid: selectedID, coordinate: coordinate) }
        else { run(.simulate, udid: selectedID, coordinate: coordinate) }
    }

    func restore() {
        guard canRestore else { return }
        if operation?.isSimulation == true, let bridge {
            stopping = true
            status("Arrêt demandé…", "Repère attend que le moteur ait envoyé la commande d’arrêt.")
            log("Arrêt envoyé à la session en cours.")
            do { try bridge.stop() }
            catch {
                stopping = false
                status("L’arrêt n’a pas pu être envoyé", error.localizedDescription, error: true)
                log(error.localizedDescription)
            }
        } else if let id = activeID ?? (selectedID.isEmpty ? nil : selectedID) {
            activeID = id
            restorationNeeded = true
            status("Restauration demandée…", "Connexion à l’iPhone pour effacer la position simulée.")
            run(.clear, udid: id)
        }
    }

    private var resources: URL {
        if let path = ProcessInfo.processInfo.environment["REPERE_RESOURCES"], !path.isEmpty {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return Bundle.main.resourceURL ?? Bundle.main.bundleURL
    }

    private func commandArguments(_ action: Operation, udid: String? = nil, coordinate: CLLocationCoordinate2D? = nil) -> [String] {
        var arguments = [action.rawValue, "--connection", connectionType]
        if let udid { arguments += ["--udid", udid] }
        if let coordinate {
            if action == .random {
                arguments += ["--latitude=\(coordinate.latitude)", "--longitude=\(coordinate.longitude)"]
            } else { arguments += ["--lat=\(coordinate.latitude)", "--lon=\(coordinate.longitude)"] }
        }
        if action == .random, let radius = randomRadius {
            arguments += ["--radius=\(radius)", "--speed=\(randomSpeed)"]
        }
        if action == .route, let file = routeFile {
            arguments += ["--route-file", file.path]
            if routeTiming == .duration, let seconds = requestedDuration { arguments += ["--duration-seconds", String(seconds)] }
            else { arguments += ["--speed-kmh", String(routeSpeed)] }
        }
        return arguments
    }

    private func run(_ action: Operation, udid: String? = nil, coordinate: CLLocationCoordinate2D? = nil) {
        let id = UUID()
        commandID = id
        operation = action
        operationReceivedSuccess = false
        operationReceivedError = false
        let arguments = commandArguments(action, udid: udid, coordinate: coordinate)
        do {
            bridge = try BridgeProcess(resources: resources, arguments: arguments,
                event: { [weak self] event in
                    guard let self, self.commandID == id else { return }
                    self.receive(event, action: action, udid: udid)
                }, finished: { [weak self] code, diagnostic in
                    guard let self, self.commandID == id else { return }
                    self.completed(action, code: code, diagnostic: diagnostic)
                })
            if action.isSimulation {
                sessionActivity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled], reason: "Maintenir la session de localisation Repère")
            }
        } catch {
            operation = nil
            commandID = nil
            bridge = nil
            // No subprocess was started, so this attempt cannot have changed GPS.
            if action.isSimulation { activeID = nil; restorationNeeded = false; removeRouteFile() }
            status("Impossible de lancer la connexion", error.localizedDescription, error: true)
            log(error.localizedDescription)
            if quitRequested { DispatchQueue.main.async { [weak self] in self?.resolveQuit() } }
        }
    }

    private func receive(_ event: BridgeEvent, action: Operation, udid: String?) {
        switch event.kind {
        case "devices" where action == .devices:
            operationReceivedSuccess = true
            devices = event.devices ?? []
            if !devices.contains(where: { $0.id == selectedID }) { selectedID = devices.first?.id ?? "" }
            if devices.isEmpty {
                status("Aucun iPhone détecté", connectionType == "Network" ? "Activez l’affichage Wi-Fi de l’iPhone dans Finder, puis débranchez le câble et actualisez. Voir le guide." : "Branchez-le en USB, déverrouillez-le et acceptez « Faire confiance ».")
                log("Aucun appareil disponible. Consultez le guide de connexion.")
            } else {
                status("iPhone détecté", "Choisissez un lieu, préparez la connexion, puis appliquez la position.")
                log("\(devices.count) appareil\(devices.count > 1 ? "s" : "") détecté\(devices.count > 1 ? "s" : "").")
            }
        case "prepared" where action == .prepare:
            operationReceivedSuccess = true
            if let udid { preparedIDs.insert(udid + ":" + connectionType) }
            status("Connexion préparée", "Vous pouvez maintenant demander une simulation de position.")
            log(event.message ?? "Préparation terminée.")
        case "ready" where action.isSimulation:
            simulationReady = true
            if !stopping {
                let title = action == .route ? "Trajet en cours" : action == .random ? "Déplacement aléatoire en cours" : "Simulation demandée"
                status(title, "Gardez Repère ouvert et la connexion \(connectionLabel) active. Résultat à vérifier dans les apps.")
            }
            log(event.message ?? "Le service a accepté la demande de simulation.")
        case let kind where action == .random && (kind == "random_started" || kind == "random_progress"):
            if let lat = event.latitude, let lon = event.longitude {
                let point = RoutePoint(latitude: lat, longitude: lon)
                if point.valid { traveler = point }
            }
            if let traveled = event.distance_m, traveled.isFinite, traveled >= 0 { randomTraveled = max(randomTraveled, traveled) }
            if let paused = event.paused {
                routePaused = paused
                if requestedPause == paused { routePausePending = false; requestedPause = nil }
            }
            if simulationReady && !stopping {
                status(routePaused ? "Déplacement aléatoire en pause" : "Déplacement aléatoire en cours",
                       "\(String(format: "%.0f", randomTraveled)) m parcourus · La session continue dans le cercle jusqu’à votre arrêt.")
            }
            if event.kind == "random_started", let message = event.message { log(message) }
        case "route_progress" where action == .route:
            if let lat = event.latitude, let lon = event.longitude {
                let point = RoutePoint(latitude: lat, longitude: lon)
                if point.valid { traveler = point }
            }
            if let total = event.total_m, total.isFinite, total > 0 { routeDistance = total }
            if let traveled = event.distance_m, traveled.isFinite { routeTraveled = min(routeDistance, max(0, traveled)) }
            if let paused = event.paused {
                routePaused = paused
                if requestedPause == paused { routePausePending = false; requestedPause = nil }
            }
            if !stopping && !routeFinished { status(routePaused ? "Trajet en pause" : "Trajet en cours", "\(Int(routeFraction * 100)) % parcouru · \(routeDuration) de déplacement restant. Les pauses prolongent la durée totale.") }
        case "route_finished" where action == .route:
            routeFinished = true
            routePausePending = false
            requestedPause = nil
            routeTraveled = routeDistance
            if !stopping { status("Arrivée au point B", "La position B reste simulée jusqu’à « Arrêter et restaurer le GPS ».") }
            log(event.message ?? "Arrivée au point B.")
        case "unchanged" where action.isSimulation:
            // The bridge guarantees set was never attempted. The following
            // error must leave preparation and discovery available for repair.
            restorationNeeded = false
            activeID = nil
            simulationReady = false
            traveler = nil
            randomTraveled = 0
            routePaused = false
            routePausePending = false
            requestedPause = nil
            operationReceivedSuccess = true
            if let udid { preparedIDs.remove(udid + ":" + connectionType) }
            status("Aucune position envoyée", event.message ?? "La simulation n’a pas démarré.")
            log(event.message ?? "Aucune position n’a été envoyée. Préparez à nouveau la connexion.")
        case "cleared" where action.isSimulation || action == .clear:
            operationReceivedSuccess = true
            restorationNeeded = false
            activeID = nil
            simulationReady = false
            routePaused = false
            routePausePending = false
            requestedPause = nil
            traveler = nil
            routeTraveled = 0
            randomTraveled = 0
            routeFinished = false
            status("Demande d’arrêt envoyée", "Repère a envoyé la commande d’arrêt. Vérifiez la position affichée sur l’iPhone.")
            log(event.message ?? "Commande d’arrêt envoyée au service de localisation.")
        case "error":
            operationReceivedError = true
            let message = event.message ?? "Le service a rencontré une erreur."
            status("La connexion a rencontré un problème", message, error: true)
            log(message)
        case "log":
            if let message = event.message { log(message) }
        default:
            if let message = event.message { log(message) }
        }
    }

    private func completed(_ action: Operation, code: Int32, diagnostic: String) {
        if let activity = sessionActivity { ProcessInfo.processInfo.endActivity(activity); sessionActivity = nil }
        operation = nil
        commandID = nil
        bridge = nil
        stopping = false
        routePausePending = false
        requestedPause = nil
        if action == .route { removeRouteFile() }
        if code != 0 && !operationReceivedError {
            let message = diagnostic.isEmpty ? "Le moteur s’est arrêté avec le code \(code)." : String(diagnostic.suffix(1200))
            status("L’opération a échoué", message, error: true)
            log(message)
        } else if !operationReceivedSuccess && !operationReceivedError {
            let message = action.isSimulation ? "La session s’est terminée sans confirmation de l’envoi de l’arrêt." : "Le moteur s’est arrêté sans confirmer le résultat."
            status("Résultat non confirmé", message, error: true)
            log(message)
        }
        if restorationNeeded && (action.isSimulation || action == .clear) {
            simulationReady = false
            statusTitle = "Restauration non confirmée"
            statusIsError = true
            statusDetail += " Utilisez « Restaurer le GPS » avant de changer d’iPhone."
        }
        if quitRequested { resolveQuit() }
    }

    private func status(_ title: String, _ detail: String, error: Bool = false) {
        statusTitle = title
        statusDetail = detail
        statusIsError = error
    }

    private func log(_ message: String) {
        journal.append(JournalEntry(message: message))
        if journal.count > 60 { journal.removeFirst(journal.count - 60) }
    }

    func requestQuit() -> NSApplication.TerminateReply {
        if !restorationNeeded && !isBusy { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = restorationNeeded ? "Arrêter la simulation avant de quitter ?" : "Une opération est encore en cours"
        alert.informativeText = restorationNeeded
            ? "Repère va demander l’effacement de la position simulée sur \(activePhoneName). Gardez l’iPhone connecté."
            : "Repère peut attendre la fin de cette opération, puis quitter."
        alert.addButton(withTitle: restorationNeeded ? "Arrêter et quitter" : "Attendre puis quitter")
        alert.addButton(withTitle: "Annuler")
        guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
        quitRequested = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if self.restorationNeeded { self.restore() }
            else if !self.isBusy { self.resolveQuit() }
        }
        scheduleQuitCheck()
        return .terminateLater
    }

    private func scheduleQuitCheck() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self] in
            guard let self, self.quitRequested, self.isBusy else { return }
            let alert = NSAlert()
            alert.messageText = self.operation == .prepare ? "La préparation est encore en cours" : "L’arrêt prend plus de temps que prévu"
            alert.informativeText = self.operation == .prepare
                ? "Le téléchargement des composants développeur peut prendre plusieurs minutes. Repère quittera après cette opération."
                : "L’envoi de la commande d’arrêt n’est pas encore confirmé. Vous pouvez garder Repère ouvert pour suivre l’opération."
            alert.addButton(withTitle: "Continuer à attendre")
            alert.addButton(withTitle: "Rester dans Repère")
            alert.addButton(withTitle: "Quitter quand même")
            switch alert.runModal() {
            case .alertFirstButtonReturn: self.scheduleQuitCheck()
            case .alertSecondButtonReturn:
                self.quitRequested = false
                NSApplication.shared.reply(toApplicationShouldTerminate: false)
            default: NSApplication.shared.reply(toApplicationShouldTerminate: true)
            }
        }
    }

    private func resolveQuit() {
        guard quitRequested else { return }
        if !restorationNeeded {
            NSApplication.shared.reply(toApplicationShouldTerminate: true)
            return
        }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "La restauration n’est pas confirmée"
        alert.informativeText = "L’iPhone peut encore utiliser la position simulée. Rebranchez-le et réessayez, ou redémarrez l’iPhone avant de vérifier sa position."
        alert.addButton(withTitle: "Réessayer la restauration")
        alert.addButton(withTitle: "Rester dans Repère")
        alert.addButton(withTitle: "Quitter quand même")
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            restore()
            scheduleQuitCheck()
        case .alertSecondButtonReturn:
            quitRequested = false
            NSApplication.shared.reply(toApplicationShouldTerminate: false)
        default: NSApplication.shared.reply(toApplicationShouldTerminate: true)
        }
    }
}

struct LocationMap: NSViewRepresentable {
    let coordinate: CLLocationCoordinate2D?
    let centerRequest: Int
    let routePoints: [RoutePoint]
    let routeStart: RoutePoint?
    let routeEnd: RoutePoint?
    let randomCenter: RoutePoint?
    let randomRadius: Double?
    let traveler: RoutePoint?
    let focus: RoutePoint?
    let routeCenterRequest: Int
    var choose: (CLLocationCoordinate2D) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.userContentController.add(context.coordinator, name: "repere")
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 800, height: 700), configuration: config)
        view.customUserAgent = "Repere/1.0 (personal macOS application; WebKit)"
        view.navigationDelegate = context.coordinator
        context.coordinator.webView = view
        let resources = ProcessInfo.processInfo.environment["REPERE_RESOURCES"].map { URL(fileURLWithPath: $0) } ?? Bundle.main.resourceURL!
        let folder = resources.appendingPathComponent("Map")
        view.loadFileURL(folder.appendingPathComponent("index.html"), allowingReadAccessTo: folder)
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {
        context.coordinator.parent = self
        context.coordinator.sync()
    }
    static func dismantleNSView(_ view: WKWebView, coordinator: Coordinator) {
        view.configuration.userContentController.removeScriptMessageHandler(forName: "repere")
        view.navigationDelegate = nil
    }
    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        var parent: LocationMap
        weak var webView: WKWebView?
        var loaded = false
        var lastKey = ""
        var lastCenter = 0
        var lastRouteCenter = 0
        var lastRoute: [RoutePoint]?
        var lastEndpoints = ""
        var lastTraveler = ""
        var lastRandomArea = ""
        init(_ parent: LocationMap) { self.parent = parent }
        func sync() {
            guard loaded, let view = webView else { return }
            let key = "\(parent.coordinate?.latitude.description ?? "nil"),\(parent.coordinate?.longitude.description ?? "nil"),\(parent.centerRequest)"
            let center = lastCenter != parent.centerRequest
            lastCenter = parent.centerRequest
            if key != lastKey {
                lastKey = key
                if let c = parent.coordinate {
                    view.evaluateJavaScript("window.repereSet(\(c.latitude),\(c.longitude),\(center ? "true" : "false"))", completionHandler: nil)
                } else {
                    view.evaluateJavaScript("window.repereSet(null,null,false)", completionHandler: nil)
                    if center, let focus = parent.focus {
                        view.evaluateJavaScript("window.repereCenter(\(focus.latitude),\(focus.longitude))", completionHandler: nil)
                    }
                }
            }
            let centerRoute = lastRouteCenter != parent.routeCenterRequest
            lastRouteCenter = parent.routeCenterRequest
            if lastRoute != parent.routePoints || centerRoute {
                lastRoute = parent.routePoints
                if let data = try? JSONEncoder().encode(parent.routePoints.map(\.pair)), let json = String(data: data, encoding: .utf8) {
                    view.evaluateJavaScript("window.repereRoute(\(json),\(centerRoute ? "true" : "false"))", completionHandler: nil)
                    lastEndpoints = ""
                }
            }
            func pair(_ point: RoutePoint?) -> String {
                guard let point else { return "null" }
                return "[\(point.latitude),\(point.longitude)]"
            }
            let randomArea = "\(pair(parent.randomCenter)),\(parent.randomRadius ?? 0)"
            if lastRandomArea != randomArea || center {
                let fitArea = parent.randomCenter != nil && (lastRandomArea != randomArea || center)
                lastRandomArea = randomArea
                view.evaluateJavaScript("window.repereSetRandomArea(\(randomArea),\(fitArea ? "true" : "false"))", completionHandler: nil)
            }
            let endpoints = "\(pair(parent.routeStart)),\(pair(parent.routeEnd))"
            if lastEndpoints != endpoints {
                lastEndpoints = endpoints
                view.evaluateJavaScript("window.repereRouteEndpoints(\(endpoints))", completionHandler: nil)
            }
            let traveler = parent.traveler.map { "\($0.latitude),\($0.longitude)" } ?? "null,null"
            if lastTraveler != traveler {
                lastTraveler = traveler
                view.evaluateJavaScript("window.repereTraveler(\(traveler))", completionHandler: nil)
            }
        }
        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.frameInfo.isMainFrame, message.frameInfo.request.url?.isFileURL == true,
                  let body = message.body as? [String: Any] else { return }
            if body["kind"] as? String == "ready" { loaded = true; sync(); return }
            guard let lat = body["lat"] as? Double, let lon = body["lon"] as? Double,
                  lat.isFinite, lon.isFinite, (-90...90).contains(lat), (-180...180).contains(lon) else { return }
            parent.choose(CLLocationCoordinate2D(latitude: lat, longitude: lon))
        }
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
            if url.isFileURL { decisionHandler(.allow); return }
            if navigationAction.navigationType == .linkActivated, url.scheme == "https",
               ["www.openstreetmap.org", "leafletjs.com"].contains(url.host ?? "") { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }
}

struct RepereButtonStyle: ButtonStyle {
    var primary = false
    var compact = false
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: compact ? 11 : 12, weight: .semibold))
            .foregroundStyle(primary ? Color.white : ink)
            .padding(.horizontal, compact ? 11 : 15).padding(.vertical, compact ? 8 : 11)
            .background(primary ? accent : raised, in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(primary ? Color.white.opacity(0.10) : outline))
            .brightness(configuration.isPressed ? -0.06 : 0)
            .opacity(enabled ? 1 : 0.38)
    }
}

struct RepereView: View {
    @ObservedObject var model: RepereModel
    @State private var showJournal = false
    @FocusState private var searchFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(outline)
            toolbar
            Divider().overlay(outline)
            HStack(alignment: .top, spacing: 16) {
                sidebar.frame(width: 320)
                mapArea
                    .clipShape(RoundedRectangle(cornerRadius: 18))
                    .overlay(RoundedRectangle(cornerRadius: 18).stroke(outline))
            }.padding(16).frame(maxWidth: .infinity, maxHeight: .infinity)
            if showJournal { journalPanel }
            footer
        }
        .background(canvas).foregroundStyle(ink).tint(accent)
        .frame(minWidth: 1080, minHeight: 740)
        .sheet(isPresented: $model.showGuide) { ConnectionGuide().frame(width: 600, height: 700) }
    }

    private var header: some View {
        HStack(spacing: 16) {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14)
                        .fill(LinearGradient(colors: [accent, Color(red: 0.14, green: 0.30, blue: 0.84)], startPoint: .topLeading, endPoint: .bottomTrailing))
                        .frame(width: 46, height: 46)
                    Image(systemName: "location.north.line.fill").font(.system(size: 24, weight: .semibold)).foregroundStyle(.white)
                }
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text("Repère").font(.system(size: 24, weight: .bold, design: .rounded))
                        Text("v1.3").font(.system(size: 10, weight: .bold, design: .monospaced))
                            .foregroundStyle(Color(red: 0.38, green: 0.68, blue: 1))
                            .padding(.horizontal, 7).padding(.vertical, 4).background(accent.opacity(0.13), in: Capsule())
                    }
                    Text(model.preview ? "Aperçu de l’interface" : "Localisation & trajets")
                        .font(.system(size: 11)).foregroundStyle(muted)
                }
            }
            Spacer(minLength: 12)
            Picker("Type de déplacement", selection: $model.destinationMode) {
                ForEach(DestinationMode.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }.pickerStyle(.segmented).labelsHidden().frame(width: 380).disabled(!model.canEditTarget)
            Spacer(minLength: 12)
            devicePill
            Button { model.showGuide = true } label: { Image(systemName: "questionmark.circle").font(.system(size: 19)) }
                .buttonStyle(.plain).foregroundStyle(muted).help("Guide de connexion")
        }.padding(.horizontal, 24).padding(.vertical, 18).background(surface.opacity(0.65))
    }

    private var devicePill: some View {
        HStack(spacing: 9) {
            Circle().fill(model.devices.isEmpty ? muted.opacity(0.6) : Color.green).frame(width: 7, height: 7)
            Image(systemName: "iphone").font(.system(size: 19)).foregroundStyle(muted)
            VStack(alignment: .leading, spacing: 2) {
                if model.devices.isEmpty {
                    Text("Aucun iPhone").font(.system(size: 12, weight: .medium))
                } else {
                    Picker("iPhone", selection: $model.selectedID) {
                        ForEach(model.devices) { Text($0.name).tag($0.id) }
                    }.labelsHidden().frame(maxWidth: 170).disabled(model.isBusy || model.restorationNeeded)
                }
                Text(model.selectedPhone.map { "iOS \($0.version) · \(model.connectionLabel)" } ?? "Connecter un appareil")
                    .font(.system(size: 10)).foregroundStyle(muted)
            }.frame(minWidth: 115, alignment: .leading)
            Button { model.scan() } label: { Image(systemName: "arrow.clockwise").font(.system(size: 13, weight: .medium)) }
                .buttonStyle(.plain).padding(4)
                .disabled(model.preview || model.isBusy || model.restorationNeeded)
                .accessibilityLabel("Actualiser").help("Rechercher les appareils connectés")
        }.padding(.horizontal, 13).padding(.vertical, 9).background(raised.opacity(0.7), in: Capsule())
    }

    private var toolbar: some View {
        HStack(spacing: 16) {
            Button { model.prepare() } label: {
                HStack(spacing: 7) {
                    if model.operation == .prepare { ProgressView().controlSize(.mini) }
                    else { Image(systemName: model.prepared ? "checkmark.circle.fill" : "link") }
                    Text(model.operation == .prepare ? "Préparation…" : model.prepared ? "Connexion préparée" : "Préparer la connexion")
                }
            }.buttonStyle(RepereButtonStyle(primary: !model.prepared, compact: true))
                .disabled(model.preview || model.isBusy || model.restorationNeeded || model.selectedID.isEmpty)
            Rectangle().fill(outline).frame(width: 1, height: 22)
            HStack(spacing: 8) {
                Text("Connexion").font(.system(size: 11)).foregroundStyle(muted)
                Picker("Connexion", selection: $model.connectionType) {
                    Text("USB").tag("USB")
                    Text("Wi-Fi").tag("Network")
                }.pickerStyle(.segmented).labelsHidden().frame(width: 120).disabled(model.preview || model.isBusy)
                    .onChange(of: model.connectionType) { _, _ in model.changeConnection() }
            }
            if model.connectionType == "Network" {
                Button { model.showGuide = true } label: { Label("Configurer le Wi-Fi", systemImage: "info.circle") }
                    .buttonStyle(.plain).font(.system(size: 11)).foregroundStyle(muted)
            }
            Spacer()
            Label(model.restorationNeeded ? "Session protégée" : model.prepared ? "Prêt à démarrer" : "En attente de connexion",
                  systemImage: model.restorationNeeded ? "lock.fill" : model.prepared ? "checkmark.circle" : "circle.dotted")
                .font(.system(size: 11)).foregroundStyle(model.prepared ? Color.green : muted)
        }.padding(.horizontal, 24).padding(.vertical, 11).background(surface)
    }

    private var sidebar: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                destinationCard
                if model.destinationMode == .route { timingCard }
                if model.destinationMode == .random { randomSettingsCard }
                if model.destinationMode == .route && !model.routePoints.isEmpty { routeSummary }
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "info.circle").padding(.top, 1)
                    Text("La simulation s’applique à l’iPhone. Snapchat et Localiser ne peuvent pas être ciblés séparément.")
                        .fixedSize(horizontal: false, vertical: true)
                }.font(.system(size: 10)).foregroundStyle(muted).lineSpacing(3).padding(.horizontal, 6)
            }.padding(.bottom, 6)
        }.scrollIndicators(.automatic)
    }

    private func card<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 15, content: content)
            .padding(17).frame(maxWidth: .infinity, alignment: .leading)
            .background(surface, in: RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(outline))
    }

    private func cardTitle(_ title: String, icon: String, detail: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon).font(.system(size: 14, weight: .semibold)).foregroundStyle(accent)
                .frame(width: 30, height: 30).background(accent.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 14, weight: .semibold))
                Text(detail).font(.system(size: 10)).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var destinationCard: some View {
        card {
            cardTitle(model.destinationMode == .route ? "Votre itinéraire" : model.destinationMode == .random ? "Centre du cercle" : "Votre destination",
                      icon: model.destinationMode == .route ? "point.topleft.down.to.point.bottomright.curvepath" : model.destinationMode == .random ? "circle.dashed" : "mappin.and.ellipse",
                      detail: model.destinationMode == .route ? "Placez A puis B sur la carte." : model.destinationMode == .random ? "Placez le centre de votre zone sur la carte." : "Recherchez un lieu ou cliquez sur la carte.")
            if model.destinationMode == .route { routeSection }
            else { fixedTargetSection }
        }
    }

    private var fixedTargetSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(model.placeName).font(.system(size: 13, weight: .medium)).lineLimit(3)
            HStack(spacing: 10) {
                coordinateField("Latitude", placeholder: "48,856600", binding: $model.latitude)
                coordinateField("Longitude", placeholder: "2,352200", binding: $model.longitude)
            }
            if let error = model.coordinateError {
                Text(error).font(.system(size: 10)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            Button { model.centerRequest += 1 } label: {
                Label(model.destinationMode == .random ? "Voir le cercle" : "Centrer sur le point", systemImage: "scope").frame(maxWidth: .infinity)
            }.buttonStyle(RepereButtonStyle(compact: true)).disabled(model.coordinate == nil)
        }
    }

    private func routeEndpointEditor(_ endpoint: RouteEndpoint, title: String, latitude: Binding<String>, longitude: Binding<String>) -> some View {
        let selected = model.routeEndpoint == endpoint
        let color = endpoint == .start ? accent : Color(red: 0.48, green: 0.42, blue: 0.98)
        return VStack(alignment: .leading, spacing: 9) {
            Button { model.routeEndpoint = endpoint } label: {
                HStack(spacing: 8) {
                    Text(endpoint.rawValue).font(.system(size: 10, weight: .bold))
                        .foregroundStyle(.white).frame(width: 22, height: 22).background(color, in: Circle())
                    Text(title).font(.system(size: 12, weight: .semibold))
                    Spacer()
                    if selected { Text("Sur la carte").font(.system(size: 9, weight: .medium)).foregroundStyle(color) }
                    Image(systemName: selected ? "cursorarrow.click" : "mappin").font(.system(size: 11)).foregroundStyle(color)
                }.contentShape(Rectangle())
            }.buttonStyle(.plain).disabled(!model.canEditTarget)
            HStack(spacing: 8) {
                coordinateField("Latitude \(endpoint.rawValue)", placeholder: "48,856600", binding: latitude)
                coordinateField("Longitude \(endpoint.rawValue)", placeholder: "2,352200", binding: longitude)
            }
        }.padding(11)
            .background(selected ? color.opacity(0.07) : canvas.opacity(0.45), in: RoundedRectangle(cornerRadius: 11))
            .overlay(RoundedRectangle(cornerRadius: 11).stroke(selected ? color.opacity(0.5) : outline))
    }

    private var routeSection: some View {
        VStack(alignment: .leading, spacing: 11) {
            routeEndpointEditor(.start, title: "Départ", latitude: $model.startLatitude, longitude: $model.startLongitude)
            routeEndpointEditor(.end, title: "Arrivée", latitude: $model.endLatitude, longitude: $model.endLongitude)
            Button { model.swapRoute() } label: { Label("Inverser le trajet", systemImage: "arrow.up.arrow.down") }
                .font(.system(size: 10, weight: .medium)).buttonStyle(.plain).foregroundStyle(muted).disabled(!model.canEditTarget)
            Picker("Tracé", selection: $model.routeKind) {
                ForEach(RouteKind.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }.pickerStyle(.segmented).labelsHidden().disabled(!model.canEditTarget)
            Button { model.calculateRoute() } label: {
                HStack(spacing: 8) {
                    if model.routeCalculating { ProgressView().controlSize(.mini) }
                    else { Image(systemName: "point.topleft.down.to.point.bottomright.curvepath") }
                    Text(model.routeCalculating ? "Calcul du trajet…" : "Tracer le trajet")
                    Spacer()
                    Image(systemName: "arrow.right")
                }
            }.buttonStyle(RepereButtonStyle(compact: true)).disabled(!model.canCalculateRoute)
            Text(model.routeMessage).font(.system(size: 10)).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true)
            if model.routeKind == .road {
                Text("Itinéraire voiture · OSRM / OpenStreetMap. Le calcul transmet les points A et B à OSRM.")
                    .font(.system(size: 9)).foregroundStyle(muted.opacity(0.85)).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var timingCard: some View {
        card {
            cardTitle("Rythme du trajet", icon: "clock", detail: "Choisissez la durée ou la vitesse.")
            Picker("Régler le trajet par", selection: $model.routeTiming) {
                ForEach(RouteTiming.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }.pickerStyle(.segmented).labelsHidden().disabled(!model.canEditTarget)
            if model.routeTiming == .duration {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    TextField("20", text: $model.routeMinutes).textFieldStyle(.plain)
                        .font(.system(size: 29, weight: .medium, design: .rounded))
                        .accessibilityLabel("Durée souhaitée en minutes").disabled(!model.canEditTarget)
                    Text("minutes").font(.system(size: 12)).foregroundStyle(muted)
                }.padding(13).background(canvas.opacity(0.75), in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(outline))
            } else {
                HStack(alignment: .firstTextBaseline) {
                    Text("\(Int(model.routeSpeed))").font(.system(size: 29, weight: .medium, design: .rounded))
                    Text("km/h").font(.system(size: 12)).foregroundStyle(muted)
                    Spacer()
                }
                Slider(value: $model.routeSpeed, in: 1...150, step: 1).disabled(!model.canEditTarget).accessibilityLabel("Vitesse du trajet")
            }
            if !model.routePoints.isEmpty, let error = model.routeTimingError {
                Text(error).font(.system(size: 10)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            Text("Les pauses s’ajoutent à la durée. À l’arrivée, le point B est maintenu jusqu’à l’arrêt.")
                .font(.system(size: 10)).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var randomSettingsCard: some View {
        card {
            cardTitle("Déplacement aléatoire", icon: "shuffle", detail: "Une promenade continue dans le cercle.")
            VStack(alignment: .leading, spacing: 7) {
                Text("RAYON DU CERCLE").font(.system(size: 9, weight: .bold)).tracking(0.7).foregroundStyle(muted)
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    TextField("200", text: $model.randomRadiusText).textFieldStyle(.plain)
                        .font(.system(size: 29, weight: .medium, design: .rounded))
                        .accessibilityLabel("Rayon du cercle en mètres").disabled(!model.canEditTarget)
                    Text("mètres").font(.system(size: 12)).foregroundStyle(muted)
                }.padding(13).background(canvas.opacity(0.75), in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(outline))
                Text("De 10 à 10 000 m autour du centre.").font(.system(size: 10)).foregroundStyle(muted)
            }
            VStack(alignment: .leading, spacing: 9) {
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    Text("Vitesse").font(.system(size: 11, weight: .medium)).foregroundStyle(muted)
                    Spacer()
                    Text(String(format: "%.0f", model.randomSpeed)).font(.system(size: 24, weight: .medium, design: .rounded)).monospacedDigit()
                    Text("km/h").font(.system(size: 11)).foregroundStyle(muted)
                }
                Slider(value: $model.randomSpeed, in: 1...150, step: 1)
                    .disabled(!model.canEditTarget).accessibilityLabel("Vitesse du déplacement aléatoire")
            }
            if let error = model.randomValidationError {
                Text(error).font(.system(size: 10)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            Text("Déplacement libre : les routes et les obstacles ne sont pas suivis. La session se poursuit jusqu’à l’arrêt ; vous pouvez la mettre en pause.")
                .font(.system(size: 10)).foregroundStyle(muted).lineSpacing(2).fixedSize(horizontal: false, vertical: true)
            if model.operation == .random || model.randomTraveled > 0 {
                Divider().overlay(outline)
                HStack {
                    Text("Distance parcourue").font(.system(size: 11)).foregroundStyle(muted)
                    Spacer()
                    Text("\(String(format: "%.0f", model.randomTraveled)) m").font(.system(size: 13, weight: .semibold)).monospacedDigit()
                }
            }
        }
    }

    private var routeSummary: some View {
        card {
            HStack {
                Label("TRAJET PRÊT", systemImage: "checkmark.circle.fill")
                    .font(.system(size: 9, weight: .bold)).tracking(0.8).foregroundStyle(accent)
                Spacer()
                Button { model.routeCenterRequest += 1 } label: { Image(systemName: "arrow.up.left.and.arrow.down.right") }
                    .buttonStyle(.plain).foregroundStyle(muted).help("Voir tout le trajet")
            }
            HStack(alignment: .firstTextBaseline, spacing: 0) {
                metric(String(format: "%.2f", model.routeDistance / 1000), label: "kilomètres")
                Spacer()
                metric(model.routeDuration, label: model.operation == .route ? "restantes" : "durée")
            }
            if model.routeTimingError == nil {
                Text("\(String(format: "%.1f", model.effectiveSpeed)) km/h · vitesse \(model.routeTiming == .duration ? "calculée" : "choisie")")
                    .font(.system(size: 10)).foregroundStyle(muted)
            }
        }
    }

    private func metric(_ value: String, label: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value).font(.system(size: 23, weight: .semibold, design: .rounded)).monospacedDigit()
            Text(label).font(.system(size: 10)).foregroundStyle(muted)
        }
    }

    private func coordinateField(_ title: String, placeholder: String, binding: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(size: 9, weight: .medium)).foregroundStyle(muted)
            TextField(placeholder, text: binding).textFieldStyle(.plain)
                .font(.system(size: 11, design: .monospaced)).padding(.horizontal, 8).padding(.vertical, 9)
                .background(canvas.opacity(0.8), in: RoundedRectangle(cornerRadius: 7))
                .overlay(RoundedRectangle(cornerRadius: 7).stroke(outline))
                .accessibilityLabel(title).disabled(!model.canEditTarget)
        }
    }

    private var mapArea: some View {
        ZStack(alignment: .top) {
            LocationMap(coordinate: model.destinationMode == .fixed ? model.coordinate : nil,
                        centerRequest: model.centerRequest,
                        routePoints: model.destinationMode == .route ? model.routePoints : [],
                        routeStart: model.destinationMode == .route ? (model.routePoints.first ?? model.routeStart) : nil,
                        routeEnd: model.destinationMode == .route ? (model.routePoints.last ?? model.routeEnd) : nil,
                        randomCenter: model.destinationMode == .random && model.randomRadius != nil ? model.randomCenter : nil,
                        randomRadius: model.destinationMode == .random ? model.randomRadius : nil,
                        traveler: model.traveler, focus: model.mapFocus,
                        routeCenterRequest: model.routeCenterRequest) { model.choose($0) }
            searchPanel.padding(18).padding(.trailing, 10)
            VStack {
                Spacer()
                HStack {
                    Label(model.canEditTarget ? (model.destinationMode == .route ? "Choisissez le point \(model.routeEndpoint.rawValue)" : model.destinationMode == .random ? "Cliquez pour placer le centre du cercle" : "Cliquez pour placer un repère") : "Session en cours · point verrouillé", systemImage: model.canEditTarget ? "cursorarrow.click" : "lock.fill")
                        .font(.system(size: 11, weight: .medium)).foregroundStyle(ink)
                        .padding(.horizontal, 13).padding(.vertical, 10)
                        .background(surface.opacity(0.96), in: Capsule())
                        .overlay(Capsule().stroke(outline))
                    Spacer()
                }.padding(18).padding(.bottom, 15)
            }.allowsHitTesting(false)
        }
    }

    private var searchPanel: some View {
        VStack(spacing: 0) {
            HStack(spacing: 11) {
                Image(systemName: "magnifyingglass").font(.system(size: 14, weight: .medium)).foregroundStyle(muted)
                TextField(model.destinationMode == .route ? "Rechercher le point \(model.routeEndpoint.rawValue)…" : "Rechercher une adresse, une ville…", text: $model.searchText)
                    .textFieldStyle(.plain).font(.system(size: 13)).focused($searchFocused).onSubmit { model.findPlace() }
                    .accessibilityLabel("Rechercher un lieu")
                if model.searching { ProgressView().controlSize(.small) }
                else {
                    Button { model.findPlace() } label: {
                        Image(systemName: "arrow.right").font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(.white).frame(width: 28, height: 28).background(accent, in: RoundedRectangle(cornerRadius: 7))
                    }.buttonStyle(.plain).help("Rechercher")
                }
            }.padding(12)
            if !model.searchResults.isEmpty {
                Divider().overlay(outline)
                ForEach(Array(model.searchResults.enumerated()), id: \.offset) { _, item in
                    Button { model.choose(item.coordinate, name: item.name, center: true) } label: {
                        HStack(spacing: 10) {
                            Image(systemName: "mappin.circle.fill").foregroundStyle(accent).font(.system(size: 19))
                            VStack(alignment: .leading, spacing: 3) {
                                Text(item.name).font(.system(size: 12, weight: .medium)).foregroundStyle(ink)
                                Text(item.detail).font(.system(size: 10)).foregroundStyle(muted).lineLimit(1)
                            }
                            Spacer()
                            Image(systemName: "arrow.up.left").font(.system(size: 10)).foregroundStyle(muted)
                        }.padding(.horizontal, 14).padding(.vertical, 11).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                }
            }
            if !model.searchMessage.isEmpty {
                Text(model.searchMessage).font(.system(size: 11)).foregroundStyle(muted)
                    .padding(.horizontal, 14).padding(.bottom, 12).frame(maxWidth: .infinity, alignment: .leading)
            }
        }.disabled(!model.canEditTarget)
            .background(surface.opacity(0.97), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.12)))
            .shadow(color: .black.opacity(0.3), radius: 16, y: 6)
    }

    private var journalPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Journal d’activité", systemImage: "terminal").font(.system(size: 11, weight: .semibold)).foregroundStyle(muted)
                Spacer()
                Text("\(model.journal.count) évènement\(model.journal.count > 1 ? "s" : "")").font(.system(size: 10)).foregroundStyle(muted)
                Button { showJournal = false } label: { Image(systemName: "xmark").font(.system(size: 10, weight: .semibold)) }
                    .buttonStyle(.plain).foregroundStyle(muted).help("Masquer le journal")
            }
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        if model.journal.isEmpty { Text("Les étapes de connexion apparaîtront ici.").foregroundStyle(muted) }
                        ForEach(model.journal) { entry in
                            HStack(alignment: .top, spacing: 12) {
                                Text(entry.date, style: .time).foregroundStyle(muted).frame(width: 56, alignment: .leading)
                                Text(entry.message).foregroundStyle(ink.opacity(0.85)).textSelection(.enabled)
                            }.id(entry.id)
                        }
                    }.font(.system(size: 10, design: .monospaced)).frame(maxWidth: .infinity, alignment: .leading)
                }.frame(height: 85)
                    .onAppear { if let last = model.journal.last { proxy.scrollTo(last.id, anchor: .bottom) } }
                    .onChange(of: model.journal.count) { _, _ in if let last = model.journal.last { proxy.scrollTo(last.id, anchor: .bottom) } }
            }
        }.padding(.horizontal, 24).padding(.vertical, 14).background(canvas)
            .overlay(alignment: .top) { Rectangle().fill(outline).frame(height: 1) }
    }

    private var footer: some View {
        VStack(spacing: 0) {
            if model.operation == .route { ProgressView(value: model.routeFraction).tint(accent).progressViewStyle(.linear) }
            else { Rectangle().fill(outline).frame(height: 1) }
            HStack(spacing: 11) {
                Circle().fill(model.statusIsError ? Color.orange : model.simulationReady ? Color.green : muted.opacity(0.5)).frame(width: 7, height: 7)
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.statusTitle).font(.system(size: 12, weight: .semibold))
                    Text(model.statusDetail).font(.system(size: 10)).foregroundStyle(muted).lineLimit(2).textSelection(.enabled)
                }.frame(maxWidth: .infinity, alignment: .leading)
                if model.isBusy && model.operation?.isSimulation != true { ProgressView().controlSize(.small) }
                Button { withAnimation(.easeInOut(duration: 0.16)) { showJournal.toggle() } } label: {
                    HStack(spacing: 6) { Image(systemName: "terminal"); Text("Journal"); Image(systemName: showJournal ? "chevron.down" : "chevron.up").font(.system(size: 8, weight: .bold)) }
                }.buttonStyle(RepereButtonStyle(compact: true))
                if model.operation == .route || model.operation == .random {
                    Button { model.toggleMovementPause() } label: {
                        Label(model.routePausePending ? "Patientez…" : model.routePaused ? "Reprendre" : "Pause", systemImage: model.routePaused ? "play.fill" : "pause.fill")
                    }.buttonStyle(RepereButtonStyle()).disabled(!model.canPauseMovement)
                }
                Button { model.restore() } label: { Label(model.operation?.isSimulation == true ? "Arrêter et restaurer le GPS" : "Restaurer le GPS", systemImage: "stop.circle") }
                    .buttonStyle(RepereButtonStyle()).disabled(!model.canRestore)
                Button { model.apply() } label: {
                    Label(model.operation?.isSimulation == true ? (model.stopping ? "Arrêt en cours…" : "Session en cours") : model.destinationMode == .route ? "Démarrer le trajet" : model.destinationMode == .random ? "Démarrer l’aléatoire" : "Appliquer la position", systemImage: model.destinationMode == .fixed ? "location.fill" : "play.fill")
                        .frame(minWidth: 150)
                }.buttonStyle(RepereButtonStyle(primary: true)).disabled(!model.canApply)
            }.padding(.horizontal, 24).padding(.vertical, 17)
        }.background(surface)
    }
}

struct ConnectionGuide: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        ScrollView {
        VStack(alignment: .leading, spacing: 22) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Prêt à connecter l’iPhone").font(.system(size: 23, weight: .semibold, design: .rounded))
                    Text("À faire une première fois, puis à vérifier si la connexion échoue.")
                        .font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
            }
            step("1", "Branchez et déverrouillez", "Reliez l’iPhone au Mac avec un câble USB qui transporte les données. Laissez l’iPhone déverrouillé.")
            step("2", "Autorisez le Mac", "Sur l’iPhone, touchez « Faire confiance » puis saisissez votre code. Si nécessaire, ouvrez l’iPhone dans le Finder du Mac.")
            step("3", "Activez le mode développeur", "Sur l’iPhone : Réglages → Confidentialité et sécurité → Mode développeur. Suivez la demande de redémarrage et confirmez l’activation. Si ce réglage n’apparaît pas, la préparation vous indiquera les prérequis manquants.")
            step("4", "Préparez, puis choisissez le lieu", "Recherchez l’iPhone avec ↻, préparez la connexion, choisissez un point et appliquez la position. Une autorisation macOS peut être nécessaire pour la connexion développeur.")
            step("5", "Sans câble, avec le même Mac", "D’abord en USB : Finder → votre iPhone → Général → « Afficher cet iPhone lorsqu’il est en Wi-Fi » → Appliquer. Arrêtez la session USB, débranchez le câble, choisissez Wi-Fi dans Repère, actualisez et préparez à nouveau. Le Mac et l’iPhone doivent être sur le même réseau. Ce mode reste à vérifier avec votre iPhone.")
            step("6", "Un trajet avec la durée de votre choix", "Choisissez Trajet A → B, placez A puis B et cliquez sur Tracer le trajet. Saisissez la durée en minutes, puis Démarrer le trajet. La vitesse est calculée automatiquement (150 km/h maximum). Les pauses prolongent la durée totale ; le point B est maintenu à l’arrivée.")
            step("7", "Se déplacer dans un cercle", "Choisissez Aléatoire, placez le centre et saisissez un rayon de 10 à 10 000 mètres. Choisissez la vitesse, puis démarrez. Le déplacement continue dans la zone jusqu’à l’arrêt, avec une commande de pause. Il ne suit pas les routes ni les obstacles.")
            Text("Gardez Repère ouvert et le Mac éveillé pendant toute la session, en USB ou Wi-Fi. Le trajet ne peut pas continuer avec le Mac éteint ou hors réseau. Pour terminer, utilisez « Arrêter et restaurer le GPS », puis contrôlez la position dans les apps.")
                .font(.system(size: 12)).lineSpacing(4).padding(15)
                .background(accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
            HStack {
                Text("Simulation au niveau de l’iPhone : aucun ciblage séparé de Snapchat ou Localiser. Leur mise à jour reste à vérifier.")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                Spacer()
                Button("Compris") { dismiss() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
            }
        }.padding(28).foregroundStyle(ink).tint(accent).background(canvas)
        }
    }
    private func step(_ number: String, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 13) {
            Text(number).font(.system(size: 12, weight: .bold)).foregroundStyle(accent)
                .frame(width: 27, height: 27).background(accent.opacity(0.10), in: Circle())
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(detail).font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(3).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

final class RepereDelegate: NSObject, NSApplicationDelegate {
    weak var model: RepereModel?
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        model?.requestQuit() ?? .terminateNow
    }
}

/// Route the red close button and Cmd-W through the same restoration flow as
/// Cmd-Q. The existing SwiftUI window delegate still receives its other events.
struct WindowCloseGuard: NSViewRepresentable {
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeNSView(context: Context) -> AttachmentView {
        let view = AttachmentView()
        view.attach = { [weak coordinator = context.coordinator] window in coordinator?.attach(to: window) }
        return view
    }
    func updateNSView(_ view: AttachmentView, context: Context) {
        if let window = view.window { context.coordinator.attach(to: window) }
    }
    final class AttachmentView: NSView {
        var attach: ((NSWindow) -> Void)?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let window { attach?(window) }
        }
    }
    final class Coordinator: NSObject, NSWindowDelegate {
        weak var original: NSWindowDelegate?
        func attach(to window: NSWindow) {
            guard window.delegate !== self else { return }
            original = window.delegate
            window.delegate = self
        }
        func windowShouldClose(_ sender: NSWindow) -> Bool {
            NSApplication.shared.terminate(nil)
            return false
        }
        override func responds(to aSelector: Selector!) -> Bool {
            super.responds(to: aSelector) || (original?.responds(to: aSelector) ?? false)
        }
        override func forwardingTarget(for aSelector: Selector!) -> Any? {
            if original?.responds(to: aSelector) == true { return original }
            return super.forwardingTarget(for: aSelector)
        }
    }
}

@main
struct RepereApp: App {
    @NSApplicationDelegateAdaptor(RepereDelegate.self) private var delegate
    @StateObject private var model = RepereModel()
    var body: some Scene {
        Window("Repère", id: "main") {
            RepereView(model: model)
                .background(WindowCloseGuard().frame(width: 0, height: 0))
                .preferredColorScheme(.dark)
                .onAppear {
                    delegate.model = model
                    NSApplication.shared.activate(ignoringOtherApps: true)
                    if !model.preview { model.scan() }
                }
        }
        .defaultSize(width: 1220, height: 870)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(replacing: .help) {
                Button("Guide de connexion de Repère") { model.showGuide = true }
            }
        }
    }
}
