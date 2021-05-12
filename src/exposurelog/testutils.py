from __future__ import annotations

__all__ = [
    "MessageDictT",
    "assert_good_response",
    "assert_messages_equal",
    "cast_special",
    "create_test_client",
    "modify_environ",
]

import contextlib
import datetime
import os
import pathlib
import typing
import unittest.mock
import uuid

import astropy.time
import httpx
import numpy as np
import sqlalchemy.engine
import testing.postgresql
from sqlalchemy.ext.asyncio import create_async_engine

from . import app, shared_state
from .create_message_table import create_message_table
from .message import MESSAGE_FIELDS

# Range of dates for random messages.
MIN_DATE_RANDOM_MESSAGE = "2021-01-01"
MAX_DATE_RANDOM_MESSAGE = "2022-12-31"

TEST_SITE_ID = "test"

random = np.random.RandomState(47)

# Type annotation aliases
MessageDictT = typing.Dict[str, typing.Any]
ArgDictT = typing.Dict[str, typing.Any]


@contextlib.asynccontextmanager
async def create_test_client(
    repo_path: pathlib.Path,
    num_messages: int,
    num_edited: int = 0,
) -> typing.AsyncGenerator[
    typing.Tuple[httpx.AsyncClient, typing.List[MessageDictT]], None
]:
    """Create the test database, test server, and httpx client."""
    with testing.postgresql.Postgresql() as postgresql:
        messages = await create_test_database(
            postgresql, num_messages=num_messages, num_edited=num_edited
        )

        db_config = db_config_from_dsn(postgresql.dsn())
        with modify_environ(
            BUTLER_URI_1=str(repo_path),
            SITE_ID=TEST_SITE_ID,
            **db_config,
        ):
            # Note: httpx.AsyncClient does not trigger startup and shutdown
            # events. We could use asgi-lifespan's LifespanManager,
            # but it does not trigger the shutdown event if there is
            # an exception, so it does not seem worth the bother.
            assert not shared_state.has_shared_state()
            await app.startup_event()
            try:
                async with httpx.AsyncClient(
                    app=app.app, base_url="http://test"
                ) as client:
                    assert shared_state.has_shared_state()
                    yield client, messages
            finally:
                await app.shutdown_event()


@contextlib.contextmanager
def modify_environ(**kwargs: typing.Any) -> typing.Iterator:
    """Context manager to temporarily patch os.environ.

    This calls `unittest.mock.patch` and is only intended for unit tests.

    Parameters
    ----------
    kwargs : `dict` [`str`, `str` or `None`]
        Environment variables to set or clear.
        Each key is the name of an environment variable (with correct case);
        it need not already exist. Each value must be one of:

        * A string value to set the env variable.
        * None to delete the env variable, if present.

    Raises
    ------
    RuntimeError
        If any value in kwargs is not of type `str` or `None`.

    Notes
    -----
    Example of use::

        from lsst.ts import salobj
        ...
        def test_foo(self):
            set_value = "Value for $ENV_TO_SET"
            with salobj.modify_environ(
                HOME=None,  # Delete this env var
                ENV_TO_SET=set_value,  # Set this env var
            ):
                self.assertNotIn("HOME", os.environ)
                self.assert(os.environ["ENV_TO_SET"], set_value)
    """
    bad_value_strs = [
        f"{name}: {value!r}"
        for name, value in kwargs.items()
        if not isinstance(value, str) and value is not None
    ]
    if bad_value_strs:
        raise RuntimeError(
            "The following arguments are not of type str or None: "
            + ", ".join(bad_value_strs)
        )

    new_environ = os.environ.copy()
    for name, value in kwargs.items():
        if value is None:
            new_environ.pop(name, None)
        else:
            new_environ[name] = value
    with unittest.mock.patch("os.environ", new_environ):
        yield


