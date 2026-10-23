# Release assets and pipeline

Owners: `scripts/build_release.py`, the public bootstraps, `.github/workflows/release.yml` and `scripts/ci_smoke_install.py`. To make a release, follow `.vorch/workflows/release-workflow.md`; this file records contracts needed when changing the machinery.

## Asset trust and source

The build creates a wheel, `install.py`, `install.ps1`, `install.sh`, `python-runtime-windows-x64.nupkg` and schema-2 `gusto-release.json`, without an outer bundle ZIP. The manifest includes Gusto version, `minimum_system_python`, and bare asset names with SHA-256. The Windows Python entry also names its version and `layout:tools`. Production builds download the pinned NuGet Python package and check the pinned hash before publishing; the hidden local Python source option exists for fixtures.

Windows bootstrap downloads and verifies wheel, installer and Python package before running the extracted interpreter. Linux uses suitable system Python and verifies wheel/installer. Runtime update validates wheel, installer and Windows Python manifest entries on every platform, although it downloads only what the update needs. `minimum_system_python` is metadata; the Linux bootstrap and shared installer explicitly require 3.10 rather than deriving that minimum dynamically from the manifest.

The first-install release base is persisted as `manifest_url`. Normal update reads it; its hidden override affects one call only. Default source is GitHub Releases `releases/latest/download/`. HTTPS or local fixtures are accepted, not arbitrary remote HTTP. These asset hashes do not pin pip's transitive web dependencies. Build attestations are published but not verified by bootstraps/update.

## CI order and coverage

A `v*` tag starts the workflow. Validation requires the tag to equal the package's numeric `v<version>`; quality then runs the reusable Linux/Windows gates. Build runs once after quality, checks each direct asset hash, and uploads one immutable workflow artifact. Windows and Linux smoke-install jobs download that same artifact. Publish waits for both smoke jobs, downloads it again, attests the tested assets and creates the latest GitHub release. It does not rebuild them.

`ci_smoke_install.py` refuses unless `GITHUB_ACTIONS=true`. Its fixture lives under checkout `.tmp`, uses a custom app/data root and port, verifies HTTP health/UI, installs the bundled skill into a fake host, exercises CLI through the installed service, status/restart, update --check and uninstall --keep-data. Linux uses the generated-unit adapter `ci_systemctl.py`, not a real user-session systemd manager. Real update activation/rollback, repair and data deletion remain hermetic-test coverage only.

`tests/test_packaging.py` owns manifest/assets, bootstrap rules, wheel content and installed payload checks. `tests/test_ci.py` owns workflow ordering, pinned actions, permissions and smoke-adapter expectations; `development.md` owns reusable quality gates.
