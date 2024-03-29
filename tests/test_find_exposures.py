import collections.abc
import http
import itertools
import pathlib
import random
import typing
import unittest

import httpx
import lsst.daf.butler

from exposurelog.exposure import EXPOSURE_ORDER_BY_VALUES
from exposurelog.routers.find_exposures import dict_from_exposure
from exposurelog.shared_state import get_shared_state
from exposurelog.testutils import (
    AssertDataDictsOrdered,
    ExposureDictT,
    assert_good_response,
    cast_special,
    create_test_client,
)


class doc_str:
    """Decorator to add a doc string to a function.

    Unlike the standard technique, this works with f strings
    """

    def __init__(self, doc: str):
        self.doc = doc

    def __call__(
        self, func: collections.abc.Callable
    ) -> collections.abc.Callable:
        func.__doc__ = self.doc
        return func


def assert_good_find_response(
    response: httpx.Response,
    exposures: collections.abc.Iterable[ExposureDictT],
    predicate: collections.abc.Callable,
) -> list[ExposureDictT]:
    """Assert that the correct exposures were found.

    Parameters
    ----------
    response
        Response from find_exposures command.
    exposures
        All exposures in the database (in any order).
    predicate
        Callable that takes one exposure and returns True if a exposure
        meets the find criteria, False if not.

    Returns
    found_exposures
        The found exposures.
    """
    found_exposures = assert_good_response(response)
    for exposure in found_exposures:
        assert predicate(
            exposure
        ), f"exposure {exposure} does not match {predicate.__doc__}"
    missing_exposures = get_missing_exposure(exposures, found_exposures)
    for exposure in missing_exposures:
        assert not predicate(
            exposure
        ), f"exposure {exposure} matches {predicate.__doc__}"
    return found_exposures


assert_exposures_ordered = AssertDataDictsOrdered(data_name="exposure")


def get_range_values(
    exposures: list[ExposureDictT], field: str
) -> tuple[float, float]:
    values = sorted(exposure[field] for exposure in exposures)
    assert len(values) >= 4, f"not enough values for {field}"
    min_value = values[1]
    max_value = values[-1]
    if max_value == min_value:
        assert isinstance(max_value, int)
        max_value = min_value + 1
    return min_value, max_value


def get_missing_exposure(
    exposures: collections.abc.Iterable[ExposureDictT],
    found_exposures: collections.abc.Iterable[ExposureDictT],
) -> list[ExposureDictT]:
    """Get exposures that were not found."""
    found_ids = set(
        found_exposure["obs_id"] for found_exposure in found_exposures
    )
    return [
        exposure
        for exposure in exposures
        if str(exposure["obs_id"]) not in found_ids
    ]


class FindExposuresTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_find_exposures_one_registry(self) -> None:
        instrument = "LATISS"
        repo_path = pathlib.Path(__file__).parent / "data" / instrument

        # Find all exposures in the registry, and save as a list of dicts.
        butler = lsst.daf.butler.Butler.from_config(str(repo_path))
        registry = butler.registry
        exposure_iter = registry.queryDimensionRecords(
            "exposure",
            instrument=instrument,
        )
        exposures = [
            dict_from_exposure(exposure) for exposure in exposure_iter
        ]
        exposures.sort(key=lambda exposure: exposure["id"])

        # Check for duplicate exposures.
        obs_ids = {exposure["obs_id"] for exposure in exposures}
        assert len(obs_ids) == len(exposures)

        # Make sure we got some exposures -- enough to select a subset
        # and pick a subrange.
        assert len(exposures) > 5

        # Make sure all exposures have the right instrument.
        for exposure in exposures:
            assert exposure["instrument"] == instrument

        async with create_test_client(repo_path=repo_path) as (
            client,
            messages,
        ):
            # Test that instrument is required.
            response = await client.get(
                "/exposurelog/exposures",
                params={"limit": 1},
            )
            assert response.status_code == 422

            async def run_find(
                find_args: dict[str, typing.Any],
                instrument: str = instrument,
            ) -> httpx.Response:
                """Run a query after adding instrument parameter."""
                full_find_args = find_args.copy()
                full_find_args["instrument"] = instrument
                response = await client.get(
                    "/exposurelog/exposures",
                    params=full_find_args,
                )
                return response

            # Make a list of find arguments and associated predicates.
            # Each entry is a tuple of:
            # * dict of find arg name: value
            # * predicate: function that takes an exposure dict
            #   and returns True if the exposure matches the query
            find_args_predicates: list[
                tuple[dict[str, typing.Any], collections.abc.Callable]
            ] = list()

            # Range arguments: min_<field>, max_<field>.
            for field in ("day_obs", "seq_num", "date"):
                min_name = f"min_{field}"
                max_name = f"max_{field}"

                if field == "date":
                    # min_date and max_date need special handling, because
                    # they are compared to a time span, rather than a scalar.
                    # The match rules are: timespan_end > min_date
                    # and timespan_beg <= max_date. This matches how
                    # daf_butler's Registry performs a timespan overlap search.
                    min_field = "timespan_end"
                    max_field = "timespan_begin"
                    min_value, __ = get_range_values(
                        exposures=exposures, field=min_field
                    )
                    _, max_value = get_range_values(
                        exposures=exposures, field=max_field
                    )

                    @doc_str(f"exposure[{min_field!r}] > {min_value}.")
                    def test_min(
                        exposure: ExposureDictT,
                        field: str = min_field,
                        min_value: typing.Any = min_value,
                    ) -> bool:
                        min_value = cast_special(min_value)
                        value = cast_special(exposure[field])
                        return value > min_value

                    @doc_str(f"exposure[{max_field!r}] <= {max_value}.")
                    def test_max(
                        exposure: ExposureDictT,
                        field: str = max_field,
                        max_value: typing.Any = max_value,
                    ) -> bool:
                        max_value = cast_special(max_value)
                        value = cast_special(exposure[field])
                        return value <= max_value

                else:
                    min_field = field
                    max_field = field
                    min_value, max_value = get_range_values(
                        exposures=exposures, field=field
                    )

                    @doc_str(f"exposure[{min_field!r}] >= {min_value}.")
                    def test_min(
                        exposure: ExposureDictT,
                        field: str = min_field,
                        min_value: typing.Any = min_value,
                    ) -> bool:
                        min_value = cast_special(min_value)
                        value = cast_special(exposure[field])
                        return value >= min_value

                    @doc_str(f"exposure[{max_field!r}] < {max_value}.")
                    def test_max(
                        exposure: ExposureDictT,
                        field: str = max_field,
                        max_value: typing.Any = max_value,
                    ) -> bool:
                        max_value = cast_special(max_value)
                        value = cast_special(exposure[field])
                        return value < max_value

                find_args_predicates += [
                    ({min_name: min_value}, test_min),
                    ({max_name: max_value}, test_max),
                ]

                # Test that an empty range (max <= min) returns no exposures.
                # There is no point combining this with other tests,
                # so test it now instead of adding it to find_args_predicates.
                empty_range_args = {
                    min_name: min_value,
                    max_name: min_value,
                }
                response = await run_find(empty_range_args)
                found_exposures = assert_good_response(response)
                assert len(found_exposures) == 0

            # Collection arguments: <field>s, with a list of allowed values.
            num_to_find = 2
            for field in (
                "group_name",
                "observation_reason",
                "observation_type",
            ):
                exposures_to_find = random.sample(exposures, num_to_find)
                values = [exposure[field] for exposure in exposures_to_find]

                @doc_str(f"exposure[{field!r}] in {values}")
                def test_collection(
                    exposure: ExposureDictT,
                    field: str = field,
                    values: list[typing.Any] = values,
                ) -> bool:
                    return exposure[field] in values

                find_args_predicates.append(
                    ({f"{field}s": values}, test_collection)
                )

            # Test single requests: one entry from find_args_predicates.
            for find_args, predicate in find_args_predicates:
                response = await run_find(find_args)
                assert_good_find_response(response, exposures, predicate)

            # Test pairs of requests: two entries from find_args_predicates,
            # which are ``and``-ed together.
            for (
                (find_args1, predicate1),
                (find_args2, predicate2),
            ) in itertools.product(find_args_predicates, find_args_predicates):
                find_args = find_args1.copy()
                find_args.update(find_args2)
                if len(find_args) < len(find_args1) + len(find_args):
                    # Overlapping arguments makes the predicates invalid.
                    continue

                @doc_str(f"{predicate1.__doc__} and {predicate2.__doc__}")
                def and_predicates(
                    exposure: ExposureDictT,
                    predicate1: collections.abc.Callable,
                    predicate2: collections.abc.Callable,
                ) -> bool:
                    return predicate1(exposure) and predicate2(exposure)

                response = await run_find(find_args)
                assert_good_find_response(response, exposures, and_predicates)

            # Test that find with no arguments finds all exposures.
            response = await run_find(dict())
            assert_good_find_response(
                response, exposures, lambda exposure: True
            )

            # Test that limit limits the number of records.
            for limit in (
                1,
                len(exposures) - 3,
                len(exposures),
                len(exposures) + 3,
            ):
                offset = 0
                while True:
                    response = await run_find(dict(offset=offset, limit=limit))
                    found_exposures = assert_good_response(response)
                    num_found = len(found_exposures)
                    assert num_found <= limit
                    if num_found < limit:
                        assert offset + num_found == len(exposures)
                    for i in range(offset, offset + num_found):
                        assert (
                            found_exposures[i]["obs_id"]
                            == exposures[i]["obs_id"]
                        )
                    if len(found_exposures) <= limit:
                        break
                    offset += limit

            # Test minimal order_by.
            order_by = ["-id"]
            response = await run_find(dict(order_by=order_by))
            found_exposures = assert_good_find_response(
                response, reversed(exposures), predicate=lambda exposure: True
            )
            print("exposure 1 =", found_exposures[0])
            assert_exposures_ordered(
                data_dicts=found_exposures, order_by=order_by
            )

            # group_name is not sufficient (there are duplicates)
            # but the service appends "id" if "id" if not specified.
            response = await run_find(dict(order_by=["group_name"]))
            exposures.sort(
                key=lambda exposure: (exposure["group_name"], exposure["id"])
            )
            assert_good_find_response(
                response, exposures, predicate=lambda exposure: True
            )

            # Now check group_name with -id to make sure the service
            # is not appending id after the -id.
            response = await run_find(dict(order_by=["group_name", "-id"]))
            exposures.sort(
                key=lambda exposure: (exposure["group_name"], -exposure["id"])
            )
            assert_good_find_response(
                response, exposures, predicate=lambda exposure: True
            )

            # Test that offset >= # of records returns nothing.
            response = await run_find(dict(limit=10, offset=len(exposures)))
            found_exposures = assert_good_response(response)
            assert len(found_exposures) == 0

            # Test that limit must be positive.
            response = await run_find({"limit": 0})
            assert response.status_code == 422

            # Test that offset must not be negative.
            response = await run_find({"limit": -1})
            assert response.status_code == 422

            # Test order_by with all records.
            for order_by_field in EXPOSURE_ORDER_BY_VALUES:
                order_by = [order_by_field]
                response = await run_find(dict(order_by=[order_by_field]))
                found_exposures = assert_good_response(response)
                assert_exposures_ordered(
                    data_dicts=found_exposures, order_by=order_by
                )

            # Check invalid order_by fields.
            for bad_order_by_field in ("not_a_field", "+id"):
                response = await run_find(dict(order_by=[bad_order_by_field]))
                assert response.status_code == http.HTTPStatus.BAD_REQUEST

    async def test_find_exposures_two_registries(self) -> None:
        """Test find_exposures with a server that has two repositories."""
        repo_path = pathlib.Path(__file__).parent / "data" / "LSSTCam"
        repo_path_2 = pathlib.Path(__file__).parent / "data" / "LATISS"
        instrument = "LATISS"

        # The first repo is for LSSTCam and the second for LATISS,
        # thus searches only return exposures from one registry.
        # Use instrument=LATISS to search the second registry
        # in order to test DM-33601.
        butler = lsst.daf.butler.Butler.from_config(str(repo_path_2))
        registry = butler.registry
        exposure_iter = registry.queryDimensionRecords(
            "exposure",
            instrument=instrument,
        )
        exposures = [
            dict_from_exposure(exposure) for exposure in exposure_iter
        ]
        exposures.sort(key=lambda exposure: exposure["id"])

        # Check for duplicate exposures.
        obs_ids = {exposure["obs_id"] for exposure in exposures}
        assert len(obs_ids) == len(exposures)

        async with create_test_client(
            repo_path=repo_path,
            repo_path_2=repo_path_2,
        ) as (
            client,
            messages,
        ):
            shared_state = get_shared_state()
            assert len(shared_state.butler_factory.repositories) == 2

            async def run_find(
                find_args: dict[str, typing.Any],
                registry: int = 2,
                instrument: str = instrument,
            ) -> httpx.Response:
                """Run a query after adding registry and instrument
                parameters.
                """
                full_find_args = find_args.copy()
                full_find_args["registry"] = registry
                full_find_args["instrument"] = instrument
                response = await client.get(
                    "/exposurelog/exposures",
                    params=full_find_args,
                )
                return response

            # Searching the wrong registry should return no matches.
            response = await run_find(dict(), registry=1)
            found_exposures = assert_good_response(response)
            assert len(found_exposures) == 0

            response = await run_find(dict())
            found_exposures = assert_good_find_response(
                response, exposures, lambda exposure: True
            )
            found_obs_ids = {
                exposure["obs_id"] for exposure in found_exposures
            }
            assert len(found_obs_ids) == len(found_exposures)

            # Check for duplicate exposures when using limit.
            for limit in (
                1,
                len(exposures) - 3,
                len(exposures),
                len(exposures) + 3,
            ):
                response = await run_find({"limit": limit})
                found_exposures = assert_good_response(response)
                assert len(found_exposures) == min(limit, len(exposures))
                found_obs_ids = {
                    exposure["obs_id"] for exposure in found_exposures
                }
                assert len(found_obs_ids) == len(found_exposures)
                assert found_obs_ids <= obs_ids
