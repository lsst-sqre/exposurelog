import http
import pathlib
import random
import unittest

import astropy.time
import httpx
import lsst.daf.butler

from exposurelog.shared_state import get_shared_state
from exposurelog.testutils import (
    TEST_TAGS,
    TEST_URLS,
    MessageDictT,
    assert_good_response,
    create_test_client,
)


def assert_good_add_response(
    response: httpx.Response, add_args: dict
) -> MessageDictT:
    """Check the response from a successful add_messages request.

    Parameters
    ----------
    response
        Response to HTTP request.
    add_args:
        Arguments to add_message.

    Returns
    -------
    message
        The message added.
    """
    message = assert_good_response(response)
    assert message["is_valid"]
    assert message["parent_id"] is None
    assert message["date_invalidated"] is None
    for key, value in add_args.items():
        if key == "is_new":
            continue  # Not part of the message
        assert message[key] == add_args[key]
    return message


def find_all_exposures(
    registry: lsst.daf.butler.Registry, instrument: str
) -> list[lsst.daf.butler.DimensionRecord]:
    """Find all exposures in the specified registry.

    Parameters
    ----------
    registry : lsst.daf.butler.Registry
        The butler registry.
    instrument : str
        The instrument.
    """
    record_iter = registry.queryDimensionRecords(
        "exposure",
        instrument=instrument,
        bind={},
        where="",
    )
    return list(record_iter)


class AddMessageTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_add_message(self) -> None:
        repo_path = pathlib.Path(__file__).parent / "data" / "LSSTCam"
        repo_path_2 = pathlib.Path(__file__).parent / "data" / "LATISS"

        async with create_test_client(
            repo_path=repo_path, repo_path_2=repo_path_2, num_messages=0
        ) as (
            client,
            messages,
        ):
            shared_state = get_shared_state()
            exposures = []
            for repository, instrument in zip(
                shared_state.butler_factory.repositories, ("LSSTCam", "LATISS")
            ):
                butler = shared_state.butler_factory.get_butler(repository)
                exposures += find_all_exposures(
                    registry=butler.registry, instrument=instrument
                )

            # Add a message whose obs_id matches an exposure
            # and with all test tags and URLs in random order.
            shuffled_test_tags = TEST_TAGS[:]
            random.shuffle(shuffled_test_tags)
            shuffled_test_urls = TEST_URLS[:]
            random.shuffle(shuffled_test_urls)
            add_args = dict(
                obs_id=exposures[0].obs_id,
                instrument=exposures[0].instrument,
                message_text="A sample message",
                level=10,
                tags=shuffled_test_tags,
                urls=shuffled_test_urls,
                user_id="test_add_message",
                user_agent="pytest",
                is_human=False,
                is_new=False,
                exposure_flag="none",
            )
            for suffix in ("", "/"):
                response = await client.post(
                    "/exposurelog/messages" + suffix, json=add_args
                )
                assert_good_add_response(response=response, add_args=add_args)

            # Add a message whose obs_id does not match an exposure,
            # and ``is_new=True``. This should succeed, with data_added = now.
            for exposure in exposures:
                current_time = astropy.time.Time.now()
                new_add_args = add_args.copy()
                new_add_args["instrument"] = exposure.instrument
                new_add_args["obs_id"] = exposure.obs_id
                response = await client.post(
                    "/exposurelog/messages",
                    json=new_add_args,
                )
                message = assert_good_add_response(
                    response=response, add_args=new_add_args
                )
                assert message["date_added"] > current_time.tai.isot

            # Error: add a message whose obs_id does not match an exposure
            # and ``is_new=False``.
            no_obs_id_args = add_args.copy()
            no_obs_id_args["obs_id"] = "No such obs_id"
            response = await client.post(
                "/exposurelog/messages",
                json=no_obs_id_args,
            )
            assert response.status_code == http.HTTPStatus.NOT_FOUND

            # Error: add a message with the wrong instrument.
            wrong_instrument_args = add_args.copy()
            instrument = {
                "LATISS": "LSSTCam",
                "LSSTCam": "LATISS",
            }[wrong_instrument_args["instrument"]]
            wrong_instrument_args["instrument"] = "No such instrument"
            response = await client.post(
                "/exposurelog/messages",
                json=wrong_instrument_args,
            )
            assert response.status_code == http.HTTPStatus.NOT_FOUND

            # Error: add a message with invalid tags.
            invalid_tags = [
                "not valid",
                "also=not=valid",
                "again?",
            ]
            for num_invalid_tags in range(1, len(invalid_tags)):
                for num_valid_tags in range(2):
                    some_valid_tags = random.sample(TEST_TAGS, num_valid_tags)
                    some_invalid_tags = random.sample(
                        invalid_tags, num_invalid_tags
                    )
                    tags_list = some_valid_tags + some_invalid_tags
                    random.shuffle(tags_list)
                    bad_tags_args = add_args.copy()
                    bad_tags_args["tags"] = tags_list
                    response = await client.post(
                        "/exposurelog/messages",
                        json=bad_tags_args,
                    )
                    assert response.status_code == http.HTTPStatus.BAD_REQUEST

            # Error: add a message that is missing a required parameter.
            # This is a schema violation. The error code is 422,
            # but I have not found that documented,
            # so accept anything in the 400s.
            optional_fields = frozenset(
                ["level", "tags", "urls", "exposure_flag", "is_new"]
            )
            for key in add_args:
                if key in optional_fields:
                    continue
                bad_add_args = add_args.copy()
                del bad_add_args[key]
                response = await client.post(
                    "/exposurelog/messages", json=bad_add_args
                )
                assert 400 <= response.status_code < 500
