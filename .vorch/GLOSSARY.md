# Glossary

What Gusto's shared terms mean and what they are not, so they are not confused with the coding agent's own environment or other systems. How a concept currently works lives in its domain map.

## Service (`Gusto-Dienst`)
The one per-user Gusto process that serves Core, the browser UI, the anonymous LAN command API and change events; browser and normal CLI commands are its clients (`service.md`). Not an installed runtime on its own, and not `gusto serve`, which is only the explicit foreground development and recovery start.

## Operational installation (`Betriebsbereite Installation`)
A versioned app runtime with a stable `gusto` command, registered per-user autostart, a started service and a passed health check (`lifecycle.md`). Not a merely copied runtime and not the installation of an agent skill; normal updates happen only through `gusto update`.

## Skill
The agent instructions Gusto ships in `skill/gusto/` and installs into an agent host on request, teaching agents to operate Gusto through the CLI (`cli.md`). Not a skill of the coding agent working in this repository, and not a Vorch workflow.

## Recipe archive (`Rezeptarchiv`)
The reversible state of a removed recipe: its Markdown, full metadata and owned images kept together outside the active cookbook; a normal "delete" archives (`catalog.md`). Not permanent deletion; only an explicitly confirmed purge destroys a snapshot.

## Shopping need (`Einkaufsbedarf`)
A lasting household purchase need such as `Pizzateig` that groups the known wordings of free-text shopping items and owns a ranked list of preferred products (`shopping.md`). Not a concrete product and not a single, transient shopping-list item.

## Deployment target
An environment on which Gusto must run and be checked reliably. Not a limit on supported platforms: the Raspberry Pi is the first Linux deployment target, not an exclusive one.
