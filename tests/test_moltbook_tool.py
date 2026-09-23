import os
import sys
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.moltbook_tool import MoltbookTool


@pytest.fixture
def mock_orchestrator():
    orchestrator = MagicMock()
    orchestrator.settings_manager = MagicMock()
    orchestrator.settings_manager.get_env.return_value = "test_key_123"
    orchestrator.settings_manager.get.return_value = False
    return orchestrator


def test_moltbook_tool_attributes(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    assert tool.name == "Moltbook"
    assert tool.icon == "🦞"
    assert tool.color == "#FF9800"
    assert tool.api_key == "test_key_123"

    expected_commands = [
        "register_account",
        "check_claim_status",
        "post",
        "get_feed",
        "get_submolt_feed",
        "comment",
        "get_comments",
        "search",
        "upvote_post",
        "downvote_post",
        "upvote_comment",
        "get_home",
        "follow_agent",
        "unfollow_agent",
        "verify_challenge",
        "delete_post",
        "delete_comment",
        "get_agent_profile",
        "mark_notifications_read_by_post",
        "mark_all_notifications_read",
        "get_notifications",
        "get_post",
        "pin_post",
        "unpin_post",
        "create_submolt",
        "list_submolts",
        "get_submolt",
        "subscribe_submolt",
        "unsubscribe_submolt",
        "update_submolt_settings",
        "add_moderator",
        "remove_moderator",
        "list_moderators",
        "define_label",
        "list_labels",
        "list_roles",
        "attach_label",
        "remove_label",
        "update_agent_profile",
        "setup_owner_email"
    ]
    for cmd in expected_commands:
        assert cmd in tool.commands, f"Command {cmd} missing from tool.commands"


def test_moltbook_tool_declarations(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    declarations = tool.get_tool_declarations()
    decl_names = {d["name"] for d in declarations}

    for cmd in tool.commands:
        expected_name = f"Moltbook_{cmd}"
        assert expected_name in decl_names, f"Declaration {expected_name} missing from get_tool_declarations"

    # Verify specific declaration structures
    by_name = {d["name"]: d for d in declarations}

    # mark_notifications_read_by_post
    mark_post_decl = by_name["Moltbook_mark_notifications_read_by_post"]
    assert "post_id" in mark_post_decl["parameters"]["properties"]
    assert "post_id" in mark_post_decl["parameters"]["required"]

    # mark_all_notifications_read
    mark_all_decl = by_name["Moltbook_mark_all_notifications_read"]
    assert mark_all_decl["parameters"]["type"] == "OBJECT"

    # get_notifications
    get_notifs_decl = by_name["Moltbook_get_notifications"]
    assert "limit" in get_notifs_decl["parameters"]["properties"]
    assert "cursor" in get_notifs_decl["parameters"]["properties"]

    # get_post
    get_post_decl = by_name["Moltbook_get_post"]
    assert "post_id" in get_post_decl["parameters"]["required"]

    # create_submolt
    create_submolt_decl = by_name["Moltbook_create_submolt"]
    assert "name" in create_submolt_decl["parameters"]["required"]
    assert "display_name" in create_submolt_decl["parameters"]["required"]

    # define_label
    define_label_decl = by_name["Moltbook_define_label"]
    assert "submolt_name" in define_label_decl["parameters"]["required"]
    assert "key" in define_label_decl["parameters"]["required"]
    assert "kind" in define_label_decl["parameters"]["required"]


def test_mark_notifications_read_by_post(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.post") as mock_post:
        mock_post.return_value.text = '{"success": true}'
        res = tool.execute("mark_notifications_read_by_post", post_id="post_999")
        assert res == '{"success": true}'
        mock_post.assert_called_once_with(
            "https://www.moltbook.com/api/v1/notifications/read-by-post/post_999",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_mark_all_notifications_read(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.post") as mock_post:
        mock_post.return_value.text = '{"success": true}'
        res = tool.execute("mark_all_notifications_read")
        assert res == '{"success": true}'
        mock_post.assert_called_once_with(
            "https://www.moltbook.com/api/v1/notifications/read-all",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_get_notifications(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.get") as mock_get:
        mock_get.return_value.text = '{"notifications": []}'
        res = tool.execute("get_notifications", limit=15, cursor="cur_abc")
        assert res == '{"notifications": []}'
        mock_get.assert_called_once_with(
            "https://www.moltbook.com/api/v1/notifications",
            params={"limit": 15, "cursor": "cur_abc"},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_get_notifications_low_token_mode(mock_orchestrator):
    mock_orchestrator.settings_manager.get.side_effect = lambda k, default=None: True if k == "core.low-token-mode" else default
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.get") as mock_get:
        mock_get.return_value.text = '{"notifications": []}'
        res = tool.execute("get_notifications", limit=25)
        assert res == '{"notifications": []}'
        mock_get.assert_called_once_with(
            "https://www.moltbook.com/api/v1/notifications",
            params={"limit": 5},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_get_post(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.get") as mock_get:
        mock_get.return_value.text = '{"post": {"id": "p123"}}'
        res = tool.execute("get_post", post_id="p123")
        assert res == '{"post": {"id": "p123"}}'
        mock_get.assert_called_once_with(
            "https://www.moltbook.com/api/v1/posts/p123",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_pin_and_unpin_post(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.post") as mock_post, patch("requests.delete") as mock_delete:
        mock_post.return_value.text = '{"pinned": true}'
        mock_delete.return_value.text = '{"unpinned": true}'

        res_pin = tool.execute("pin_post", post_id="p123")
        assert res_pin == '{"pinned": true}'
        mock_post.assert_called_once_with(
            "https://www.moltbook.com/api/v1/posts/p123/pin",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        res_unpin = tool.execute("unpin_post", post_id="p123")
        assert res_unpin == '{"unpinned": true}'
        mock_delete.assert_called_once_with(
            "https://www.moltbook.com/api/v1/posts/p123/pin",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_submolt_lifecycle(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.post") as mock_post, patch("requests.get") as mock_get, patch("requests.delete") as mock_delete:
        mock_post.return_value.text = '{"success": true}'
        mock_get.return_value.text = '{"submolts": []}'
        mock_delete.return_value.text = '{"success": true}'

        # create_submolt
        res_create = tool.execute("create_submolt", name="test-sub", display_name="Test Sub", description="Desc", allow_crypto=True)
        assert res_create == '{"success": true}'
        mock_post.assert_called_with(
            "https://www.moltbook.com/api/v1/submolts",
            json={"name": "test-sub", "display_name": "Test Sub", "description": "Desc", "allow_crypto": True},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # list_submolts
        res_list = tool.execute("list_submolts")
        assert res_list == '{"submolts": []}'
        mock_get.assert_called_with(
            "https://www.moltbook.com/api/v1/submolts",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # get_submolt
        tool.execute("get_submolt", submolt_name="test-sub", requester_id="agent-007")
        mock_get.assert_called_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub",
            params={"requester_id": "agent-007"},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # subscribe_submolt
        tool.execute("subscribe_submolt", submolt_name="test-sub")
        mock_post.assert_called_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub/subscribe",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # unsubscribe_submolt
        tool.execute("unsubscribe_submolt", submolt_name="test-sub")
        mock_delete.assert_called_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub/subscribe",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_submolt_moderation(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.patch") as mock_patch, patch("requests.post") as mock_post, patch("requests.delete") as mock_delete, patch("requests.get") as mock_get:
        mock_patch.return_value.text = '{"updated": true}'
        mock_post.return_value.text = '{"added": true}'
        mock_delete.return_value.text = '{"removed": true}'
        mock_get.return_value.text = '{"moderators": []}'

        # update_submolt_settings
        tool.execute("update_submolt_settings", submolt_name="test-sub", description="New bio", banner_color="#000", theme_color="#fff")
        mock_patch.assert_called_once_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub/settings",
            json={"description": "New bio", "banner_color": "#000", "theme_color": "#fff"},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # add_moderator
        tool.execute("add_moderator", submolt_name="test-sub", agent_name="ModAgent", role="moderator")
        mock_post.assert_called_once_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub/moderators",
            json={"agent_name": "ModAgent", "role": "moderator"},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # remove_moderator
        tool.execute("remove_moderator", submolt_name="test-sub", agent_name="ModAgent")
        mock_delete.assert_called_once_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub/moderators",
            json={"agent_name": "ModAgent"},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # list_moderators
        tool.execute("list_moderators", submolt_name="test-sub")
        mock_get.assert_called_once_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub/moderators",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_labels_and_roles(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.post") as mock_post, patch("requests.get") as mock_get, patch("requests.delete") as mock_delete:
        mock_post.return_value.text = '{"success": true}'
        mock_get.return_value.text = '{"items": []}'
        mock_delete.return_value.text = '{"deleted": true}'

        # define_label
        tool.execute(
            "define_label",
            submolt_name="test-sub",
            key="triager",
            label="Bug Triager",
            color="violet",
            kind="role",
            prompt="Sweep bugs",
            cadence_minutes=1440
        )
        mock_post.assert_called_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub/labels",
            json={
                "key": "triager",
                "label": "Bug Triager",
                "color": "violet",
                "kind": "role",
                "prompt": "Sweep bugs",
                "cadence_minutes": 1440
            },
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # list_labels
        tool.execute("list_labels", submolt_name="test-sub")
        mock_get.assert_called_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub/labels",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # list_roles
        tool.execute("list_roles", submolt_name="test-sub")
        mock_get.assert_called_with(
            "https://www.moltbook.com/api/v1/submolts/test-sub/roles",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # attach_label
        tool.execute(
            "attach_label",
            label_definition_id="def_123",
            target_type="post",
            target_id="post_456",
            placement="inline"
        )
        mock_post.assert_called_with(
            "https://www.moltbook.com/api/v1/labels/attach",
            json={
                "label_definition_id": "def_123",
                "target_type": "post",
                "target_id": "post_456",
                "placement": "inline"
            },
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # remove_label
        tool.execute("remove_label", attachment_id="att_789")
        mock_delete.assert_called_with(
            "https://www.moltbook.com/api/v1/labels/attach/att_789",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_profile_and_account(mock_orchestrator):
    tool = MoltbookTool(mock_orchestrator)
    with patch("requests.get") as mock_get, patch("requests.patch") as mock_patch, patch("requests.post") as mock_post:
        mock_get.return_value.text = '{"agent": {}}'
        mock_patch.return_value.text = '{"success": true}'
        mock_post.return_value.text = '{"success": true}'

        # get_agent_profile for own agent
        tool.execute("get_agent_profile")
        mock_get.assert_called_with(
            "https://www.moltbook.com/api/v1/agents/me",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # get_agent_profile for another agent
        tool.execute("get_agent_profile", agent_name="OtherMolty")
        mock_get.assert_called_with(
            "https://www.moltbook.com/api/v1/agents/profile",
            params={"name": "OtherMolty"},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # update_agent_profile
        tool.execute("update_agent_profile", description="Updated bio", metadata={"role": "assistant"})
        mock_patch.assert_called_once_with(
            "https://www.moltbook.com/api/v1/agents/me",
            json={"description": "Updated bio", "metadata": {"role": "assistant"}},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )

        # setup_owner_email
        tool.execute("setup_owner_email", email="owner@example.com")
        mock_post.assert_called_once_with(
            "https://www.moltbook.com/api/v1/agents/me/setup-owner-email",
            json={"email": "owner@example.com"},
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )


def test_missing_api_key_error():
    mock_orch = MagicMock()
    mock_orch.settings_manager = None
    tool = MoltbookTool(mock_orch)
    tool.api_key = ""

    res = tool.execute("get_feed")
    assert "Moltbook API Key is not set" in res

    res2 = tool.execute("mark_all_notifications_read")
    assert "Moltbook API Key is not set" in res2


def test_cerebrum_integration(mock_orchestrator):
    from core.cerebrum import Cerebrum
    cerebrum = Cerebrum(orchestrator=mock_orchestrator, settings_manager=mock_orchestrator.settings_manager)
    tool = MoltbookTool(mock_orchestrator)
    cerebrum.register_skill(tool)

    with patch("requests.post") as mock_post:
        mock_post.return_value.text = '{"success": true}'
        result = cerebrum.execute_tool_call(
            "Moltbook_mark_notifications_read_by_post",
            {"post_id": "test_post_xyz"}
        )
        assert result == '{"success": true}'
        mock_post.assert_called_once_with(
            "https://www.moltbook.com/api/v1/notifications/read-by-post/test_post_xyz",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test_key_123"}
        )
