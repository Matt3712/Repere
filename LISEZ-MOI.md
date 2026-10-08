# Repère 1.4 — simulation de position depuis le Mac

Repère est une app Mac qui envoie des positions choisies au service de simulation développeur d’un iPhone. Elle propose une carte, une recherche de lieu, des coordonnées libres, des trajets et l’arrêt explicite de la simulation.

La version 1.4 embarque Python et les dépendances de connexion pour fonctionner sans installation Python séparée. Les modes **Position fixe**, **Trajet A → B** et **Aléatoire** se choisissent dans l’en-tête. Les commandes restent en bas de la fenêtre ; le bouton **Journal** affiche ou masque l’activité.

**Le fonctionnement sur un iPhone physique, y compris l’effet dans Localiser et Snapchat, reste à valider.** Une commande de simulation envoyée avec succès ne prouve pas que ces services afficheront ou partageront cette position. L’app présente cette distinction dans son état.

## Installer l’app

Sur un **Mac Apple Silicon (M1 et suivants), sous macOS 14 ou ultérieur**, télécharge le [DMG depuis GitHub](https://github.com/Matt3712/Repere/releases/download/v1.4.0/Repere-1.4-AppleSilicon.dmg), ouvre-le et glisse **Repere.app** dans **Applications**. Éjecte le disque puis ouvre l’app. Python et ses dépendances sont inclus. Aucun paquet Intel n’est fourni.

L’app est signée localement et n’est pas notariée par Apple. Si macOS bloque sa première ouverture, consulte les [instructions officielles Apple](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac). La publication fournit aussi la somme SHA-256 du DMG.

## Compiler les sources

La compilation demande un Mac Apple Silicon et les outils de ligne de commande Xcode. Le script télécharge une distribution Python autonome dont la version et la somme SHA-256 sont épinglées.

Depuis la racine du dépôt :

```sh
cd Repere-source
./build.command
open ../Repere.app
```

Le script prépare le runtime embarqué, installe les dépendances épinglées et crée `Repere.app` à côté du dossier des sources. L’app n’utilise pas le Python du système. `./package.command` produit ensuite le DMG Apple Silicon.

## Démarrer

1. Ouvre `Repere.app` depuis Applications.
2. Branche l’iPhone en USB, déverrouille-le et accepte **Faire confiance à cet ordinateur** sur l’iPhone. Clique sur Actualiser si nécessaire.
3. Sur l’iPhone, active **Réglages → Confidentialité et sécurité → Mode développeur**. Redémarre et confirme l’activation. Si ce réglage n’apparaît pas, clique d’abord sur **Préparer la connexion** : l’app tente de le rendre visible, puis te laisse l’activer toi-même. S’il reste absent, associe l’iPhone à Xcode sur le Mac, comme indiqué dans le guide Apple ci-dessous.
4. Choisis ton iPhone et clique sur **Préparer la connexion**. L’app lit la version exacte d’iOS. La première préparation peut télécharger une image développeur et prendre plusieurs minutes ; garde Internet et le câble connectés.
5. Recherche un lieu ou clique sur la carte, vérifie les coordonnées, puis applique la simulation. Garde l’app et la connexion USB ouvertes.
6. Vérifie d’abord la position dans Plans sur l’iPhone, puis dans Snapchat et Localiser. Dans Localiser, vérifie aussi que cet iPhone est bien l’appareil utilisé pour **Ma position**. La position de l’onglet Personnes et le réseau de recherche des appareils sont des mécanismes distincts.
7. Pour terminer, utilise le bouton d’arrêt/restauration. L’app envoie une commande d’arrêt sur la même connexion, puis ferme la session. Vérifie le retour à la position réelle dans Plans. Si la restauration échoue, reconnecte l’iPhone et réessaie ; redémarre-le si nécessaire.

## Un trajet A → B avec une durée choisie

1. En haut de la fenêtre, choisis **Trajet A → B**.
2. Sélectionne **A — Départ**, puis clique sur la carte ou choisis un résultat de recherche. Repère passe ensuite à **B — Arrivée**. Tu peux aussi saisir les quatre coordonnées ou inverser A et B.
3. Choisis **Par la route** (itinéraire voiture) ou **Ligne directe**, puis **Tracer le trajet**. Le tracé routier ajuste A et B à des voies accessibles ; les marqueurs indiquent les points effectivement utilisés.
4. Choisis **Durée** et saisis le nombre de minutes souhaité, par exemple `20` ou `1,5`. Repère calcule la vitesse nécessaire. Si elle dépasse 150 km/h, augmente la durée. Tu peux aussi choisir **Vitesse** et régler directement 1 à 150 km/h.
5. Prépare la connexion si nécessaire, puis clique sur **Démarrer le trajet**. La position est mise à jour environ chaque seconde ; le marqueur et la progression suivent les coordonnées envoyées.
6. **Pause** termine l’envoi de la position correspondant à l’instant de pause, puis maintient ce point ; **Reprendre** poursuit le trajet. La durée demandée correspond au déplacement, à partir de l’envoi du point A : les pauses s’ajoutent et la latence de connexion peut retarder les mises à jour. À l’arrivée, B est maintenu jusqu’à **Arrêter et restaurer le GPS**.

Le calcul routier envoie les coordonnées A et B au serveur public OSRM, uniquement lorsque tu cliques sur Tracer. Il utilise OpenStreetMap, fonctionne sur demande avec cache et ne dépasse pas une requête par seconde. Le service public n’offre pas de garantie de disponibilité ; aucun tracé direct ne remplace automatiquement une route introuvable. Un serveur OSRM HTTPS personnel peut être configuré par `REPERE_ROUTER_URL`.

## Un déplacement aléatoire dans un cercle

1. Choisis **Aléatoire** dans l’en-tête.
2. Clique sur la carte pour placer le centre du cercle, utilise une recherche de lieu ou saisis ses coordonnées.
3. Saisis un **rayon de 10 à 10 000 mètres**. Le cercle affiché représente la zone choisie.
4. Règle une **vitesse de 1 à 150 km/h**, puis clique sur **Démarrer l’aléatoire**.
5. Le déplacement commence au centre et rejoint successivement des points tirés dans le cercle. Le marqueur suit les positions envoyées et le compteur indique la distance parcourue.
6. Utilise **Pause**, **Reprendre**, puis **Arrêter et restaurer le GPS** pour terminer. Le déplacement continue jusqu’à l’arrêt ; il n’a pas de destination finale.

Ce mode calcule des segments directs à l’intérieur du cercle. **Il ne suit pas les routes et ne contourne pas les bâtiments, l’eau ou les obstacles.** Aucun itinéraire OSRM n’est demandé pour ce mode. Le centre, le rayon et la vitesse se règlent avant le démarrage de la session. La pause fonctionne comme celle d’un trajet : le point correspondant à l’instant de pause est envoyé avant son maintien.

## Retirer le câble et utiliser le Wi-Fi

Le mode Wi-Fi permet de retirer le câble, mais **le Mac doit rester éveillé, Repère ouvert et l’iPhone sur le même réseau Wi-Fi**. Une session USB en cours ne se transforme pas automatiquement en session Wi-Fi.

1. Avec l’iPhone branché et déjà associé au Mac, ouvre **Finder → ton iPhone → Général**.
2. Coche **Afficher cet iPhone lorsqu’il est en Wi-Fi**, puis clique sur **Appliquer**.
3. Arrête toute simulation USB avec le bouton de restauration, puis débranche le câble.
4. Dans Repère, choisis **Wi-Fi**, actualise la liste, sélectionne l’iPhone et clique sur **Préparer la connexion**. Le réglage doit avoir été activé au préalable ; Repère ne le modifie pas à ta place.
5. Lance ensuite une position fixe, un trajet ou un déplacement aléatoire. Si la connexion est perdue, rétablis le réseau puis restaure le GPS. Si nécessaire, rebranche le câble et sélectionne **USB** pour restaurer le même iPhone.

Le code refuse le mode Wi-Fi pour iOS 17.0 à 17.3 ; utilise USB pour ces versions. Pour les autres versions, la disponibilité dépend des services développeur exposés par l’appareil. **Le fonctionnement réel en Wi-Fi reste à valider sur iPhone.** Un déplacement autonome avec le Mac éteint ou hors réseau n’est pas pris en charge : le Mac doit envoyer les positions successives. Une position éventuellement conservée après une coupure n’est pas une garantie de fonctionnement.

## Fonctionnement du service développeur

`com.apple.dt.simulatelocation` est un identifiant de service développeur, pas une variable globale. Le backend utilise ce service pour iOS antérieur à 17 et le service DVT `com.apple.instruments.server.services.LocationSimulation` à partir d’iOS 17.

Le tunnel pour les versions récentes d’iOS est créé dans le processus, sans commande `sudo`. Pour iOS 17.0 à 17.3, l’app essaie le tunnel natif de macOS si le tunnel userspace n’est pas disponible. La compatibilité concrète reste liée à la version d’iOS et aux services exposés par l’appareil.

Apple expose `isSimulatedBySoftware` aux applications. Repère ne masque pas cet indicateur et ne garantit pas que Snapchat acceptera une position simulée. Une simulation de localisation peut affecter d’autres apps de l’iPhone pendant la session.

### Snapchat seul ou Localiser seul ?

Cette méthode ne permet pas de cibler une application séparément. Le service reçoit une latitude et une longitude pour l’iPhone, sans identifiant d’application. Repère ne peut donc pas simuler une position pour Snapchat tout en conservant la vraie position pour Localiser, ou l’inverse. Chaque app peut accepter, ignorer ou actualiser différemment les positions simulées. Un simple sélecteur dans Repère ne changerait pas cette limite ; l’interface indique donc explicitement la portée de la simulation.

## Données et installation

- App native SwiftUI avec carte Leaflet intégrée ; aucun serveur Web local et aucun compte Apple/Snapchat demandé.
- La carte utilise OpenStreetMap ; les recherches saisies sont envoyées au service Photon de Komoot. Les deux nécessitent Internet. L’app ne demande pas ta position réelle au Mac. Tu peux saisir directement les coordonnées si tu ne souhaites pas envoyer une recherche.
- Ces services publics sont prévus ici pour un usage personnel modéré : recherche uniquement à la demande, cache des résultats et aucune fonction de téléchargement hors ligne. [Conditions des tuiles OpenStreetMap](https://operations.osmfoundation.org/policies/tiles/) et [service Photon](https://github.com/komoot/photon#demo-server). Le service de recherche peut être remplacé par un serveur Photon via la variable `REPERE_GEOCODER_URL`.
- Les services USB et les téléchargements développeur utilisent `pymobiledevice3` version 11.20.2. Ses dépendances et licences sont incluses dans l’environnement Python de l’app.
- Les données de connexion et images développeur sont stockées dans `~/Library/Application Support/Repere`. Ces données peuvent inclure les informations d’association au téléphone : elles doivent rester sur ton Mac.
- Python 3.14 et les dépendances sont embarqués dans le paquet. Le lancement isolé ignore les variables et les paquets Python personnels du Mac.
- Pour reconstruire l’app, suis la section Compiler les sources. Le runtime est fixé dans `runtime-manifest.json` et les dépendances dans `requirements.txt` et `requirements.lock`.
- Les trajets directs et le déplacement aléatoire sont calculés localement. Les informations d’association au téléphone et les images développeur ne font pas partie des sources publiques.

## Vérification et limites

Les sources et les tests sont fournis dans `Repere-source`. La validation comprend **86 tests Python**, **21 scénarios du modèle Swift** et les contrôles de carte JavaScript. Ils vérifient notamment les coordonnées, les déplacements, les pauses, la gestion d’une session et les échecs de restauration avec des transports simulés. Ils ne remplacent pas un essai USB ou Wi-Fi réel ni une vérification de Localiser/Snapchat.

Si tu ouvres `Map/index.html` directement, il s’agit seulement d’un aperçu de carte : il ne peut pas communiquer avec l’iPhone. Utilise **Repere.app** pour les fonctions de connexion et de simulation.

Pour lancer les tests depuis le dossier des sources :

```sh
../Repere.app/Contents/Resources/runtime/bin/python3 -I -B -m unittest discover -s . -p 'test_*.py'
./test_model.command
node test_map.cjs
```

Le dernier contrôle nécessite Node.js. Le projet est distribué sous [GNU GPL version 3](Repere-source/LICENSE) ; les composants tiers conservent leurs licences et attributions. Le [README](README.md) présente le projet.

## Sources techniques

- [Apple : activer le mode développeur](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)
- [Apple : reconnaître une position simulée](https://developer.apple.com/documentation/corelocation/cllocationsourceinformation/issimulatedbysoftware)
- [Apple : partager sa position dans Localiser](https://support.apple.com/en-gb/guide/iphone/iph01954dc44/ios)
- [Snapchat : partage de position sur Snap Map](https://help.snapchat.com/hc/en-us/articles/7012309470740-How-do-I-share-my-location-on-Snap-Map)
- [pymobiledevice3 : service historique](https://github.com/doronz88/pymobiledevice3/blob/master/pymobiledevice3/services/simulate_location.py)
- [pymobiledevice3 : service DVT](https://github.com/doronz88/pymobiledevice3/blob/master/pymobiledevice3/services/dvt/instruments/location_simulation.py)
- [pymobiledevice3 : tunnels iOS 17 et suivants](https://doronz88.github.io/pymobiledevice3/guides/ios17-tunnels/)
- [Apple : afficher et synchroniser l’iPhone en Wi-Fi](https://support.apple.com/fr-cf/guide/mac-help/mchlada1d602/mac)
- [OSRM : API des trajets](https://project-osrm.org/docs/v26.4.0/http)
- [OSRM : conditions du serveur public](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server)
