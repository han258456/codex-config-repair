import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tomlkit

from codex_repair.engine import (
    BACKUP_DIR, MAX_CONFIG, RELAY_PROVIDER, ConnectionSettings, Options,
    RepairError, analyze, connection_summary, execute, read_connection_settings, restore,
)


class ConnectionImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.source = self.home / "saved-config.toml-bf"
        self.config = self.home / "config.toml"
        self.original = b'model = "gpt-6-astra"\nmodel_provider = "openai"\n'
        self.config.write_bytes(self.original)
        self.auth = self.home / "auth.json"
        self.auth_bytes = b'{"fake_login":"KEEP_EXISTING_OFFICIAL_LOGIN"}'
        self.auth.write_bytes(self.auth_bytes)

    def tearDown(self):
        self.assertEqual(self.auth.read_bytes(), self.auth_bytes)

    def source_config(self, values, *, encoding="utf-8", name=None):
        if name is not None:
            self.source = self.home / name
        document = tomlkit.document()
        document.update(values)
        self.source.write_bytes(tomlkit.dumps(document).encode(encoding))
        return self.source

    def relay_config(self, *, provider="vendor", model="vendor-model", **fields):
        return self.source_config({
            "model": model,
            "model_provider": provider,
            "model_providers": {provider: {
                "base_url": "https://gateway.example.com/v1/",
                "experimental_bearer_token": "IMPORTED_FAKE_KEY",
                **fields,
            }},
        })

    def test_official_config_and_default_provider_import_as_direct(self):
        for values in [{"model": "gpt-6-astra"},
                       {"model": "gpt-6-astra", "model_provider": "openai"}]:
            with self.subTest(values=values):
                self.source_config(values)
                self.assertEqual(read_connection_settings(self.source),
                                 ConnectionSettings("direct", model="gpt-6-astra"))

    def test_empty_or_projects_only_config_has_no_connection_to_import(self):
        for values in [{}, {"projects": {"example": {"trust_level": "trusted"}}}]:
            with self.subTest(values=values):
                self.source_config(values)
                with self.assertRaises(RepairError):
                    read_connection_settings(self.source)
                self.assertEqual(self.config.read_bytes(), self.original)

    def test_official_provider_without_model_keeps_current_model_when_applied(self):
        self.source_config({"model_provider": "openai"})
        settings = read_connection_settings(self.source)
        self.assertEqual(settings, ConnectionSettings("direct"))
        plan = analyze(self.home, options=Options(False, False, False, settings))
        self.assertEqual(plan.model, "gpt-6-astra")
        self.assertFalse(plan.needed)

    def test_active_custom_provider_imports_normalized_url_key_and_model(self):
        self.relay_config()
        settings = read_connection_settings(self.source)
        self.assertEqual(settings, ConnectionSettings(
            "relay", "https://gateway.example.com/v1", "IMPORTED_FAKE_KEY", "vendor-model"))
        self.assertNotIn("IMPORTED_FAKE_KEY", repr(settings))

    def test_active_provider_wins_over_saved_or_inactive_providers(self):
        self.source_config({
            "model_provider": "active",
            "model_providers": {
                "active": {"base_url": "https://active.example.com/v1",
                           "experimental_bearer_token": "ACTIVE_FAKE_KEY"},
                RELAY_PROVIDER: {"base_url": "https://saved.example.com/v1",
                                 "experimental_bearer_token": "INACTIVE_FAKE_KEY"},
                "unused": {"base_url": "not-a-valid-url",
                           "experimental_bearer_token": "bad key"},
            },
        })
        settings = read_connection_settings(self.source)
        self.assertEqual(settings.base_url, "https://active.example.com/v1")
        self.assertEqual(settings.api_key, "ACTIVE_FAKE_KEY")

    def test_saved_inactive_relay_does_not_change_official_detection(self):
        self.source_config({
            "model_provider": "openai",
            "model_providers": {RELAY_PROVIDER: {
                "base_url": "https://saved.example.com/v1",
                "experimental_bearer_token": "INACTIVE_FAKE_KEY",
            }},
        })
        self.assertEqual(read_connection_settings(self.source), ConnectionSettings("direct"))

    def test_openai_provider_with_custom_base_url_imports_as_relay(self):
        self.relay_config(provider="openai")
        self.assertEqual(read_connection_settings(self.source).mode, "relay")

    def test_top_level_openai_base_url_imports_as_relay_without_guessing_auth(self):
        self.source_config({"model": "gpt-6-astra",
                            "openai_base_url": "https://gateway.example.com/v1/"})
        self.assertEqual(read_connection_settings(self.source), ConnectionSettings(
            "relay", "https://gateway.example.com/v1", model="gpt-6-astra"))

    def test_conflicting_top_level_and_provider_endpoints_do_not_retarget_key(self):
        self.source_config({
            "model_provider": "openai",
            "openai_base_url": "https://different.example.com/v1",
            "model_providers": {"openai": {
                "base_url": "https://original.example.com/v1/",
                "experimental_bearer_token": "IMPORTED_FAKE_KEY",
            }},
        })
        raw = self.source.read_bytes()
        with self.assertRaises(RepairError) as caught:
            read_connection_settings(self.source)
        self.assertNotIn("IMPORTED_FAKE_KEY", str(caught.exception))
        self.assertEqual(self.source.read_bytes(), raw)
        self.config.write_bytes(raw)
        self.assertFalse(connection_summary(self.home)["has_key"])

        self.source_config({
            "model_provider": "openai",
            "openai_base_url": "https://original.example.com/v1",
            "model_providers": {"openai": {
                "base_url": "https://original.example.com/v1/",
                "experimental_bearer_token": "IMPORTED_FAKE_KEY",
            }},
        })
        settings = read_connection_settings(self.source)
        self.assertEqual(settings.base_url, "https://original.example.com/v1")
        self.assertEqual(settings.api_key, "IMPORTED_FAKE_KEY")

    def test_supported_encodings_and_backup_extensions_are_read_only(self):
        text = ('model = "vendor-model"\nmodel_provider = "vendor"\n'
                '[model_providers.vendor]\nbase_url = "https://gateway.example.com/v1"\n'
                'experimental_bearer_token = "IMPORTED_FAKE_KEY"\n')
        for name, encoding in [("old-config.toml", "utf-8"),
                               ("config.toml-bf", "utf-8-sig"),
                               ("config.toml.bak", "utf-16")]:
            with self.subTest(name=name, encoding=encoding):
                self.source = self.home / name
                raw = text.encode(encoding)
                self.source.write_bytes(raw)
                settings = read_connection_settings(self.source)
                self.assertEqual(settings.api_key, "IMPORTED_FAKE_KEY")
                self.assertEqual(self.source.read_bytes(), raw)
                self.assertEqual(self.config.read_bytes(), self.original)
                self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_existing_environment_key_can_supply_imported_credential(self):
        self.relay_config(experimental_bearer_token="", env_key="CODEX_IMPORT_TEST_KEY")
        with patch.dict(os.environ, {"CODEX_IMPORT_TEST_KEY": "ENVIRONMENT_FAKE_KEY"}):
            self.assertEqual(read_connection_settings(self.source).api_key, "ENVIRONMENT_FAKE_KEY")

    def test_missing_environment_key_allows_manual_key_entry(self):
        self.relay_config(experimental_bearer_token="", env_key="CODEX_IMPORT_TEST_MISSING_KEY")
        with patch.dict(os.environ, {}, clear=True):
            settings = read_connection_settings(self.source)
        self.assertEqual(settings.mode, "relay")
        self.assertEqual(settings.api_key, "")
        self.assertEqual(settings.base_url, "https://gateway.example.com/v1")

    def test_bearer_authorization_header_can_supply_imported_credential(self):
        self.relay_config(experimental_bearer_token="", http_headers={
            "Authorization": "Bearer HEADER_FAKE_KEY",
        })
        self.assertEqual(read_connection_settings(self.source).api_key, "HEADER_FAKE_KEY")

    def test_multiple_credential_mechanisms_are_not_guessed(self):
        for fields in [{"env_key": "CODEX_IMPORT_TEST_KEY"},
                       {"http_headers": {"Authorization": "Bearer HEADER_FAKE_KEY"}}]:
            with self.subTest(fields=fields):
                self.relay_config(**fields)
                with patch.dict(os.environ, {"CODEX_IMPORT_TEST_KEY": "ENVIRONMENT_FAKE_KEY"}):
                    with self.assertRaises(RepairError) as caught:
                        read_connection_settings(self.source)
                for secret in ["IMPORTED_FAKE_KEY", "ENVIRONMENT_FAKE_KEY", "HEADER_FAKE_KEY"]:
                    self.assertNotIn(secret, str(caught.exception))

    def test_declared_environment_auth_conflicts_with_token_even_if_not_available(self):
        self.relay_config(env_key="CODEX_IMPORT_TEST_MISSING_KEY")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RepairError):
                read_connection_settings(self.source)

    def test_dynamic_auth_official_auth_or_incompatible_wire_api_is_not_guessed(self):
        for fields in [{"auth": {"command": "SECRET_COMMAND_NOT_TO_EXECUTE"}}, {"auth": {}},
                       {"requires_openai_auth": True}, {"wire_api": "chat"}]:
            with self.subTest(fields=fields):
                self.relay_config(**fields)
                with self.assertRaises(RepairError) as caught:
                    read_connection_settings(self.source)
                self.assertNotIn("IMPORTED_FAKE_KEY", str(caught.exception))
                self.assertNotIn("SECRET_COMMAND_NOT_TO_EXECUTE", str(caught.exception))

    def test_environment_authorization_header_is_not_imported_as_static_auth(self):
        for token in ["", "IMPORTED_FAKE_KEY"]:
            with self.subTest(has_inline_token=bool(token)):
                self.relay_config(experimental_bearer_token=token, env_http_headers={
                    "Authorization": "CODEX_IMPORT_TEST_HEADER",
                })
                with patch.dict(os.environ, {"CODEX_IMPORT_TEST_HEADER": "Bearer ENV_HEADER_FAKE_KEY"}):
                    with self.assertRaises(RepairError) as caught:
                        read_connection_settings(self.source)
                self.assertNotIn("ENV_HEADER_FAKE_KEY", str(caught.exception))
                self.assertNotIn("IMPORTED_FAKE_KEY", str(caught.exception))

    def test_active_profile_is_rejected_instead_of_importing_unrelated_main_settings(self):
        self.source_config({"model": "main-model", "profile": "chosen",
                            "profiles": {"chosen": {"model": "profile-model"}}})
        with self.assertRaises(RepairError):
            read_connection_settings(self.source)

    def test_relay_without_saved_key_is_readable_but_cannot_apply_without_key(self):
        self.relay_config(experimental_bearer_token="")
        settings = read_connection_settings(self.source)
        self.assertEqual(settings.api_key, "")
        with self.assertRaises(RepairError):
            analyze(self.home, options=Options(False, False, False, settings))
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_import_does_not_read_official_login_file(self):
        self.relay_config()
        original_read = Path.read_bytes

        def guarded_read(path):
            if path == self.auth:
                raise AssertionError("Connection import must not read auth.json")
            return original_read(path)

        with patch.object(Path, "read_bytes", guarded_read):
            settings = read_connection_settings(self.source)
        self.assertEqual(settings.api_key, "IMPORTED_FAKE_KEY")

    def test_invalid_toml_error_does_not_expose_source_text_or_key(self):
        self.source.write_text('model = "SECRET_INVALID_SOURCE\n', encoding="utf-8")
        raw = self.source.read_bytes()
        with self.assertRaises(RepairError) as caught:
            read_connection_settings(self.source)
        self.assertNotIn("SECRET_INVALID_SOURCE", str(caught.exception))
        self.assertEqual(self.source.read_bytes(), raw)

    def test_invalid_selected_provider_fields_are_rejected_without_secret_echo(self):
        for fields in [
            {"base_url": "https://user:SECRET_INVALID_SOURCE@gateway.example.com/v1"},
            {"base_url": "https://gateway.example.com/v1?key=SECRET_INVALID_SOURCE"},
            {"base_url": 123},
            {"experimental_bearer_token": "SECRET_INVALID_SOURCE bad key"},
            {"experimental_bearer_token": 123},
            {"env_key": 123, "experimental_bearer_token": ""},
            {"http_headers": ["Authorization"]},
            {"http_headers": {"Authorization": 123}},
            {"requires_openai_auth": "false"},
        ]:
            with self.subTest(fields=fields):
                self.relay_config(**fields)
                raw = self.source.read_bytes()
                with self.assertRaises(RepairError) as caught:
                    read_connection_settings(self.source)
                self.assertNotIn("SECRET_INVALID_SOURCE", str(caught.exception))
                self.assertEqual(self.source.read_bytes(), raw)

    def test_invalid_model_or_provider_shape_is_rejected(self):
        for values in [{"model": ["gpt-6-astra"]}, {"model": "model with spaces"},
                       {"model_provider": 123},
                       {"model_provider": "vendor", "model_providers": []},
                       {"model_provider": "vendor", "model_providers": {"vendor": "bad"}},
                       {"model_provider": "missing", "model_providers": {}}]:
            with self.subTest(values=values):
                self.source_config(values)
                with self.assertRaises(RepairError):
                    read_connection_settings(self.source)
                self.assertEqual(self.config.read_bytes(), self.original)

    def test_missing_oversized_or_unreadable_encoding_source_is_rejected(self):
        for raw in [None, b"#" + b"a" * MAX_CONFIG, b"\x80\xffnot-utf8"]:
            with self.subTest(raw_kind="missing" if raw is None else "present"):
                if raw is None:
                    self.source.unlink(missing_ok=True)
                else:
                    self.source.write_bytes(raw)
                with self.assertRaises(RepairError):
                    read_connection_settings(self.source)
                self.assertEqual(self.config.read_bytes(), self.original)
                self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_imported_relay_uses_normal_preview_backup_and_exact_restore(self):
        self.relay_config()
        source_before = self.source.read_bytes()
        settings = read_connection_settings(self.source)
        plan = analyze(self.home, options=Options(False, False, False, settings))
        self.assertTrue(plan.needed)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertNotIn("IMPORTED_FAKE_KEY", repr(plan.options))
        self.assertNotIn("IMPORTED_FAKE_KEY", json.dumps(plan.report()))
        backup = execute(plan, process_probe=lambda: [])
        saved = tomlkit.parse(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["model_provider"], RELAY_PROVIDER)
        self.assertEqual(saved["model"], "vendor-model")
        self.assertEqual(saved["model_providers"][RELAY_PROVIDER]["experimental_bearer_token"],
                         "IMPORTED_FAKE_KEY")
        self.assertEqual((backup / "originals" / "config.toml").read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), source_before)
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), source_before)

    def current_custom_config(self):
        self.relay_config()
        self.config.write_bytes(self.source.read_bytes())
        return self.config.read_bytes()

    def test_current_custom_provider_summary_prefills_endpoint_without_exposing_key(self):
        self.current_custom_config()
        summary = connection_summary(self.home)
        self.assertEqual(summary["provider"], "vendor")
        self.assertEqual(summary["selected_mode"], "relay")
        self.assertEqual(summary["base_url"], "https://gateway.example.com/v1")
        self.assertTrue(summary["has_key"])
        self.assertNotIn("IMPORTED_FAKE_KEY", repr(summary))

    def test_direct_summary_does_not_echo_credential_bearing_cached_endpoint(self):
        for url in ["https://gateway.example.com/v1?key=SECRET_CACHED_ENDPOINT",
                    "https://user:SECRET_CACHED_ENDPOINT@gateway.example.com/v1"]:
            with self.subTest(url=url):
                self.source_config({
                    "model_provider": "openai",
                    "model_providers": {RELAY_PROVIDER: {
                        "base_url": url, "experimental_bearer_token": "IMPORTED_FAKE_KEY",
                    }},
                })
                self.config.write_bytes(self.source.read_bytes())
                summary = connection_summary(self.home)
                self.assertEqual(summary["selected_mode"], "direct")
                self.assertEqual(summary["base_url"], "")
                self.assertFalse(summary["has_key"])
                self.assertNotIn("SECRET_CACHED_ENDPOINT", repr(summary))
                self.assertNotIn("IMPORTED_FAKE_KEY", repr(summary))

    def test_current_custom_provider_key_can_be_reused_at_same_normalized_endpoint(self):
        before = self.current_custom_config()
        settings = ConnectionSettings("relay", "https://gateway.example.com/v1")
        plan = analyze(self.home, options=Options(False, False, False, settings))
        self.assertNotIn("IMPORTED_FAKE_KEY", json.dumps(plan.report()))
        backup = execute(plan, process_probe=lambda: [])
        saved = tomlkit.parse(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["model_providers"][RELAY_PROVIDER]["experimental_bearer_token"],
                         "IMPORTED_FAKE_KEY")
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.config.read_bytes(), before)

    def test_current_openai_override_reuses_key_only_at_its_top_level_endpoint(self):
        self.source_config({
            "model_provider": "openai",
            "openai_base_url": "https://gateway.example.com/v1/",
            "model_providers": {"openai": {
                "experimental_bearer_token": "IMPORTED_FAKE_KEY",
            }},
        })
        before = self.source.read_bytes()
        self.config.write_bytes(before)
        summary = connection_summary(self.home)
        self.assertEqual(summary["selected_mode"], "relay")
        self.assertEqual(summary["base_url"], "https://gateway.example.com/v1")
        self.assertTrue(summary["has_key"])
        self.assertNotIn("IMPORTED_FAKE_KEY", repr(summary))

        settings = ConnectionSettings("relay", "https://gateway.example.com/v1")
        plan = analyze(self.home, options=Options(False, False, False, settings))
        backup = execute(plan, process_probe=lambda: [])
        saved = tomlkit.parse(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["model_providers"][RELAY_PROVIDER]["experimental_bearer_token"],
                         "IMPORTED_FAKE_KEY")
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.config.read_bytes(), before)

        changed = ConnectionSettings("relay", "https://another.example.com/v1")
        with self.assertRaises(RepairError):
            analyze(self.home, options=Options(False, False, False, changed))
        self.assertEqual(self.config.read_bytes(), before)

    def test_current_custom_provider_key_cannot_be_reused_at_different_endpoint(self):
        before = self.current_custom_config()
        settings = ConnectionSettings("relay", "https://another.example.com/v1")
        with self.assertRaises(RepairError):
            analyze(self.home, options=Options(False, False, False, settings))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.home / BACKUP_DIR).exists())


if __name__ == "__main__":
    unittest.main()
