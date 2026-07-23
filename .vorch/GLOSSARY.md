## Einkaufsbedarf

**Definition:** Ein dauerhafter Kaufbedarf wie „Pizzateig“, der bekannte Formulierungen freier Einkaufsposten zusammenführt und eine geordnete Liste bevorzugter Produkte besitzt.

**Not:** Kein konkretes Produkt und kein einzelner, vergänglicher Einkaufsposten.

## Rezeptarchiv

**Definition:** Der reversible Lifecycle-Zustand eines Rezepts, in dem Markdown, vollständige Metadaten und eigene Bilder gemeinsam außerhalb des aktiven Kochbuchs aufbewahrt werden. Ein Agent behandelt ein normales „Rezept löschen/entfernen“ als Archivieren und kann den Snapshot vollständig wiederherstellen.

**Not:** Kein endgültiges Löschen; nur `archive purge --yes` zerstört den Snapshot, und sichtbare Einkaufsposten mit diesem Rezept als Quelle verhindern das Purge.

## Deployment-Ziel

**Definition:** Eine besonders wichtige Umgebung, auf der Gusto zuverlässig laufen und geprüft werden soll. Sie bestimmt weder das allgemeine Installationsmodell noch schränkt sie die unterstützten Plattformen ein.

**Not:** Der Raspberry Pi ist ein erstes Linux-Deployment-Ziel, keine Exklusivplattform.
