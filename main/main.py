from __future__ import annotations

import asyncio
import os
from asyncio import Semaphore, Task
from fnmatch import fnmatch

from httpx import AsyncClient

from main.api import GithubAPI
from main.models import Inputs
from main.utils import filter_image_names

BASE_URL = 'https://api.github.com'

deleted: list[str] = []
failed: list[str] = []
needs_github_assistance: list[str] = []

GITHUB_ASSISTANCE_MSG = (
    'Publicly visible package versions with more than '
    '5000 downloads cannot be deleted. '
    'Contact GitHub support for further assistance.'
)

# This could be made into a setting if needed
MAX_SLEEP = 60 * 10  # 10 minutes


async def select_package_versions_to_delete(*, image_name: str, inputs: Inputs, semaphore: Semaphore, http_client: AsyncClient) -> list[Task]:
    index = 0
    tasks = []

    async for version in GithubAPI.list_package_versions(
            account_type=inputs.account_type,
            org_name=inputs.org_name,
            image_name=image_name,
            http_client=http_client
    ):
        # Skip deleting initial package versions, if specified in the action inputs
        if 0 < inputs.keep_at_least == index:
            continue

        # Parse either the update-at timestamp, or the created-at timestamp depending on which was specified
        updated_or_created_at = getattr(version, inputs.timestamp_to_use.value)

        if not updated_or_created_at:
            print(f'Skipping image version {version.id}. Unable to parse timestamps.')
            continue

        if inputs.cut_off < updated_or_created_at:
            # Skipping because it's above our datetime cut-off
            # we're only looking to delete containers older than some timestamp
            continue

        # Load the tags for the individual image we're processing
        if (
                hasattr(version, 'metadata')
                and hasattr(version.metadata, 'container')
                and hasattr(version.metadata.container, 'tags')
        ):
            image_tags = version.metadata.container.tags
        else:
            image_tags = []

        if inputs.untagged_only and image_tags:
            # Skipping because no tagged images should be deleted
            # We could proceed if image_tags was empty, but it's not
            continue

        if not image_tags and not inputs.filter_include_untagged:
            # Skipping, because the filter_include_untagged setting is False
            continue

        delete_image = not inputs.filter_tags

        for filter_tag in inputs.filter_tags:
            # One thing to note here is that we use fnmatch to support wildcards.
            # A filter-tags setting of 'some-tag-*' should match to both
            # 'some-tag-1' and 'some-tag-2'.
            if any(fnmatch(tag, filter_tag) for tag in image_tags):
                delete_image = True
                break

        for skip_tag in inputs.skip_tags:
            if any(fnmatch(tag, skip_tag) for tag in image_tags):
                # Skipping because this image version is tagged with a protected tag
                delete_image = False

        if delete_image:
            tasks.append(asyncio.create_task(
                GithubAPI.delete_package(
                    account_type=inputs.account_type,
                    org_name=inputs.org_name,
                    image_name=image_name,
                    version_id=version.id,
                    http_client=http_client,
                    semaphore=semaphore,
                )
            )
            )

        index += 1

    return tasks


async def get_and_delete_old_package_versions(image_name: str, inputs: Inputs, http_client: AsyncClient, semaphore: Semaphore) -> None:
    """Delete old package versions for an image name."""
    # Create list of tasks for versions to delete concurrently
    tasks = await select_package_versions_to_delete(image_name=image_name, inputs=inputs, semaphore=semaphore, http_client=http_client)

    if not tasks:
        print(f'No more versions to delete for {image_name}')

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for item in results:
        if isinstance(item, Exception):
            try:
                raise item
            except Exception as e:
                # Unhandled errors *shouldn't* occur
                print(
                    f'Unhandled exception raised at runtime: `{e}`. '
                    f'Please report this at https://github.com/snok/container-retention-policy/issues/new'
                )


async def main(
        account_type: str, org_name: str, image_names: str, timestamp_to_use: str, cut_off: str, token: str,
        untagged_only: str, skip_tags: str, keep_at_least: str, filter_tags: str, filter_include_untagged: str,
) -> None:
    """
    Delete old image versions.

    See action.yml for additional descriptions of each parameter.

    The argument order matters. They are fed to the script from the action, in order.

    All arguments are either strings or empty strings. We properly
    parse types and values in the Inputs pydantic model.

    :param account_type: Account type. must be 'org' or 'personal'.
    :param org_name: The name of the org. Required if account type is 'org'.
    :param image_names: The image names to delete versions for. Can be a single
                        image name, or multiple comma-separated image names.
    :param timestamp_to_use: Which timestamp to base our cut-off on. Can be 'updated_at' or 'created_at'.
    :param cut_off: Can be a human-readable relative time like '2 days ago UTC', or a timestamp.
                            Must contain a reference to the timezone.
    :param token: The personal access token to authenticate with.
    :param untagged_only: Whether to only delete untagged images.
    :param skip_tags: Comma-separated list of tags to not delete.
        Supports wildcard '*', '?', '[seq]' and '[!seq]' via Unix shell-style wildcards
    :param keep_at_least: Number of images to always keep
    :param filter_tags: Comma-separated list of tags to consider for deletion.
        Supports wildcard '*', '?', '[seq]' and '[!seq]' via Unix shell-style wildcards
    :param filter_include_untagged: Whether to consider untagged images for deletion.
    """
    inputs = Inputs(
        image_names=image_names, account_type=account_type, org_name=org_name, timestamp_to_use=timestamp_to_use,
        cut_off=cut_off, untagged_only=untagged_only, skip_tags=skip_tags, keep_at_least=keep_at_least,
        filter_tags=filter_tags, filter_include_untagged=filter_include_untagged,
    )

    semaphore = Semaphore(50)

    async with AsyncClient(headers={'accept': 'application/vnd.github.v3+json', 'Authorization': f'Bearer {token}'}) as client:

        tasks = []

        GithubAPI.list_packages(account_type=inputs.account_type, org_name=inputs.org_name, http_client=client).__aiter__().

        # Iterate over each package in the user or organization's repo
        async for package in GithubAPI.list_packages(account_type=inputs.account_type, org_name=inputs.org_name, http_client=client):

            # Iterate over each image name from inputs
            for image_name in inputs.image_names:

                # If the package matches the image name, add a task for deleting its old image versions
                if fnmatch(package.name, image_name):
                    tasks.append(asyncio.create_task(get_and_delete_old_package_versions(image_name, inputs, client, semaphore)))

        # Delete image versions concurrently for each image name
        await asyncio.gather(*tasks)

    if needs_github_assistance:
        # Print a human-readable list of public images we couldn't handle
        print('\n')
        print('─' * 110)
        image_list = '\n\t- ' + '\n\t- '.join(needs_github_assistance)
        msg = (
            '\nThe follow images are public and have more than 5000 downloads. '
            f'These cannot be deleted via the Github API:\n{image_list}\n\n'
            f'If you still want to delete these images, contact Github support.\n\n'
            'See https://docs.github.com/en/rest/reference/packages for more info.\n'
        )
        print(msg)
        print('─' * 110)

    # Then add it to the action outputs
    for name, l in [
        ('needs-github-assistance', needs_github_assistance),
        ('deleted', deleted),
        ('failed', failed),
    ]:
        comma_separated_list = ','.join(l)

        with open(os.environ['GITHUB_ENV'], 'a') as f:
            f.write(f'{name}={comma_separated_list}')
