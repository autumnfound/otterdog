# *******************************************************************************
# Copyright (c) 2023 Eclipse Foundation and others.
# This program and the accompanying materials are made available
# under the terms of the MIT License
# which is available at https://spdx.org/licenses/MIT.html
# SPDX-License-Identifier: MIT
# *******************************************************************************

from __future__ import annotations

import dataclasses
from typing import Any, Optional, cast

from jsonbender import S  # type: ignore

from otterdog.models import (
    ModelObject,
    LivePatchHandler,
    LivePatch,
    LivePatchContext,
    LivePatchType,
    ValidationContext,
    FailureType,
)
from otterdog.models.workflow_settings import WorkflowSettings
from otterdog.providers.github import GitHubProvider
from otterdog.utils import Change, is_set_and_valid


@dataclasses.dataclass
class RepositoryWorkflowSettings(WorkflowSettings):
    """
    Represents workflow settings defined on repository level.
    """

    enabled: bool

    @property
    def model_object_name(self) -> str:
        return "repo_workflow_settings"

    def validate(self, context: ValidationContext, parent_object: Any) -> None:
        super().validate(context, parent_object)

        if is_set_and_valid(self.enabled) and self.enabled is True:
            from .github_organization import GitHubOrganization

            org_workflow_settings = cast(GitHubOrganization, context.root_object).settings.workflows

            if org_workflow_settings.enabled_repositories == "none" and self.enabled is True:
                context.add_failure(
                    FailureType.ERROR,
                    f"{self.get_model_header(parent_object)} has enabled workflows, "
                    f"while on organization level it disabled for all repositories.",
                )

            if (
                org_workflow_settings.default_workflow_permissions == "read"
                and self.default_workflow_permissions == "write"
            ):
                context.add_failure(
                    FailureType.ERROR,
                    f"{self.get_model_header(parent_object)} has 'default_workflow_permissions' of value "
                    f"'{self.default_workflow_permissions}', "
                    f"while on organization level it is restricted to "
                    f"'{org_workflow_settings.default_workflow_permissions}'.",
                )

    def include_field_for_diff_computation(self, field: dataclasses.Field) -> bool:
        if self.enabled is False:
            if field.name == "enabled":
                return True
            else:
                return False

        return super().include_field_for_diff_computation(field)

    @classmethod
    def get_mapping_to_provider(cls, org_id: str, data: dict[str, Any], provider: GitHubProvider) -> dict[str, Any]:
        if "enabled" in data and data["enabled"] is False:
            return {"enabled": S("enabled")}
        else:
            return super().get_mapping_to_provider(org_id, data, provider)

    @classmethod
    def generate_live_patch(
        cls,
        expected_object: Optional[ModelObject],
        current_object: Optional[ModelObject],
        parent_object: Optional[ModelObject],
        context: LivePatchContext,
        handler: LivePatchHandler,
    ) -> None:
        assert isinstance(expected_object, RepositoryWorkflowSettings)

        if current_object is None:
            handler(LivePatch.of_addition(expected_object, parent_object, expected_object.apply_live_patch))
            return

        assert isinstance(current_object, RepositoryWorkflowSettings)

        modified_workflow_settings: dict[str, Change[Any]] = expected_object.get_difference_from(current_object)

        # FIXME: needed to add this hack to ensure that enabled is also present in
        #        the modified data as GitHub has made this property required.
        if "allowed_actions" in modified_workflow_settings:
            modified_workflow_settings["enabled"] = Change(expected_object.enabled, expected_object.enabled)

        if len(modified_workflow_settings) > 0:
            handler(
                LivePatch.of_changes(
                    expected_object,
                    current_object,
                    modified_workflow_settings,
                    parent_object,
                    False,
                    cls.apply_live_patch,
                )
            )

    @classmethod
    def apply_live_patch(cls, patch: LivePatch, org_id: str, provider: GitHubProvider) -> None:
        from .repository import Repository

        assert isinstance(patch.parent_object, Repository)

        match patch.patch_type:
            case LivePatchType.ADD:
                assert isinstance(patch.expected_object, RepositoryWorkflowSettings)
                provider.update_repo_workflow_settings(
                    org_id, patch.parent_object.name, patch.expected_object.to_provider_data(org_id, provider)
                )

            case LivePatchType.CHANGE:
                assert patch.changes is not None
                github_settings = cls.changes_to_provider(org_id, patch.changes, provider)
                provider.update_repo_workflow_settings(org_id, patch.parent_object.name, github_settings)

            case _:
                raise RuntimeError(f"unexpected patch type '{patch.patch_type}'")
