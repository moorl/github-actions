import contextlib
import importlib.util
import io
import os
import pathlib
import socket
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "moori_plugin_store_info.py"
SPEC = importlib.util.spec_from_file_location("moori_plugin_store_info", SCRIPT_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class ShopwareUrlAvailabilityTest(unittest.TestCase):
    def run_main(self, environment, yaml_content=None):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary_directory:
            yaml_path = pathlib.Path(temporary_directory) / "store.yaml"
            if yaml_content is not None:
                yaml_path.write_text(
                    yaml_content,
                    encoding="utf-8",
                )
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--yaml-path", str(yaml_path)]),
                contextlib.redirect_stdout(output),
            ):
                try:
                    module.main()
                except SystemExit as error:
                    return error.code, output.getvalue()
        return 0, output.getvalue()

    def test_missing_shopware_url_skips_before_reading_yaml(self):
        exit_code, output = self.run_main({})

        self.assertEqual(0, exit_code)
        self.assertIn("::notice::", output)

    def test_unresolvable_shopware_url_skips_before_reading_yaml(self):
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("not found")):
            exit_code, output = self.run_main({"SHOPWARE_URL": "https://missing.example"})

        self.assertEqual(0, exit_code)
        self.assertIn("::notice::", output)

    def test_resolvable_shopware_url_continues_with_yaml_loading(self):
        address = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0))]
        with mock.patch("socket.getaddrinfo", return_value=address):
            exit_code, output = self.run_main({"SHOPWARE_URL": "https://shop.example"})

        self.assertEqual(1, exit_code)
        self.assertNotIn("::notice::", output)
        self.assertIn("YAML-Datei nicht gefunden", output)

    def test_malformed_shopware_url_is_skipped(self):
        try:
            exit_code, output = self.run_main({"SHOPWARE_URL": "http://[bad"})
        except ValueError as error:
            self.fail(f"Malformed SHOPWARE_URL was not skipped: {error}")

        self.assertEqual(0, exit_code)
        self.assertIn("::notice::", output)

    def test_hostname_encoding_error_is_skipped(self):
        with mock.patch("socket.getaddrinfo", side_effect=UnicodeError("invalid hostname")):
            try:
                exit_code, output = self.run_main({"SHOPWARE_URL": "https://shop.example"})
            except UnicodeError as error:
                self.fail(f"Unencodable SHOPWARE_URL hostname was not skipped: {error}")

        self.assertEqual(0, exit_code)
        self.assertIn("::notice::", output)

    def test_missing_client_credentials_remain_errors(self):
        address = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0))]
        environments = (
            {
                "SHOPWARE_URL": "https://shop.example",
                "SHOPWARE_CLIENT_SECRET": "secret",
                "REPO_NAME": "ExamplePlugin",
            },
            {
                "SHOPWARE_URL": "https://shop.example",
                "SHOPWARE_CLIENT_ID": "client-id",
                "REPO_NAME": "ExamplePlugin",
            },
        )

        for environment in environments:
            with self.subTest(environment=environment):
                with mock.patch("socket.getaddrinfo", return_value=address):
                    exit_code, output = self.run_main(environment, "store: {}\n")

                self.assertEqual(1, exit_code)
                self.assertIn("::error::", output)
                self.assertIn("SHOPWARE_CLIENT_ID / SHOPWARE_CLIENT_SECRET fehlen", output)


if __name__ == "__main__":
    unittest.main()
