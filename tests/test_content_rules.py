import unittest

import tomlkit

from codex_repair.content import ContentCheck, check_content


class ContentRulesTests(unittest.TestCase):
    def test_valid_content_is_preserved_exactly(self):
        text = (
            '# model = “comment remains unchanged” True\r\n'
            'model = "gpt-example" # comment\r\n'
            'model_reasoning_effort = "ultra"\r\n'
            'model_provider = "openai"\r\n'
            'sandbox_mode = "workspace-write"\r\n'
            'future_setting = "True"\r\n'
            '[desktop]\r\n'
            'future_desktop_setting = 123\r\n'
        )
        for auto_fix in (False, True):
            check = check_content(text, auto_fix)
            self.assertTrue(check.valid, check.issues)
            self.assertEqual(check.text, text)
            self.assertEqual(check.fixes, [])

    def test_empty_or_comment_only_content_cannot_be_saved(self):
        for text in ("", " \r\n", "# comment\n# model = 'example'\n"):
            check = check_content(text, True)
            self.assertFalse(check.valid)
            self.assertEqual(check.text, text)

    def test_parser_issue_is_safe_and_has_location(self):
        secret = "SECRET_DIAGNOSTIC_MUST_NOT_LEAK"
        check = check_content(f'model = "valid"\n{secret} = \n')
        self.assertFalse(check.valid)
        self.assertEqual(check.issues[0].line, 2)
        self.assertNotIn(secret, repr(check))
        self.assertNotIn(secret, check.issues[0].message)
        self.assertIn("列", check.issues[0].message)

    def test_duplicate_key_is_not_discarded_or_auto_repaired(self):
        text = 'model = "first"\nmodel = "second"\n'
        check = check_content(text, True)
        self.assertFalse(check.valid)
        self.assertEqual(check.text, text)
        self.assertEqual(check.fixes, [])

    def test_fenced_document_is_fixable_and_retains_inner_comments(self):
        text = '```toml\n# Keep this comment\nmodel = "example"\n```\n'
        scan = check_content(text)
        self.assertFalse(scan.valid)
        self.assertTrue(scan.issues[0].fixable)
        self.assertEqual(scan.text, text)
        repaired = check_content(text, True)
        self.assertTrue(repaired.valid, repaired.issues)
        self.assertEqual(repaired.text, '# Keep this comment\nmodel = "example"\n')
        self.assertNotIn("```", repaired.text)
        self.assertTrue(repaired.fixes)
        empty_label = check_content('```\nmodel = "example"\n```', True)
        self.assertTrue(empty_label.valid, empty_label.issues)

    def test_other_code_fence_language_is_not_repaired(self):
        for text in ('```json\nmodel = "example"\n```', '```toml\nmodel = "example"```'):
            check = check_content(text, True)
            self.assertFalse(check.valid)
            self.assertEqual(check.text, text)

    def test_bom_at_start_is_removed(self):
        text = '\ufeffmodel = "example"\r\n'
        check = check_content(text, True)
        self.assertTrue(check.valid, check.issues)
        self.assertEqual(check.text, text[1:])

    def test_smart_delimiters_preserve_inner_apostrophes_and_comments(self):
        text = 'model = “example’s-model” # Keep “comment”\n'
        repaired = check_content(text, True)
        self.assertTrue(repaired.valid, repaired.issues)
        self.assertEqual(repaired.text, 'model = "example’s-model" # Keep “comment”\n')

    def test_boolean_case_only_changes_value_tokens(self):
        text = (
            'model_supports_reasoning_summaries = True\n'
            'custom = {True = False, False = [True, False]}\n'
            'ordinary_string = "True False" # True False\n'
            '[True]\n'
            'False = True'
        )
        repaired = check_content(text, True)
        self.assertTrue(repaired.valid, repaired.issues)
        self.assertEqual(repaired.text, text.replace(
            'summaries = True', 'summaries = true',
        ).replace(
            '{True = False, False = [True, False]}', '{True = false, False = [true, false]}',
        ).replace('False = True', 'False = true'))
        parsed = tomlkit.parse(repaired.text).unwrap()
        self.assertIn("True", parsed)
        self.assertIn("True", parsed["custom"])

    def test_multiline_basic_and_literal_strings_are_never_rewritten(self):
        text = (
            'first = """\n'
            'model = “keep these delimiters”\n'
            'enabled = True # literal text\n'
            '"""\n'
            "second = '''\n"
            'model = “keep these too”\n'
            'enabled = False\n'
            "'''\n"
            'model_supports_reasoning_summaries = True\n'
        )
        repaired = check_content(text, True)
        self.assertTrue(repaired.valid, repaired.issues)
        self.assertEqual(repaired.text, text.replace('summaries = True', 'summaries = true'))

    def test_escaped_quotes_do_not_expose_string_tokens(self):
        text = (
            'secret = "escaped \\" True False"\n'
            'model_supports_reasoning_summaries = False\n'
        )
        repaired = check_content(text, True)
        self.assertTrue(repaired.valid, repaired.issues)
        self.assertEqual(repaired.text, text.replace('summaries = False', 'summaries = false'))

    def test_failed_syntax_repairs_are_atomic(self):
        text = 'model = “example”\nmodel_supports_reasoning_summaries = True\nbroken = [\n'
        check = check_content(text, True)
        self.assertFalse(check.valid)
        self.assertEqual(check.text, text)
        self.assertEqual(check.fixes, [])
        self.assertFalse(check.issues[0].fixable)

    def test_known_string_booleans_are_repaired_without_touching_secrets(self):
        text = (
            '# Keep all spacing and comments\r\n'
            'model_supports_reasoning_summaries = "true" # root\r\n'
            'unknown_boolean = "false"\r\n'
            '[model_providers.relay]\r\n'
            'name = "Example"\r\n'
            'base_url = "https://example.com/v1"\r\n'
            'experimental_bearer_token = "SECRET_True_false"\r\n'
            'requires_openai_auth = "false" # auth\r\n'
            'supports_websockets = \'true\'\r\n'
            '[mcp_servers.test]\r\n'
            'command = "python"\r\n'
            'args = ["True", "false"]\r\n'
            'enabled = "true" # server\r\n'
        )
        scan = check_content(text)
        self.assertFalse(scan.valid)
        self.assertTrue(all(issue.fixable for issue in scan.issues))
        repaired = check_content(text, True)
        self.assertTrue(repaired.valid, repaired.issues)
        self.assertEqual(repaired.text, text.replace(
            'summaries = "true"', 'summaries = true',
        ).replace(
            'requires_openai_auth = "false"', 'requires_openai_auth = false',
        ).replace(
            "supports_websockets = 'true'", 'supports_websockets = true',
        ).replace('enabled = "true"', 'enabled = true'))
        self.assertNotIn("SECRET_True_false", repr(repaired))
        self.assertEqual(len(repaired.fixes), 4)

    def test_known_types_are_errors_and_future_effort_is_allowed(self):
        invalid = [
            'model = 10', 'model_provider = false',
            'model_reasoning_effort = ["ultra"]',
            'model_verbosity = "unsupported"',
            'model_reasoning_summary = 4', 'personality = "unsupported"',
            'notify = "command"', 'notify = ["command", 42]',
            'model_supports_reasoning_summaries = 1',
            'model_providers = []', 'mcp_servers = "test"',
            'projects = false', 'desktop = 1',
        ]
        for text in invalid:
            with self.subTest(text=text):
                self.assertFalse(check_content(text, True).valid)
        for value in ("max", "ultra", "future-effort"):
            self.assertTrue(check_content(f'model_reasoning_effort = "{value}"').valid)

    def test_custom_provider_definition_is_required_and_builtins_are_allowed(self):
        self.assertFalse(check_content('model_provider = "not-defined"').valid)
        for provider in ("openai", "ollama", "lmstudio", "amazon-bedrock"):
            self.assertTrue(check_content(f'model_provider = "{provider}"').valid)
        text = 'model_provider = "relay"\n[model_providers.relay]\nname = "Relay"\n'
        self.assertTrue(check_content(text).valid)

    def test_custom_provider_types_and_auth_conflicts_are_manual_errors(self):
        prefix = 'model_provider = "relay"\n[model_providers.relay]\nname = "Relay"\n'
        invalid = [
            'base_url = 42', 'env_key = false', 'experimental_bearer_token = []',
            'requires_openai_auth = 1', 'supports_websockets = "maybe"',
            'wire_api = "chat"',
            'env_key = "API_KEY"\nexperimental_bearer_token = "SECRET"',
            'requires_openai_auth = true\nenv_key = "API_KEY"',
            'env_key = "API_KEY"\n[model_providers.relay.auth]\ncommand = "tool"',
        ]
        for suffix in invalid:
            with self.subTest(suffix=suffix):
                text = prefix + suffix
                check = check_content(text, True)
                self.assertFalse(check.valid)
                self.assertEqual(check.text, text)
                self.assertFalse(any(issue.fixable for issue in check.issues))
                self.assertNotIn("SECRET", repr(check))
        self.assertFalse(check_content('[model_providers.relay]\nbase_url = "https://example.com"').valid)

    def test_base_url_rejects_unsafe_or_ambiguous_content_without_echoing(self):
        prefix = '[model_providers.relay]\nname = "Relay"\nbase_url = '
        for url in (
            "ftp://example.com", "https://user:SECRET@example.com/v1",
            "https://example.com?token=SECRET", "https://example.com#SECRET",
            "https://", "https://example.com:bad", "https://example.com/v 1",
            "https://example.com\\SECRET", "https://example.com/?", "https://example.com/#",
        ):
            with self.subTest(url=url):
                text = prefix + tomlkit.string(url).as_string()
                check = check_content(text)
                self.assertFalse(check.valid)
                self.assertNotIn("SECRET", repr(check))
        for url in ("https://example.com/v1", "http://localhost:8080/v1", "https://example.com/a%3Fb"):
            self.assertTrue(check_content(prefix + tomlkit.string(url).as_string()).valid)

    def test_mcp_transport_and_field_types_are_validated(self):
        prefix = '[mcp_servers.test]\n'
        invalid = [
            '', 'command = "python"\nurl = "https://example.com/mcp"',
            'command = 12', 'url = false', 'command = ""',
            'command = "python"\nargs = "--version"',
            'command = "python"\nargs = ["--version", 12]',
            'command = "python"\nenabled = 1',
        ]
        for suffix in invalid:
            with self.subTest(suffix=suffix):
                self.assertFalse(check_content(prefix + suffix).valid)
        self.assertTrue(check_content(prefix + 'command = "python"\nargs = ["--version"]\nenabled = true').valid)
        self.assertTrue(check_content(prefix + 'url = "https://example.com/mcp"').valid)

    def test_legacy_setting_warns_and_is_not_removed(self):
        text = 'disable_response_storage = true\n'
        check = check_content(text, True)
        self.assertTrue(check.valid)
        self.assertEqual(check.text, text)
        self.assertEqual(check.issues[0].severity, "warning")
        self.assertFalse(check.issues[0].fixable)

    def test_check_repr_does_not_contain_private_content(self):
        check = ContentCheck('experimental_bearer_token = "NEVER_PRINT_ME"')
        self.assertNotIn("NEVER_PRINT_ME", repr(check))


if __name__ == "__main__":
    unittest.main()
