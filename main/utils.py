from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, AsyncGenerator
from urllib.parse import quote_from_bytes

from main.main import GITHUB_ASSISTANCE_MSG, MAX_SLEEP, needs_github_assistance, failed, deleted

if TYPE_CHECKING:
    from httpx import Response, AsyncClient


async def wait_for_rate_limit(*, response: Response, eligible_for_secondary_limit: bool = False) -> None:
    """
    Sleeps or terminates the workflow if we've hit rate limits.

    See docs on rate limits: https://docs.github.com/en/rest/rate-limit?apiVersion=2022-11-28.
    """
    if int(response.headers['x-ratelimit-remaining']) == 0:
        ratelimit_reset = datetime.fromtimestamp(int(response.headers['x-ratelimit-reset']))
        delta = ratelimit_reset - datetime.now()

        if delta > timedelta(seconds=MAX_SLEEP):
            print(
                f'Rate limited for {delta} seconds. '
                f'Terminating workflow, since that\'s above the maximum allowed sleep time. '
                f'Retry the job manually, when the rate limit is refreshed.'
            )
            exit(1)
        elif delta > timedelta(seconds=0):
            print(f'Rate limit exceeded. Sleeping for {delta} seconds')
            await asyncio.sleep(delta.total_seconds())

    elif eligible_for_secondary_limit:
        # https://docs.github.com/en/rest/guides/best-practices-for-integrators#dealing-with-secondary-rate-limits
        await asyncio.sleep(1)


async def get_all_pages(*, url: str, http_client: AsyncClient) -> AsyncGenerator[dict, None]:
    """
    Accumulate all pages of a paginated API endpoint.

    :param url: The full API URL
    :param http_client: HTTP client.
    :return: List of objects.
    """
    rel_regex = re.compile(r'<([^<>]*)>; rel="(\w+)"')
    rels = {'next': url}

    while 'next' in rels:
        response = await http_client.get(rels['next'])
        response.raise_for_status()

        for item in response.json():
            yield item

        rels = {rel: url for url, rel in rel_regex.findall(response.headers['link'])}

        await wait_for_rate_limit(response=response)


def post_deletion_output(*, response: Response, image_name: str, version_id: int) -> None:
    """
    Output a little info to the user.
    """
    image_name_with_tag = f'{image_name}:{version_id}'
    if response.is_error:
        if response.status_code == 400 and response.json()['message'] == GITHUB_ASSISTANCE_MSG:
            # Output the names of these images in one block at the end
            needs_github_assistance.append(image_name_with_tag)
        else:
            failed.append(image_name_with_tag)
            print(
                f"\nCouldn't delete {image_name_with_tag}.\n"
                f'Status code: {response.status_code}\nResponse: {response.json()}\n'
            )
    else:
        deleted.append(image_name_with_tag)
        print(f'Deleted old image: {image_name_with_tag}')


def encode_image_name(name: str) -> str:
    return quote_from_bytes(name.strip().encode('utf-8'), safe='')
