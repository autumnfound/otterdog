# *******************************************************************************
# Copyright (c) 2023 Eclipse Foundation and others.
# This program and the accompanying materials are made available
# under the terms of the MIT License
# which is available at https://spdx.org/licenses/MIT.html
# SPDX-License-Identifier: MIT
# *******************************************************************************

import difflib
import os

from colorama import Style, Fore

from otterdog.config import OrganizationConfig
from otterdog.models.github_organization import GitHubOrganization

from . import Operation


class CanonicalDiffOperation(Operation):
    def __init__(self):
        super().__init__()

    def pre_execute(self) -> None:
        self.printer.print(f"Showing changes to a canonical version of the configuration at "
                           f"'{self.config.config_file}'")

    def execute(self, org_config: OrganizationConfig) -> int:
        github_id = org_config.github_id

        self.printer.print(f"Organization {Style.BRIGHT}{org_config.name}{Style.RESET_ALL}[id={github_id}]")
        self.printer.level_up()

        try:
            org_file_name = self.jsonnet_config.get_org_config_file(github_id)

            if not os.path.exists(org_file_name):
                self.printer.print_warn(f"configuration file '{org_file_name}' does not yet exist, run fetch first")
                return 1

            try:
                organization = \
                    GitHubOrganization.load_from_file(github_id,
                                                      self.jsonnet_config.get_org_config_file(github_id),
                                                      self.config,
                                                      False)
            except RuntimeError as ex:
                self.printer.print_warn(f"failed to load configuration: {str(ex)}")
                return 1

            with open(org_file_name, "r") as file:
                original_config = file.read()

            original_config_without_comments: list[str] = \
                list(filter(lambda x: not x.strip().startswith("#"), original_config.split("\n")))

            canonical_config = organization.to_jsonnet(self.jsonnet_config)
            canonical_config_as_lines = canonical_config.split("\n")

            if original_config_without_comments[-1] == "":
                original_config_without_comments.pop(-1)

            for line in difflib.unified_diff(original_config_without_comments,
                                             canonical_config_as_lines,
                                             "original",
                                             "canonical"):
                if line.startswith("+"):
                    self.printer.print(f"{Fore.GREEN}{line}{Style.RESET_ALL}")
                elif line.startswith("-"):
                    self.printer.print(f"{Fore.RED}{line}{Style.RESET_ALL}")
                else:
                    self.printer.print(line)

            return 0

        finally:
            self.printer.level_down()
