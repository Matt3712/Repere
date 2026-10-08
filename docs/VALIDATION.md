# Validation — Repère 1.4

État au 8 octobre 2026, sur un Mac Apple Silicon sous macOS 15.6.1. Ces résultats distinguent les tests logiciels et l’affichage de l’app des essais sur appareil physique.

## Tests logiciels

- **86 tests Python réussis avec le runtime embarqué** : coordonnées, connexion simulée, préparation, position fixe, interpolation des trajets, durée, déplacement aléatoire, pause/reprise, arrêt, perte de connexion et restauration après erreur.
- **21 scénarios Swift réussis** : validation des paramètres, construction des commandes, préparation obligatoire, états de session, pause et restauration.
- **Carte JavaScript** : validation et suppression du cercle, modification du rayon, cadrage, nettoyage des tracés et placement du marqueur autour de la ligne de changement de date. Leaflet est remplacé par des objets de test pour ces contrôles.
- **Compilation Swift réussie** pour macOS 14+ sur Apple Silicon.

Les tests du déplacement aléatoire couvrent notamment les rayons de 10 et 10 000 mètres, huit centres incluant les pôles et la ligne de changement de date, l’appartenance au disque de milliers de points, la continuité du déplacement et le tirage des destinations. La pause entre deux mises à jour, la pause pendant un envoi et la conservation de la durée de déplacement disposent de tests de régression.

## Vérifications visuelles

L’app native 1.4 a été ouverte avec le mode Aléatoire. La carte et un cercle de 500 mètres autour de la tour Eiffel ont été affichés, avec les coordonnées, le rayon et la vitesse. Les commandes restent accessibles en bas de la fenêtre. Aucun iPhone n’était connecté.

La validation d’un rayon invalide et le retour au mode Position fixe avaient été contrôlés en version 1.3.

Le mode Trajet avait aussi été vérifié visuellement en version 1.2 : calcul Tour Eiffel → Louvre, tracé et marqueurs A/B, durée de 20 minutes et vitesse calculée. Ce contrôle historique ne constitue pas un nouvel essai de trajet sur appareil.

## Paquet autonome et DMG

- CPython **3.14.8**, distribution Astral `python-build-standalone` du 3 octobre 2026, archive vérifiée par SHA-256.
- **100 dépendances épinglées**, plus pip, embarquées avec leurs notices ; `pip check` ne signale aucune incompatibilité.
- **101 binaires Mach-O contrôlés** : présence d’une tranche arm64, version minimale déclarée compatible avec macOS 14, bibliothèques requises disponibles dans l’app ou le système.
- Signature locale vérifiée avec `codesign --verify --deep --strict`. Cette signature **n’est pas une notarisation Apple**.
- Copie de l’app dans un dossier temporaire avec espaces et accents : Python, les modules natifs, les services développeur importés et `bridge.py --help` fonctionnent depuis ce nouvel emplacement.
- Variables `PYTHONHOME`, `PYTHONPATH` et `PYTHONUSERBASE` volontairement incompatibles pendant ce contrôle : le lancement isolé les ignore et utilise le runtime embarqué.
- DMG compressé créé et contrôle d’intégrité `hdiutil verify` réussi. Sa somme SHA-256 accompagne la publication GitHub.

Ces contrôles valident le paquet sur le Mac de construction. Ils ne remplacent pas un essai sur une autre machine ou sur macOS 14. Aucun Python installé séparément n’est requis par le paquet 1.4.

## Ce qui reste à vérifier

Aucun essai de simulation réel sur iPhone ne valide cette version. Les tests de transport emploient des objets simulés ; les marqueurs de l’interface représentent les coordonnées envoyées et ne confirment pas celles affichées ou partagées par une autre app.

- Déplacement réel en USB, pause, reprise et retour à la position réelle.
- Effet concret dans Plans, Localiser et Snapchat selon la version d’iOS.
- Connexion et restauration sur le réseau Wi-Fi d’un iPhone associé.
- Essai sur un autre Mac Apple Silicon, notamment sous macOS 14.
- Notarisation Apple. Aucun binaire Intel n’est fourni.

Le DMG cible Apple Silicon uniquement ; il ne s’agit pas d’un installateur universel Intel/Apple Silicon.
