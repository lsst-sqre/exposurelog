import datetime
import os
import pathlib
import random
import unittest

from exposurelog.testutils import create_test_client, modify_environ


class TestUtilsTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_create_client_errors(self) -> None:
        repo_path = pathlib.Path(__file__).parent / "data" / "LSSTCam"

        # num_edited must be < num_messages (unless both are 0)
        with self.assertRaises(ValueError):
            async with create_test_client(
                repo_path=repo_path,
                num_messages=5,
                num_edited=5,
                random_seed=31,
            ):
                pass

    def test_modify_environ(self) -> None:
        random.seed(45)
        original_environ = os.environ.copy()
        n_to_delete = 3
        self.assertGreater(len(original_environ), n_to_delete)
        curr_time = datetime.datetime.now().isoformat()
        new_key0 = "_a_long_key_name_" + curr_time
        new_key1 = "_another_long_key_name_" + curr_time
        self.assertNotIn(new_key0, os.environ)
        self.assertNotIn(new_key1, os.environ)
        some_keys = random.sample(list(original_environ.keys()), 3)
        kwargs = {
            some_keys[0]: None,
            some_keys[1]: None,
            some_keys[2]: "foo",
            new_key0: "bar",
            new_key1: None,
        }
        with modify_environ(**kwargs):
            for name, value in kwargs.items():
                if value is None:
                    self.assertNotIn(name, os.environ)
                else:
                    self.assertEqual(os.environ[name], value)
            for name, value in os.environ.items():
                if name in kwargs:
                    self.assertEqual(value, kwargs[name])
                else:
                    self.assertEqual(value, original_environ[name])
        self.assertEqual(os.environ, original_environ)

        # Values that are neither None nor a string should raise RuntimeError
        for bad_value in (3, 1.23, True, False):
            bad_kwargs = kwargs.copy()
            bad_kwargs[new_key1] = bad_value  # type: ignore
            with self.assertRaises(RuntimeError):
                with modify_environ(**bad_kwargs):
                    pass
            self.assertEqual(os.environ, original_environ)
