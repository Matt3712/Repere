# Repère

![Version 1.4](https://img.shields.io/badge/version-1.4-4F8CFF)
![macOS 14+](https://img.shields.io/badge/macOS-14%2B-202631)
![SwiftUI](https://img.shields.io/badge/interface-SwiftUI-FA7343)
[![GPL-3.0](https://img.shields.io/badge/licence-GPL--3.0-blue)](Repere-source/LICENSE)

**Une carte sur le Mac pour simuler les déplacements de son iPhone.**

Repère permet de choisir une position, de suivre un trajet A → B ou de se déplacer continuellement dans un cercle. L’interface native réunit la carte, les réglages de déplacement, la connexion à l’iPhone et le journal de session.

**[⬇ Télécharger le DMG — Mac Apple Silicon](https://github.com/Matt3712/Repere/releases/download/v1.4.0/Repere-1.4-AppleSilicon.dmg)** · **[📖 Guide d’utilisation](https://github.com/Matt3712/Repere/blob/main/LISEZ-MOI.md)** · [Toutes les versions](https://github.com/Matt3712/Repere/releases)

![Illustration de Repère : carte, trajet et zone de déplacement aléatoire](docs/assets/repere-hero.svg)


La version 1.4 intègre Python et les dépendances de connexion : aucune installation de Python ou compilation n’est nécessaire pour utiliser le DMG. Le fonctionnement sur un iPhone physique, y compris l’effet dans Localiser et Snapchat, reste à valider. Une commande envoyée au service développeur ne confirme pas la position affichée ou partagée par ces apps.

## Fonctionnalités

| Mode | Ce qu’il permet |
| --- | --- |
| **Position fixe** | Choisir un point sur la carte, rechercher une adresse ou saisir des coordonnées. |
| **Trajet A → B** | Tracer un itinéraire routier ou direct, choisir une durée ou une vitesse, mettre en pause et reprendre. Le point B est maintenu à l’arrivée. |
| **Aléatoire — nouveau en 1.3** | Choisir le centre d’un cercle et un rayon de **10 à 10 000 m**, puis se déplacer continuellement à **1–150 km/h** jusqu’à l’arrêt. |
| **Connexion** | Préparer l’iPhone en USB ou sélectionner une connexion Wi-Fi déjà configurée. |
| **Contrôle de session** | Suivre le point envoyé, consulter le journal et demander la restauration du GPS. |

Le mode Aléatoire relie des points tirés dans le cercle par des segments directs. Il ne suit ni les routes, ni les bâtiments, ni les obstacles. Les modes Trajet et Aléatoire disposent de commandes **Pause** et **Reprendre**.

## Compatibilité

| Élément | État |
| --- | --- |
| **Mac** | macOS 14 ou ultérieur, **Apple Silicon (M1 et suivants)**. Aucun binaire Intel fourni. |
| **Installation** | DMG autonome avec CPython et les dépendances embarqués. Aucun Python à installer. |
| **Construction des sources** | Mac Apple Silicon et outils de ligne de commande Xcode. Le script télécharge le runtime Python épinglé et vérifie son SHA-256. |
| **iPhone** | Association au Mac et services développeur disponibles ; mode développeur activé lorsqu’il est requis. La compatibilité dépend de la version d’iOS. |
| **Wi-Fi** | Mac éveillé, Repère ouvert et iPhone sur le même réseau. Le code exclut iOS 17.0–17.3 pour ce transport. Fonctionnement sur appareil non validé. |

Repère utilise les services de simulation développeur de l’iPhone. Il ne permet pas de cibler séparément Snapchat ou Localiser et ne masque pas l’indicateur de position simulée exposé par iOS. Aucun fonctionnement autonome avec le Mac éteint ou déconnecté du réseau n’est proposé.

## Compiler depuis les sources

Sur un Mac Apple Silicon, installe les outils de ligne de commande Xcode (`xcode-select --install`), puis clone le dépôt :

```sh
git clone https://github.com/Matt3712/Repere.git
cd Repere
cd Repere-source
./build.command
open ../Repere.app
```

Le script compile l’app dans **`../Repere.app`**, embarque le runtime CPython autonome et installe les dépendances épinglées. Il construit le paquet dans un dossier temporaire avant de remplacer la copie précédente. Internet est nécessaire lors de la construction.

Pour créer le DMG, lance `./package.command`. Le runtime et sa provenance sont décrits dans [`runtime-manifest.json`](Repere-source/runtime-manifest.json). Les licences embarquées sont conservées dans les ressources de l’app.

## Première connexion

1. Branche l’iPhone en USB, déverrouille-le et accepte **Faire confiance à cet ordinateur**.
2. Dans Repère, sélectionne l’iPhone et clique sur **Préparer la connexion**.
3. Si nécessaire, active **Réglages → Confidentialité et sécurité → Mode développeur** sur l’iPhone, puis redémarre et confirme. Si le réglage est masqué, la préparation tente de le rendre visible.
4. Relance la préparation. Un téléchargement développeur peut être nécessaire à la première utilisation.

Le [guide complet](LISEZ-MOI.md) décrit les prérequis et les étapes de dépannage.


## Sources et validation

Les sources SwiftUI, le pont Python et la carte se trouvent dans [`Repere-source`](Repere-source). `requirements.txt` fixe `pymobiledevice3` à **11.20.2** et `requirements.lock` fixe ses dépendances.

Depuis ce dossier, après construction :

```sh
../Repere.app/Contents/Resources/runtime/bin/python3 -I -B -m unittest discover -s . -p 'test_*.py'
./test_model.command
node test_map.cjs
```
