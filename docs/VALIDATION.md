# Validation — Repère 1.3

État au 7 octobre 2026, sur un Mac Apple Silicon. Ces résultats distinguent les tests logiciels et l’affichage de l’app des essais sur appareil physique.

## Tests logiciels

- **86 tests Python réussis** : coordonnées, connexion simulée, préparation, position fixe, interpolation des trajets, durée, déplacement aléatoire, pause/reprise, arrêt, perte de connexion et restauration après erreur.
- **21 scénarios Swift réussis** : validation des paramètres, construction des commandes, préparation obligatoire, états de session, pause et restauration.
- **Carte JavaScript** : validation et suppression du cercle, modification du rayon, cadrage, nettoyage des tracés et placement du marqueur autour de la ligne de changement de date. Leaflet est remplacé par des objets de test pour ces contrôles.
- **Compilation Swift réussie** pour macOS 14+ sur Apple Silicon.

Les tests du déplacement aléatoire couvrent notamment les rayons de 10 et 10 000 mètres, huit centres incluant les pôles et la ligne de changement de date, l’appartenance au disque de milliers de points, la continuité du déplacement et le tirage des destinations. La pause entre deux mises à jour, la pause pendant un envoi et la conservation de la durée de déplacement disposent de tests de régression.

## Vérifications visuelles

L’app native a été ouverte avec le mode Aléatoire. Un cercle de 500 mètres autour d’un lieu public de démonstration a été affiché sur la carte, avec ses champs de coordonnées, son rayon et sa vitesse. La saisie d’un rayon invalide affiche une erreur. Le retour au mode Position fixe a été contrôlé. Les commandes restent accessibles en bas de la fenêtre.

Le mode Trajet avait aussi été vérifié visuellement en version 1.2 : calcul Tour Eiffel → Louvre, tracé et marqueurs A/B, durée de 20 minutes et vitesse calculée. Ce contrôle historique ne constitue pas un nouvel essai de trajet sur appareil.

## Ce qui reste à vérifier

Aucun essai de simulation réel sur iPhone ne valide cette version. Les tests de transport emploient des objets simulés ; les marqueurs de l’interface représentent les coordonnées envoyées et ne confirment pas celles affichées ou partagées par une autre app.

- Déplacement réel en USB, pause, reprise et retour à la position réelle.
- Effet concret dans Plans, Localiser et Snapchat selon la version d’iOS.
- Connexion et restauration sur le réseau Wi-Fi d’un iPhone associé.
- Construction sur Mac Intel ; distribution autonome et notarisation.

La compilation publiée comme sources dépend d’un Python installé localement. Un binaire construit sur un Mac ne doit pas être présenté comme un installateur universel pour d’autres machines.
