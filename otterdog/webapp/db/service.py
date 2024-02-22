#  *******************************************************************************
#  Copyright (c) 2024 Eclipse Foundation and others.
#  This program and the accompanying materials are made available
#  under the terms of the Eclipse Public License 2.0
#  which is available at http://www.eclipse.org/legal/epl-v20.html
#  SPDX-License-Identifier: EPL-2.0
#  *******************************************************************************

from __future__ import annotations

from logging import getLogger

from odmantic import query
from odmantic.query import QueryExpression
from quart import current_app

from otterdog.webapp import mongo
from otterdog.webapp.utils import (
    current_utc_time,
    get_otterdog_config,
    get_rest_api_for_app,
)
from otterdog.webapp.webhook.github_models import PullRequest

from .models import (
    ApplyStatus,
    ConfigurationModel,
    InstallationModel,
    InstallationStatus,
    PullRequestModel,
    PullRequestStatus,
    TaskModel,
    TaskStatus,
)

logger = getLogger(__name__)


async def update_installation_status(installation_id: int, action: str) -> None:
    logger.info(f"updating installation status for installation with id '{installation_id}': {action}")

    match action:
        case "created":
            await update_installations()

        case "deleted":
            await update_installations()

        case "suspend":
            installation = await mongo.odm.find_one(
                InstallationModel, InstallationModel.installation_id == installation_id
            )

            if installation is not None:
                installation.installation_status = InstallationStatus.SUSPENDED
                await mongo.odm.save(installation)

        case "unsuspend":
            installation = await mongo.odm.find_one(
                InstallationModel, InstallationModel.installation_id == installation_id
            )

            if installation is not None:
                installation.installation_status = InstallationStatus.INSTALLED
                await mongo.odm.save(installation)

        case _:
            pass


async def update_installations() -> None:
    logger.info("updating all installations")

    rest_api = get_rest_api_for_app()
    otterdog_config = await get_otterdog_config()
    all_configured_organization_names: set[str] = set(otterdog_config.organization_names)
    all_installations = await rest_api.app.get_app_installations()

    async with mongo.odm.session() as session:
        existing_organizations: set[str] = set()
        async for org in session.find(InstallationModel):
            existing_organizations.add(org.github_id)

        for app_installation in all_installations:
            installation_id = app_installation["id"]
            github_id = app_installation["account"]["login"]
            project_name = otterdog_config.get_project_name(github_id)
            suspended_at = app_installation["suspended_at"]
            installation_status = InstallationStatus.INSTALLED if suspended_at is None else InstallationStatus.SUSPENDED

            if project_name is not None:
                org_config = otterdog_config.get_organization_config(project_name)
                config_repo = org_config.config_repo
                base_template = org_config.base_template
                all_configured_organization_names.remove(project_name)
            else:
                project_name = None
                config_repo = None
                base_template = None

            model = InstallationModel(  # type: ignore
                installation_id=installation_id,
                installation_status=installation_status,
                project_name=project_name,
                github_id=github_id,
                config_repo=config_repo,
                base_template=base_template,
            )

            if github_id in existing_organizations:
                existing_organizations.remove(github_id)

            await session.save(model)

        # process organizations that have the GitHub App not installed
        for github_id in existing_organizations:
            project_name = otterdog_config.get_project_name(github_id)
            if project_name is None:
                await session.remove(InstallationModel, InstallationModel.github_id == github_id)
            else:
                existing_model = await mongo.odm.find_one(InstallationModel, InstallationModel.github_id == github_id)

                if existing_model is not None:
                    existing_model.project_name = project_name
                    existing_model.installation_status = InstallationStatus.NOT_INSTALLED
                    await mongo.odm.save(existing_model)

        # finally add all organizations that are in the config but have the app not installed yet
        for name in all_configured_organization_names:
            config = otterdog_config.get_organization_config(name)

            if config is not None:
                model = InstallationModel(  # type: ignore
                    installation_status=InstallationStatus.NOT_INSTALLED,
                    project_name=config.name,
                    github_id=config.github_id,
                    config_repo=config.config_repo,
                    base_template=config.base_template,
                )

                await mongo.odm.save(model)

    for installation in await get_active_installations():
        configuration_model = await get_configuration_by_github_id(installation.github_id)
        if configuration_model is None:
            from otterdog.webapp.tasks.fetch_all_pull_requests import (
                FetchAllPullRequestsTask,
            )
            from otterdog.webapp.tasks.fetch_config import FetchConfigTask

            assert installation.config_repo is not None

            current_app.add_background_task(
                FetchConfigTask(
                    installation.installation_id,
                    installation.github_id,
                    installation.config_repo,
                )
            )

            current_app.add_background_task(
                FetchAllPullRequestsTask(
                    installation.installation_id,
                    installation.github_id,
                    installation.config_repo,
                )
            )