def assert_good_response(response: httpx.Response) -> typing.Any:
    """Assert that a response is good and return the data.

    Parameters
    ----------
    command
        The command. If None then return the whole response, else return
        the response from the command (response["data"][command]) --
        a single message dict or a list of message dicts.
    """
    assert (
        response.status_code == 200
    ), f"Bad response {response.status_code}: {response.text}"
    data = response.json()
    assert "errors" not in data, f"errors={data['errors']}"
    return data


def assert_messages_equal(
    message1: MessageDictT, message2: MessageDictT
) -> None:
    """Assert that two messages are identical.

    Handle the "id" field specially because it may be a uuid.UUID or a str.
    Handle the "date_added" and "date_invalidated" fields specially
    because they may be datetime.datetime or ISOT strings.
    """
    assert message1.keys() == message2.keys()
    for field in message1:
        values = [
            cast_special(value) for value in (message1[field], message2[field])
        ]
        assert (
            values[0] == values[1]
        ), f"field {field} unequal: {values[0]} != {values[1]}"


def cast_special(value: typing.Any) -> typing.Any:
    """Cast special types (not plain old types) to str.

    This allows comparison between values in the database
    and values returned by the web API.

    The special types are:

    * datetime.datetime: converted to an ISO string with "T" separator.
    * uuid.UUID: convert to a string.
    """
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep="T")
    elif isinstance(value, uuid.UUID):
        return str(value)
    return value


def db_config_from_dsn(dsn: dict[str, str]) -> dict[str, str]:
    """Get app database configuration arguments from a database dsn.

    The intended usage is to configure the application
    from an instance of testing.postgresql.Postgresql()::

        with testing.postgresql.Postgresql() as postgresql:
            create_test_database(postgresql, num_messages=0)

            with modify_environ(
                BUTLER_URI_1=str(repo_path),
                SITE_ID=TEST_SITE_ID,
                **db_config,
            ):
                import exposurelog.app

                client = fastapi.testclient.TestClient(exposurelog.app.app)
    """
    assert dsn.keys() <= {"port", "host", "user", "database"}
    return {
        f"exposurelog_db_{key}".upper(): str(value)
        for key, value in dsn.items()
    }


random = np.random.RandomState(47)


def random_bool() -> bool:
    return random.rand() > 0.5


def random_date(precision: int = 0) -> datetime.datetime:
    """Return a random date

    This is the same format as dates returned from the database.
    """
    min_date_unix = astropy.time.Time(MIN_DATE_RANDOM_MESSAGE).unix
    max_date_unix = astropy.time.Time(MAX_DATE_RANDOM_MESSAGE).unix
    dsec = max_date_unix - min_date_unix
    unix_time = min_date_unix + random.rand() * dsec
    return astropy.time.Time(
        unix_time, format="unix", precision=precision
    ).datetime


def random_str(nchar: int) -> str:
    """Return a random string of printable UTF-8 characters.

    The list of characters is limited, but attempts to
    cover a wide range of potentially problematic characters
    including ' " \t \n \\ and an assortment of non-ASCII characters.
    """
    chars = list(
        "abcdefgABCDEFG012345 \t\n\r"
        "'\"“”`~!@#$%^&*()-_=+[]{}\\|,.<>/?"
        "¡™£¢∞§¶•ªº–≠“‘”’«»…ÚæÆ≤¯≥˘÷¿"
        "œŒ∑„®‰†ˇ¥ÁüîøØπ∏åÅßÍ∂ÎƒÏ©˝˙Ó∆Ô˚¬ÒΩ¸≈˛çÇ√◊∫ıñµÂ"
    )
    return "".join(random.choice(chars, size=(20,)))


