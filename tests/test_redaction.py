import unittest

import tomlkit

from codex_repair.engine import redact_toml


class RedactionTests(unittest.TestCase):
    def test_all_comment_trivia_is_removed_from_display_only(self):
        text = (
            '# DEMO_SECRET_HEADER\n'
            'model = "example" # api_key = DEMO_SECRET_INLINE\n'
            '[desktop] # DEMO_SECRET_TABLE_COMMENT\n'
            'future = [\n'
            '  "value", # DEMO_SECRET_ARRAY_COMMENT\n'
            ']\n'
        )
        before = text
        display = redact_toml(text)
        self.assertNotIn("DEMO_SECRET", display)
        self.assertEqual(tomlkit.parse(display).unwrap(), {"model": "example", "desktop": {"future": ["value"]}})
        self.assertEqual(text, before)

    def test_comment_marker_inside_ordinary_string_is_preserved(self):
        text = 'model = "example#literal"\n'
        self.assertEqual(tomlkit.parse(redact_toml(text))["model"], "example#literal")

    def test_mcp_args_are_masked_as_a_whole(self):
        text = (
            '[mcp_servers.demo]\n'
            'command = "python"\n'
            'args = ["--api-key", "DEMO_SECRET_FLAG", "DEMO_SECRET_POSITIONAL", "--other=value"]\n'
            'enabled = true\n'
        )
        display = redact_toml(text)
        self.assertNotIn("DEMO_SECRET", display)
        values = tomlkit.parse(display).unwrap()["mcp_servers"]["demo"]
        self.assertEqual(values["command"], "python")
        self.assertEqual(values["enabled"], True)
        self.assertEqual(values["args"], ["•••• 已隐藏 ••••"])
        self.assertIn("DEMO_SECRET_FLAG", text)

    def test_multiline_and_nested_credentials_are_masked(self):
        text = (
            'experimental_bearer_token = """\nDEMO_SECRET_LINE_ONE\nDEMO_SECRET_LINE_TWO\n"""\n'
            'future = [{password = "DEMO_SECRET_NESTED", public = "visible"}]\n'
        )
        display = redact_toml(text)
        self.assertNotIn("DEMO_SECRET", display)
        self.assertIn("visible", display)

    def test_sensitive_table_values_are_masked_even_with_opaque_names(self):
        text = (
            '[mcp_servers.demo]\ncommand = "python"\n'
            '[mcp_servers.demo.env]\nOPAQUE = "DEMO_SECRET_ENV"\n'
            '[model_providers.relay]\nname = "Relay"\n'
            '[model_providers.relay.http_headers]\nX-Custom = "DEMO_SECRET_HEADER"\n'
            '[model_providers.relay.auth]\ncommand = "DEMO_SECRET_AUTH"\n'
            '[shell_environment_policy.set]\nVALUE = "DEMO_SECRET_SHELL"\n'
        )
        display = redact_toml(text)
        self.assertNotIn("DEMO_SECRET", display)
        self.assertIn("已隐藏", display)
        self.assertIn('name = "Relay"', display)

    def test_url_credentials_query_and_fragment_are_never_exposed(self):
        for value in (
            "https://user:DEMO_SECRET@example.com/v1",
            "https://example.com/v1?token=DEMO_SECRET",
            "https://example.com/v1#DEMO_SECRET",
            "curl https://user:DEMO_SECRET withspaces@example.com/v1",
            "https://example.com/v1?value=DEMO_SECRET withspaces",
            'https://user:DEMO_SECRET"quote@example.com/v1',
            "https://example.com/?token=DEMO_SECRET; embedded-command",
        ):
            with self.subTest(value=value):
                text = 'url = ' + tomlkit.string(value).as_string()
                display = redact_toml(text)
                self.assertNotIn("DEMO_SECRET", display)
                self.assertIn("已隐藏", display)

    def test_command_with_embedded_key_option_is_hidden(self):
        text = '[mcp_servers.demo]\ncommand = "python --api-key DEMO_SECRET_COMMAND"\n'
        display = redact_toml(text)
        self.assertNotIn("DEMO_SECRET_COMMAND", display)

    def test_nonsecret_values_and_url_remain_visible(self):
        text = (
            'model = "example"\nfuture_setting = 12\n'
            '[model_providers.relay]\nname = "Relay"\nbase_url = "https://example.com/v1"\n'
        )
        self.assertEqual(tomlkit.parse(redact_toml(text)).unwrap(), tomlkit.parse(text).unwrap())

    def test_invalid_content_fails_closed_without_echoing_input(self):
        display = redact_toml('model = "DEMO_SECRET_UNTERMINATED')
        self.assertNotIn("DEMO_SECRET", display)
        self.assertIn("未展示", display)


if __name__ == "__main__":
    unittest.main()
