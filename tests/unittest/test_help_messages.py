import pytest

from pr_agent.servers.help import HelpMessage


class TestGeneralCommandsText:
    @pytest.mark.parametrize("command", [
        "/review", "/describe", "/improve", "/ask", "/update_changelog",
        "/help_docs", "/add_docs", "/generate_labels", "/config",
    ])
    def test_lists_all_general_commands(self, command):
        assert command in HelpMessage.get_general_commands_text()

    def test_bot_help_text_embeds_commands_text(self):
        bot_help = HelpMessage.get_general_bot_help_text()
        assert HelpMessage.get_general_commands_text() in bot_help
        assert bot_help.startswith("> To invoke the PR-Agent")


class TestUsageGuides:
    @pytest.mark.parametrize("guide_method,tool_marker", [
        (HelpMessage.get_review_usage_guide, "pr_reviewer"),
        (HelpMessage.get_describe_usage_guide, "pr_description"),
        (HelpMessage.get_improve_usage_guide, "pr_code_suggestions"),
    ])
    def test_configurable_tool_guides_show_config_section(self, guide_method, tool_marker):
        output = guide_method()
        assert output.startswith("**Overview:**")
        assert tool_marker in output

    @pytest.mark.parametrize("guide_method,command", [
        (HelpMessage.get_review_usage_guide, "/review"),
        (HelpMessage.get_describe_usage_guide, "/describe"),
        (HelpMessage.get_ask_usage_guide, "/ask"),
        (HelpMessage.get_improve_usage_guide, "/improve"),
        (HelpMessage.get_help_docs_usage_guide, "/help_docs"),
    ])
    def test_each_guide_mentions_its_command(self, guide_method, command):
        output = guide_method()
        assert command in output

    def test_describe_guide_includes_general_bot_help(self):
        output = HelpMessage.get_describe_usage_guide()
        assert HelpMessage.get_general_bot_help_text() in output

    def test_guides_link_to_documentation_site(self):
        for guide in (HelpMessage.get_review_usage_guide, HelpMessage.get_describe_usage_guide,
                      HelpMessage.get_ask_usage_guide, HelpMessage.get_improve_usage_guide,
                      HelpMessage.get_help_docs_usage_guide):
            assert "https://pr-agent-docs.codium.ai/" in guide()
