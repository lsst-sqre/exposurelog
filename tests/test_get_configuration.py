import pathlib
import unittest

from exposurelog.shared_state import get_shared_state
from exposurelog.testutils import assert_good_response, create_test_client


class GetConfigurationTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_one_butler(self) -> None:
        repo_path = pathlib.Path(__file__).parent / "data" / "LSSTCam"
        async with create_test_client(repo_path=repo_path, num_messages=0) as (
            client,
            messages,
        ):
            shared_state = get_shared_state()
            for suffix in ("", "/"):
                response = await client.get(
                    "/exposurelog/configuration" + suffix
                )
                data = assert_good_response(response)
                assert data["site_id"] == shared_state.site_id
                # Note: the SharedState butler_uri_{n} attributes are set
                # programmatically, so mypy does not know about them.
                assert data["butler_uri_1"] == shared_state.butler_uri_1  # type: ignore
                assert data["butler_uri_2"] == ""
                assert data["butler_uri_3"] == ""

    async def test_two_butlers(self) -> None:
        repo_path = pathlib.Path(__file__).parent / "data" / "LSSTCam"
        repo_path_3 = pathlib.Path(__file__).parent / "data" / "LATISS"
        async with create_test_client(
            repo_path=repo_path,
            repo_path_3=repo_path_3,
        ) as (
            client,
            messages,
        ):
            shared_state = get_shared_state()
            assert len(shared_state.butler_factory.repositories) == 2
            response = await client.get("/exposurelog/configuration")
            data = assert_good_response(response)
            assert data["site_id"] == shared_state.site_id
            # Note: the SharedState butler_uri_{n} attributes are set
            # programmatically, so mypy does not know about them.
            assert data["butler_uri_1"] == shared_state.butler_uri_1  # type: ignore
            assert data["butler_uri_2"] == ""
            assert data["butler_uri_3"] == shared_state.butler_uri_3  # type: ignore
