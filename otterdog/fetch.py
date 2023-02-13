# *******************************************************************************
# Copyright (c) 2023 Eclipse Foundation and others.
# This program and the accompanying materials are made available
# under the terms of the MIT License
# which is available at https://spdx.org/licenses/MIT.html
# SPDX-License-Identifier: MIT
# *******************************************************************************

import os

from colorama import Style

import utils
from config import OtterdogConfig, OrganizationConfig
from github import Github
from operation import Operation
from organization import load_from_github


class FetchOperation(Operation):
    def __init__(self, config: OtterdogConfig):
        self.config = config
        self.jsonnet_config = self.config.jsonnet_config

    def execute(self, org_config: OrganizationConfig) -> int:
        github_id = org_config.github_id
        credentials = self.config.get_credentials(org_config)
        gh_client = Github(credentials)

        print(f"Organization {Style.BRIGHT}{org_config.name}{Style.RESET_ALL}[id={org_config.github_id}]")

        organization = load_from_github(github_id, gh_client)
        output = organization.write_jsonnet_config(self.jsonnet_config)

        output_dir = self.jsonnet_config.orgs_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        output_file_name = self.jsonnet_config.get_org_config_file(github_id)

        with open(output_file_name, "w") as file:
            file.write(output)

        print(f"  written resource descriptor to '{output_file_name}'")

        return 0