async def get_installation(installation_id: int) -> InstallationModel | None:
    return await mongo.odm.find_one(InstallationModel, InstallationModel.installation_id == installation_id)


async def get_all_installations_count() -> int:
    return await mongo.odm.count(InstallationModel)


async def get_installations() -> list[InstallationModel]:
    return await mongo.odm.find(InstallationModel, sort=InstallationModel.project_name)


async def get_active_installations() -> list[InstallationModel]:
    return await mongo.odm.find(
        InstallationModel, InstallationModel.installation_status == InstallationStatus.INSTALLED
    )


async def get_tasks(limit: int) -> list[TaskModel]:
    return await mongo.odm.find(TaskModel, limit=limit, sort=query.desc(TaskModel.created_at))


async def get_tasks_paged(params: dict[str, str]) -> tuple[list[TaskModel], int]:
    page_index = 1
    page_size = 20
    sort_field = "created_at"
    sort_order = "desc"

    queries: list[QueryExpression] = []

    for k, v in params.items():
        match k:
            case "pageIndex":
                page_index = int(v)
            case "pageSize":
                page_size = int(v)
            case "sortField":
                sort_field = v
            case "sortOrder":
                sort_order = v
            case _:
                if v:
                    queries.append(query.match(TaskModel.__dict__[k], v))

    sort = (
        query.desc(TaskModel.__dict__[sort_field])
        if sort_order == "desc"
        else query.asc(TaskModel.__dict__[sort_field])
    )

    skip = (page_index - 1) * page_size
    return (
        await mongo.odm.find(
            TaskModel,
            *queries,
            skip=skip,
            limit=page_size,
            sort=sort,
        ),
        await mongo.odm.count(TaskModel, *queries),
    )


async def get_configurations() -> list[ConfigurationModel]:
    return await mongo.odm.find(ConfigurationModel)


async def get_configuration_by_github_id(github_id: str) -> ConfigurationModel | None:
    return await mongo.odm.find_one(ConfigurationModel, ConfigurationModel.github_id == github_id)


async def get_configuration_by_project_name(project_name: str) -> ConfigurationModel | None:
    return await mongo.odm.find_one(ConfigurationModel, ConfigurationModel.project_name == project_name)


async def create_task(task: TaskModel) -> None:
    await mongo.odm.save(task)


async def finish_task(task: TaskModel) -> None:
    task.status = TaskStatus.FINISHED
    task.updated_at = current_utc_time()
    await mongo.odm.save(task)


async def fail_task(task: TaskModel, exception: Exception) -> None:
    task.status = TaskStatus.FAILED
    task.updated_at = current_utc_time()
    task.log = str(exception)
    await mongo.odm.save(task)


async def save_config(config: ConfigurationModel) -> None:
    await mongo.odm.save(config)


