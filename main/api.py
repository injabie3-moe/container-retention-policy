from __future__ import annotations

from asyncio import Semaphore
from typing import AsyncGenerator, TypeAlias

from httpx import AsyncClient, TimeoutException

from main.main import BASE_URL
from main.models import PackageResponse, PackageVersionResponse, AccountType
from main.utils import get_all_pages, wait_for_rate_limit, post_deletion_output, encode_image_name

# PackageResponse generator
PRG: TypeAlias = AsyncGenerator[PackageResponse, None]

# PackageVersionResponse
PVRG: TypeAlias = AsyncGenerator[PackageVersionResponse, None]


async def list_org_packages(*, org_name: str, http_client: AsyncClient) -> PRG:
    """List all packages for an organization."""
    url = f'{BASE_URL}/orgs/{org_name}/packages?package_type=container&per_page=100'
    async for package in get_all_pages(url=url, http_client=http_client):
        yield PackageResponse(**package)


async def list_packages(*, http_client: AsyncClient) -> PRG:
    """List all packages for a user."""
    url = f'{BASE_URL}/user/packages?package_type=container&per_page=100'
    async for package in get_all_pages(url=url, http_client=http_client):
        yield PackageResponse(**package)


async def list_org_package_versions(*, org_name: str, image_name: str, http_client: AsyncClient) -> PVRG:
    """List image versions for an organization."""
    url = f'{BASE_URL}/orgs/{org_name}/packages/container/{encode_image_name(image_name)}/versions?per_page=100'
    async for package_version in get_all_pages(url=url, http_client=http_client):
        yield PackageVersionResponse(**package_version)


async def list_package_versions(*, image_name: str, http_client: AsyncClient) -> PVRG:
    """List image versions for a user."""
    url = f'{BASE_URL}/user/packages/container/{encode_image_name(image_name)}/versions?per_page=100'
    async for package_version in get_all_pages(url=url, http_client=http_client):
        yield PackageVersionResponse(**package_version)


async def _delete_package_versions(url: str, semaphore: Semaphore, http_client: AsyncClient, image_name: str, version_id: int) -> None:
    async with semaphore:
        try:
            response = await http_client.delete(url)
            await wait_for_rate_limit(response=response, eligible_for_secondary_limit=True)
            post_deletion_output(response=response, image_name=image_name, version_id=version_id)
        except TimeoutException as e:
            print(f'Request to delete {image_name} timed out with error `{e}`')


async def delete_org_package_versions(*, org_name: str, image_name: str, version_id: int, http_client: AsyncClient,
                                      semaphore: Semaphore) -> None:
    """Delete an image version for an organization."""
    url = f'{BASE_URL}/orgs/{org_name}/packages/container/{encode_image_name(image_name)}/versions/{version_id}'
    await _delete_package_versions(url=url, semaphore=semaphore, http_client=http_client, image_name=image_name, version_id=version_id)


async def delete_package_versions(*, image_name: str, version_id: int, http_client: AsyncClient, semaphore: Semaphore) -> None:
    """Delete an image version for a user."""
    url = f'{BASE_URL}/user/packages/container/{encode_image_name(image_name)}/versions/{version_id}'
    await _delete_package_versions(url=url, semaphore=semaphore, http_client=http_client, image_name=image_name, version_id=version_id)


class GithubAPI:
    """
    Provide a unified API, regardless of account type.
    """

    @staticmethod
    async def list_packages(*, account_type: AccountType, org_name: str | None, http_client: AsyncClient) -> PRG:
        if account_type != AccountType.ORG:
            generator = list_packages(http_client=http_client)
        else:
            assert isinstance(org_name, str)
            generator = list_org_packages(org_name=org_name, http_client=http_client)

        async for package in generator:
            yield package

    @staticmethod
    async def list_package_versions(*, account_type: AccountType, org_name: str | None, image_name: str, http_client: AsyncClient) -> PVRG:
        if account_type != AccountType.ORG:
            generator = list_package_versions(image_name=image_name, http_client=http_client)
        else:
            assert isinstance(org_name, str)
            generator = list_org_package_versions(org_name=org_name, image_name=image_name, http_client=http_client)

        async for package_version in generator:
            yield package_version

    @staticmethod
    async def delete_package(*, account_type: AccountType, org_name: str | None, image_name: str, version_id: int, http_client: AsyncClient, semaphore: Semaphore) -> None:
        if account_type != AccountType.ORG:
            return await delete_package_versions(image_name=image_name, version_id=version_id, http_client=http_client, semaphore=semaphore)
        else:
            assert isinstance(org_name, str)
            return await delete_org_package_versions(org_name=org_name, image_name=image_name, version_id=version_id, http_client=http_client, semaphore=semaphore)