def random_message() -> MessageDictT:
    """Make one random message, as a dict of field: value.

    All messages will have ``id=None``, ``site_id=TEST_SITE_ID``,
    ``is_valid=True``, ``date_invalidated=None``, and ``parent_id=None``.

    Fields are in the same order as `Message` and the database schema,
    to make it easier to visually compare these messages to messages in
    responses.

    String are random ASCII characters, and each string field has
    a slightly different arbitrary length.

    To use:

    * Call multiple times to make a list of messages.
    * Sort that list by ``date_added``.
    * Add the ``id`` field, in order, starting at 1.
    * Optionally modify some messages to be edited versions
      of earlier messages, as follows:

      * Set edited_message["parent_id"] = parent_message["id"]
      * Set parent_message["is_valid"] = False
      * Set parent_message["date_invalidated"] =
        edited_message["date_added"]
    """
    random_yyyymmdd = astropy.time.Time(random_date()).strftime("%Y%m%d")

    message = dict(
        id=None,
        site_id=TEST_SITE_ID,
        obs_id=random_str(nchar=18),
        instrument=random_str(nchar=16),
        day_obs=int(random_yyyymmdd),
        message_text=random_str(nchar=20),
        user_id=random_str(nchar=14),
        user_agent=random_str(nchar=12),
        is_human=random_bool(),
        is_valid=True,
        exposure_flag=random.choice(["none", "junk", "questionable"]),
        date_added=random_date(),
        date_invalidated=None,
        parent_id=None,
    )

    # Check that we have set all fields (not necessarily in order).
    assert set(message) == set(MESSAGE_FIELDS)

    return message


def random_messages(num_messages: int, num_edited: int) -> list[MessageDictT]:
    """Make a list of random messages, each a dict of field: value.

    Parameters
    ----------
    num_messages
        Number of messages
    num_edited
        Number of these messages that should be edited versions
        of earlier messages.

    Notes
    -----

    The list will be in order of increasing ``date_added``.

    Link about half of the messages to an older message.
    """
    message_list = [random_message() for i in range(num_messages)]
    message_list.sort(key=lambda message: message["date_added"])
    for i, message in enumerate(message_list):
        message["id"] = uuid.uuid4()

    # Create edited messages.
    parent_message_id_set = set()
    edited_messages = list(
        # [1:] because there is no older message to be the parent.
        random.choice(message_list[1:], size=num_edited, replace=False)
    )
    edited_messages.sort(key=lambda message: message["date_added"])
    for i, message in enumerate(edited_messages):
        while True:
            parent_message = random.choice(message_list[0 : i + 1])
            if parent_message["id"] not in parent_message_id_set:
                parent_message_id_set.add(parent_message["id"])
                break
        message["parent_id"] = parent_message["id"]
        parent_message["is_valid"] = False
        parent_message["date_invalidated"] = message["date_added"]
    return message_list


async def create_test_database(
    postgresql: testing.postgresql.Postgresql,
    num_messages: int,
    num_edited: int = 0,
) -> list[MessageDictT]:
    """Create a test database, initialize it with random messages,
    and return the messages.

    Parameters
    ----------
    postgresql
        Test database. Typically created using::

            with testing.postgresql.Postgresql() as postgresql:
    num_messages
        Number of messages
    num_edited, optional
        Number of these messages that should be edited versions
        of earlier messages. Must be 0 or < ``num_messages``.

    Returns
    -------
    messages
        The randomly created messages. Each message is a dict of field: value
        and all fields are set.
    """
    if num_edited > 0 and num_edited >= num_messages:
        raise ValueError(
            f"num_edited={num_edited} must be zero or "
            f"less than num_messages={num_messages}"
        )
    sa_url = sqlalchemy.engine.make_url(postgresql.url())
    sa_url = sa_url.set(drivername="postgresql+asyncpg")
    engine = create_async_engine(sa_url)

    table = create_message_table()
    async with engine.begin() as connection:
        await connection.run_sync(table.metadata.create_all)

    messages = random_messages(
        num_messages=num_messages, num_edited=num_edited
    )
    async with engine.begin() as connection:
        for message in messages:
            # Do not insert the "is_valid" field
            # because it is computed.
            pruned_message = message.copy()
            del pruned_message["is_valid"]
            result = await connection.execute(
                table.insert().values(**pruned_message).returning(table.c.id)
            )
            data = result.fetchone()
            assert message["id"] == data["id"]

    return messages
