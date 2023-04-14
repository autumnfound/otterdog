#  *******************************************************************************
#  Copyright (c) 2023 Eclipse Foundation and others.
#  This program and the accompanying materials are made available
#  under the terms of the MIT License
#  which is available at https://spdx.org/licenses/MIT.html
#  SPDX-License-Identifier: MIT
#  *******************************************************************************

import os

from colorama import Style

from otterdog.config import OtterdogConfig, OrganizationConfig
from otterdog.providers.github import Github
from otterdog.organization import load_from_github
from otterdog.utils import IndentingPrinter

from . import Operation


class ImportOperation(Operation):
    def __init__(self):
        self.config = None
        self.jsonnet_config = None
        self._printer = None

    @property
    def printer(self) -> IndentingPrinter:
        return self._printer

    def init(self, config: OtterdogConfig, printer: IndentingPrinter) -> None:
        self.config = config
        self.jsonnet_config = self.config.jsonnet_config
        self._printer = printer

    def pre_execute(self) -> None:
        self.printer.print(f"Importing resources for configuration at '{self.config.config_file}'")

    def execute(self, org_config: OrganizationConfig) -> int:
        github_id = org_config.github_id

        self.printer.print(f"Organization {Style.BRIGHT}{org_config.name}{Style.RESET_ALL}[id={github_id}]")

        org_file_name = self.jsonnet_config.get_org_config_file(github_id)

        if os.path.exists(org_file_name) and not self.config.force_processing:
            self.printer.print(f"\n{Style.BRIGHT}Definition already exists{Style.RESET_ALL} at "
                               f"'{org_file_name}'.\n"
                               f"  Performing this action will overwrite its contents.\n"
                               f"  Do you want to continue?\n"
                               f"  Only 'yes' will be accepted to approve.\n\n")

            self.printer.print(f"  {Style.BRIGHT}Enter a value:{Style.RESET_ALL} ", end='')
            answer = input()
            if answer != "yes":
                self.printer.print("\nImport cancelled.")
                return 1

        self.printer.level_up()

        try:
            try:
                credentials = self.config.get_credentials(org_config)
            except RuntimeError as e:
                self.printer.print_error(f"invalid credentials\n{str(e)}")
                return 1

            gh_client = Github(credentials)

            if self.config.no_web_ui is True:
                self.printer.print_warn(f"The Web UI will not be queried as '--no-web-ui' has been specified, "
                                        f"the resulting config will be incomplete")

            organization = load_from_github(github_id,
                                            self.jsonnet_config,
                                            gh_client,
                                            self.config.no_web_ui,
                                            self.printer)

            ignored_keys = gh_client.web_org_settings if self.config.no_web_ui else {}
            output = organization.write_jsonnet_config(self.jsonnet_config, ignored_keys)

            output_dir = self.jsonnet_config.orgs_dir
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

            with open(org_file_name, "w") as file:
                file.write(output)

            self.printer.print(f"organization definition written to '{org_file_name}'")

            return 0
        finally:
            self.printer.level_down()
