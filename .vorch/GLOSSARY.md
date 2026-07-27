## Einkaufsbedarf

**Definition:** Ein dauerhafter Kaufbedarf wie „Pizzateig“, der bekannte Formulierungen freier Einkaufsposten zusammenführt und eine geordnete Liste bevorzugter Produkte besitzt.

**Not:** Kein konkretes Produkt und kein einzelner, vergänglicher Einkaufsposten.

## Rezeptarchiv

**Definition:** Der reversible Lifecycle-Zustand eines Rezepts, in dem Markdown, vollständige Metadaten und eigene Bilder gemeinsam außerhalb des aktiven Kochbuchs aufbewahrt werden. Ein Agent behandelt ein normales „Rezept löschen/entfernen“ als Archivieren und kann den Snapshot vollständig wiederherstellen.

**Not:** Kein endgültiges Löschen; nur `archive purge --yes` zerstört den Snapshot, und sichtbare Einkaufsposten mit diesem Rezept als Quelle verhindern das Purge.

## Deployment-Ziel

**Definition:** Eine besonders wichtige Umgebung, auf der Gusto zuverlässig laufen und geprüft werden soll. Sie bestimmt weder das allgemeine Installationsmodell noch schränkt sie die unterstützten Plattformen ein.

**Not:** Der Raspberry Pi ist ein erstes Linux-Deployment-Ziel, keine Exklusivplattform.

## Gusto-Dienst

**Definition:** Der pro Benutzer laufende Gusto-Prozess, der Core, Browser-UI,
anonyme LAN-API und Änderungsereignisse gemeinsam bereitstellt. Browser und
normale CLI-Befehle benutzen diesen einen Dienst.

**Not:** Keine bloße installierte Runtime und kein optionaler Zusatz zur App;
`gusto serve` ist nur der explizite Vordergrund-/Recovery-Start.

## Betriebsbereite Installation

**Definition:** Eine versionierte App-Runtime mit stabilem `gusto`-Befehl,
registriertem Benutzer-Autostart, sofort gestarteten Gusto-Dienst und
erfolgreicher Health-Prüfung.

**Not:** Weder eine bloß kopierte Runtime noch die Installation eines
Agent-Skills; normale Updates erfolgen ausschließlich mit `gusto update`.
