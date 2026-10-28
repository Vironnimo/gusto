"""CLI owner gate: mapping, presentation, local control and real process boundaries."""
from __future__ import annotations

import importlib
import os
import sys

from _cli_support import (
    HOME, ENV, check, start_server, initialize_command_fixture, process_json,
    process_json_error, store_snapshot, stop_server, run,
)
import _cli_support as support
from _cli_forwarding import check_forwarding
from _cli_client import check_client_contract
from _cli_presentation import check_presentation
from _cli_local import (check_lifecycle_contract, check_serve_json_contract,
                        check_serve_dependency_preflight)
from _cli_editor import (check_edit_readback_contract, check_editor_arguments_contract,
                         check_uncertain_delivery_contract, check_edit_cleanup_failure)
from _cli_scenarios import (check_catalog, check_images_and_cooking, check_content,
                            check_shopping, check_favorites, check_diagnostics_and_archive)


def main():
    check_client_contract()
    importlib.import_module("gusto.cli")
    check("gusto.core" not in sys.modules,
          "importing the normal CLI client must not import local persistence")
    os.environ.update(ENV)
    check_forwarding()
    check_presentation()
    check_lifecycle_contract()
    check_edit_readback_contract()
    check_edit_cleanup_failure()
    start_server()
    initialize_command_fixture()
    home = process_json("home")
    from pathlib import Path
    check(Path(home["path"]).resolve() == HOME.resolve()
          and home["source"] == "environment",
          "home --json must explain the active GUSTO_HOME data directory")
    check(home["server_url"] == support.SERVER_URL and home["server_reachable"] is True,
          "home --json must report server discovery and reachability separately")
    check(Path(home["server_data_path"]).resolve() == HOME.resolve()
          and isinstance(home["server_revision"], int) and home["server_version"],
          "home --json must identify the reachable server-owned store/version")
    check_uncertain_delivery_contract()
    check_editor_arguments_contract()
    check_serve_json_contract()
    check_serve_dependency_preflight()
    invalid_port = run("serve", "--port", "70000", expect=2)
    check(not invalid_port.stdout and "--port" in invalid_port.stderr,
          "serve must reject ports outside the TCP range")
    slug = check_catalog()
    first_photo = check_images_and_cooking(slug)
    check_content(slug)
    check_shopping(slug)
    check_favorites(first_photo)
    check_diagnostics_and_archive(slug)
    snapshot = store_snapshot()
    stop_server()
    unavailable = process_json_error("new", "Darf nicht angelegt werden")
    check("nicht erreichbar" in unavailable["error"],
          "a domain command must fail cleanly while the server is stopped")
    check(store_snapshot() == snapshot,
          "a failed remote mutation must not change the local store")
    offline = process_json("check", "--offline")
    check("recipe_count" in offline,
          "the explicitly marked offline check must not need the server")
    stopped_home = process_json("home")
    check(stopped_home["server_reachable"] is False,
          "home must stay local and report a stopped service")

    refused_uninstall = process_json_error("uninstall", "--keep-data")
    check("Projekt-Checkout" in refused_uninstall["error"],
          "uninstall must refuse to delete a development checkout")

    support.API_CLIENT.close()
    print(f"OK - {support.checks} CLI checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