async def find_pull_request(owner: str, repo: str, pull_request: int) -> PullRequestModel | None:
    return await mongo.odm.find_one(
        PullRequestModel,
        PullRequestModel.org_id == owner,
        PullRequestModel.repo_name == repo,
        PullRequestModel.pull_request == pull_request,
    )


async def update_or_create_pull_request(
    owner: str,
    repo: str,
    pull_request: PullRequest,
    valid: bool | None = None,
    in_sync: bool | None = None,
    requires_manual_apply: bool | None = None,
    apply_status: ApplyStatus | None = None,
) -> None:
    pull_request_status = PullRequestStatus[pull_request.get_pr_status()]

    pr_model = await find_pull_request(owner, repo, pull_request.number)
    if pr_model is None:
        pr_model = PullRequestModel(  # type: ignore
            org_id=owner,
            repo_name=repo,
            pull_request=pull_request.number,
            draft=pull_request.draft,
            status=pull_request_status,
            created_at=pull_request.created_at,
            updated_at=pull_request.updated_at,
            closed_at=pull_request.closed_at,
            merged_at=pull_request.merged_at,
        )
    else:
        pr_model.draft = pull_request.draft
        pr_model.status = pull_request_status
        pr_model.created_at = pull_request.created_at
        pr_model.updated_at = pull_request.updated_at
        pr_model.closed_at = pull_request.closed_at
        pr_model.merged_at = pull_request.merged_at

    if apply_status is not None:
        pr_model.apply_status = apply_status

    if valid is not None:
        pr_model.valid = valid

    if in_sync is not None:
        pr_model.in_sync = in_sync

    if requires_manual_apply is not None:
        pr_model.requires_manual_apply = requires_manual_apply

    await update_pull_request(pr_model)


async def update_pull_request(pull_request: PullRequestModel) -> None:
    await mongo.odm.save(pull_request)


async def get_open_or_incomplete_pull_requests() -> list[PullRequestModel]:
    return await mongo.odm.find(
        PullRequestModel,
        _open_or_incomplete_pull_requests_query(),
    )


async def get_open_or_incomplete_pull_requests_count() -> int:
    return await mongo.odm.count(
        PullRequestModel,
        _open_or_incomplete_pull_requests_query(),
    )


def _open_or_incomplete_pull_requests_query() -> QueryExpression:
    return query.or_(
        PullRequestModel.status == PullRequestStatus.OPEN,
        query.and_(
            PullRequestModel.status == PullRequestStatus.MERGED,
            PullRequestModel.apply_status != ApplyStatus.COMPLETED,
        ),
    )


async def get_merged_pull_requests_count() -> int:
    return await mongo.odm.count(
        PullRequestModel,
        _merged_pull_requests_query(),
    )


def _merged_pull_requests_query() -> QueryExpression:
    return query.and_(
        PullRequestModel.status == PullRequestStatus.MERGED,
        PullRequestModel.apply_status == ApplyStatus.COMPLETED,
    )


async def get_merged_pull_requests_paged(params: dict[str, str]) -> tuple[list[PullRequestModel], int]:
    page_index = 1
    page_size = 20
    sort_field = "merged_at"
    sort_order = "desc"

    queries: list[QueryExpression] = [_merged_pull_requests_query()]

    for k, v in params.items():
        match k:
            case "pageIndex":
                page_index = int(v)
            case "pageSize":
                page_size = int(v)
            case "sortField":
                sort_field = v
            case "sortOrder":
                sort_order = v
            case _:
                if v:
                    queries.append(query.match(PullRequestModel.__dict__[k], v))

    sort = (
        query.desc(PullRequestModel.__dict__[sort_field])
        if sort_order == "desc"
        else query.asc(PullRequestModel.__dict__[sort_field])
    )

    skip = (page_index - 1) * page_size
    return (
        await mongo.odm.find(
            PullRequestModel,
            *queries,
            skip=skip,
            limit=page_size,
            sort=sort,
        ),
        await mongo.odm.count(PullRequestModel, *queries),
    )
