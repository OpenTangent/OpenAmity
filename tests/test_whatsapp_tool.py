import os
import sys
import tempfile
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.whatsapp_tool import WhatsAppSkill


@pytest.fixture
def mock_whatsapp_tool():
    """Fixture providing a WhatsAppSkill instance with mocked daemon and ready status."""
    mock_orch = MagicMock()
    mock_orch.agent_id = "test-agent-wa"
    mock_settings = MagicMock()
    mock_settings.get.side_effect = lambda key, default=None: {
        "core.tts.provider": "piper",
        "core.tts.piper.model-name-piper": "en_GB-cori-high",
        "core.antigravity.agy-mode": False,
    }.get(key, default)
    mock_orch.settings_manager = mock_settings

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    with patch("core.whatsapp_daemon.WhatsAppDaemon") as mock_daemon_cls:
        daemon_instance = MagicMock()
        daemon_instance.base_url = "http://127.0.0.1:3000"
        daemon_instance.data_dir = "/tmp/dummy_wa_data"
        mock_daemon_cls.return_value = daemon_instance

        with patch("requests.get", return_value=mock_status_resp):
            tool = WhatsAppSkill(orchestrator=mock_orch)
            yield tool, mock_orch


def test_whatsapp_tool_declarations(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    decls = tool.get_tool_declarations()
    names = [d["name"] for d in decls]
    assert "WhatsApp_send_voice" in names
    assert "WhatsApp_send" in names
    assert "WhatsApp_send_location" in names
    assert "WhatsApp_send_contact" in names
    assert "WhatsApp_unread" in names


def test_whatsapp_send_voice_with_piper_tts(mock_whatsapp_tool):
    tool, mock_orch = mock_whatsapp_tool

    def fake_synth(text, output_path, agent_id=None, settings=None, voice=None):
        with open(output_path, "wb") as f:
            f.write(b"OggOpusDummyAudioData")
        return output_path

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}
    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True, "target": "123456789@c.us"}

    with patch("core.audio_output.synthesize_speech_to_file", side_effect=fake_synth) as mock_synth_fn:
        with patch("requests.get", return_value=mock_status_resp):
            with patch("requests.post", return_value=mock_post_resp) as mock_post:
                result = tool.execute("send_voice", target="Alice", message="Hello from Piper voice notes!")

                assert result == "Voice message sent to Alice successfully."
                mock_synth_fn.assert_called_once()
                call_kwargs = mock_synth_fn.call_args[1]
                assert call_kwargs["text"] == "Hello from Piper voice notes!"
                assert call_kwargs["agent_id"] == "test-agent-wa"
                assert call_kwargs["settings"] == mock_orch.settings_manager

                mock_post.assert_called_once()
                args, kwargs = mock_post.call_args
                assert args[0] == "http://127.0.0.1:3000/send"
                assert kwargs["data"]["target"] == "Alice"
                assert kwargs["data"]["isVoice"] == "true"
                assert "media" in kwargs["files"]


def test_whatsapp_send_voice_with_reply_to(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool

    def fake_synth(text, output_path, agent_id=None, settings=None, voice=None):
        with open(output_path, "wb") as f:
            f.write(b"OggOpusDummyAudioData")
        return output_path

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}
    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True}

    with patch("core.audio_output.synthesize_speech_to_file", side_effect=fake_synth):
        with patch("requests.get", return_value=mock_status_resp):
            with patch("requests.post", return_value=mock_post_resp) as mock_post:
                result = tool.execute("send_voice", target="Bob", message="Replying to your note", reply_to="msg_98765")
                assert "successfully" in result
                assert mock_post.call_args[1]["data"]["reply_to"] == "msg_98765"


def test_whatsapp_send_voice_agy_mode_not_blocked(mock_whatsapp_tool):
    tool, mock_orch = mock_whatsapp_tool
    mock_orch.settings_manager.get.side_effect = lambda key, default=None: {
        "core.tts.provider": "piper",
        "core.antigravity.agy-mode": True,
    }.get(key, default)

    def fake_synth(text, output_path, agent_id=None, settings=None, voice=None):
        with open(output_path, "wb") as f:
            f.write(b"OggOpusDummyAudioData")
        return output_path

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}
    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True}

    with patch("core.audio_output.synthesize_speech_to_file", side_effect=fake_synth):
        with patch("requests.get", return_value=mock_status_resp):
            with patch("requests.post", return_value=mock_post_resp):
                result = tool.execute("send_voice", target="Charlie", message="Voice note in agy mode")
                assert "successfully" in result
                assert "unavailable when agy-mode is active" not in result


def test_whatsapp_send_voice_synthesis_failure(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    with patch("core.audio_output.synthesize_speech_to_file", side_effect=RuntimeError("Piper ONNX model missing")):
        with patch("requests.get", return_value=mock_status_resp):
            result = tool.execute("send_voice", target="Dave", message="Will fail synthesis")
            assert "Failed to generate TTS audio: Piper ONNX model missing" in result


def test_whatsapp_send_voice_bridge_error_response(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool

    def fake_synth(text, output_path, agent_id=None, settings=None, voice=None):
        with open(output_path, "wb") as f:
            f.write(b"OggOpusDummyAudioData")
        return output_path

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}
    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"error": "Target contact not found on WhatsApp"}

    with patch("core.audio_output.synthesize_speech_to_file", side_effect=fake_synth):
        with patch("requests.get", return_value=mock_status_resp):
            with patch("requests.post", return_value=mock_post_resp):
                result = tool.execute("send_voice", target="InvalidUser", message="Test message")
                assert result == "Error: Target contact not found on WhatsApp"


def test_whatsapp_send_voice_missing_parameters(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}
    with patch("requests.get", return_value=mock_status_resp):
        result = tool.execute("send_voice", target="", message="")
        assert "Usage: send_voice <target> <message text to speak>" in result


def test_whatsapp_unread_with_voice_message_attaches_media(mock_whatsapp_tool, tmp_path):
    tool, _ = mock_whatsapp_tool
    voice_file = tmp_path / "voice_msg.ogg"
    voice_file.write_bytes(b"OggS" + b"\x00" * 50)

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_unread_resp = MagicMock()
    mock_unread_resp.json.return_value = {
        "unread": [
            {
                "id": "msg_001",
                "chatId": "123456789@c.us",
                "chatName": "Alice",
                "senderName": "Alice",
                "senderNumber": "123456789",
                "content": "",
                "type": "ptt",
                "hasMedia": True,
                "mediaPath": str(voice_file)
            }
        ]
    }

    def fake_get(url, *args, **kwargs):
        if url.endswith("/status"):
            return mock_status_resp
        elif url.endswith("/unread"):
            return mock_unread_resp
        return MagicMock()

    with patch("requests.get", side_effect=fake_get):
        res = tool.execute("unread")
        assert isinstance(res, dict)
        assert "result" in res
        assert "media" in res
        assert str(voice_file) in res["media"]
        assert "Voice message audio has been attached" in res["result"]
        assert "Media_transcribe_audio" in res["result"]


def test_whatsapp_unread_without_media_returns_string(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_unread_resp = MagicMock()
    mock_unread_resp.json.return_value = {
        "unread": [
            {
                "id": "msg_002",
                "chatId": "123456789@c.us",
                "chatName": "Bob",
                "senderName": "Bob",
                "senderNumber": "123456789",
                "content": "Hello there!",
                "type": "chat",
                "hasMedia": False
            }
        ]
    }

    def fake_get(url, *args, **kwargs):
        if url.endswith("/status"):
            return mock_status_resp
        elif url.endswith("/unread"):
            return mock_unread_resp
        return MagicMock()

    with patch("requests.get", side_effect=fake_get):
        res = tool.execute("unread")
        assert isinstance(res, str)
        assert "Bob (+123456789): Hello there!" in res
        assert "Unread Messages:" in res


def test_whatsapp_unread_with_location_message(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_unread_resp = MagicMock()
    mock_unread_resp.json.return_value = {
        "unread": [
            {
                "id": "false_12345@c.us_LOC001",
                "chatId": "123456789@c.us",
                "chatName": "Andrew",
                "senderName": "Andrew",
                "senderNumber": "123456789",
                "content": "[Location Pin: Latitude: -29.7408043 | Longitude: 30.831676 | Maps: https://maps.google.com/?q=-29.7408043,30.831676]",
                "type": "location",
                "hasMedia": False,
                "location": {
                    "lat": -29.7408043,
                    "lng": 30.831676,
                    "name": "",
                    "address": "",
                    "comment": "",
                    "url": "",
                    "isLive": False
                }
            }
        ]
    }

    def fake_get(url, *args, **kwargs):
        if url.endswith("/status"):
            return mock_status_resp
        elif url.endswith("/unread"):
            return mock_unread_resp
        return MagicMock()

    with patch("requests.get", side_effect=fake_get):
        res = tool.execute("unread")
        # Location message should not attach media files
        assert isinstance(res, str)
        assert "[Location Pin: Latitude: -29.7408043 | Longitude: 30.831676 | Maps: https://maps.google.com/?q=-29.7408043,30.831676]" in res
        assert "WhatsApp_send_location" in res


def test_whatsapp_send_location_success(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True, "target": "123456789@c.us"}

    with patch("requests.get", return_value=mock_status_resp):
        with patch("requests.post", return_value=mock_post_resp) as mock_post:
            result = tool.execute(
                "send_location",
                target="Alice",
                latitude=-29.740804,
                longitude=30.831676,
                name="Kloof Gorge",
                address="Kloof, KwaZulu-Natal",
                url="https://example.com"
            )

            assert result == "Location pin sent to Alice successfully."
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert args[0] == "http://127.0.0.1:3000/send_location"
            payload = kwargs["json"]
            assert payload["target"] == "Alice"
            assert payload["lat"] == -29.740804
            assert payload["lng"] == 30.831676
            assert payload["name"] == "Kloof Gorge"
            assert payload["address"] == "Kloof, KwaZulu-Natal"
            assert payload["url"] == "https://example.com"


def test_whatsapp_send_location_with_reply_to(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True}

    with patch("requests.get", return_value=mock_status_resp):
        with patch("requests.post", return_value=mock_post_resp) as mock_post:
            result = tool.execute(
                "send_location",
                target="Bob",
                latitude=51.5074,
                longitude=-0.1278,
                reply_to="msg_pin_123"
            )
            assert "successfully" in result
            assert mock_post.call_args[1]["json"]["reply_to"] == "msg_pin_123"


def test_whatsapp_send_location_missing_parameters(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    with patch("requests.get", return_value=mock_status_resp):
        res1 = tool.execute("send_location", target="")
        assert "Usage: send_location" in res1

        res2 = tool.execute("send_location", target="Charlie", latitude="not_a_number", longitude=10.0)
        assert "Error: Latitude and longitude must be valid numbers." in res2


def test_whatsapp_send_location_bridge_error(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"error": "Target not found"}

    with patch("requests.get", return_value=mock_status_resp):
        with patch("requests.post", return_value=mock_post_resp):
            res = tool.execute("send_location", target="Unknown", latitude=10.0, longitude=20.0)
            assert res == "Error: Target not found"


def test_whatsapp_send_contact_success(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True, "target": "123456789@c.us"}

    with patch("requests.get", return_value=mock_status_resp):
        with patch("requests.post", return_value=mock_post_resp) as mock_post:
            result = tool.execute(
                "send_contact",
                target="Alice",
                contact_name="Bob Smith",
                contact_phone="+1234567890"
            )

            assert result == "Contact card for Bob Smith sent to Alice successfully."
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert args[0] == "http://127.0.0.1:3000/send_contact"
            payload = kwargs["json"]
            assert payload["target"] == "Alice"
            assert payload["name"] == "Bob Smith"
            assert payload["phone"] == "+1234567890"


def test_whatsapp_send_contact_with_org_and_email(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True}

    with patch("requests.get", return_value=mock_status_resp):
        with patch("requests.post", return_value=mock_post_resp) as mock_post:
            result = tool.execute(
                "send_contact",
                target="Alice",
                contact_name="Bob Smith",
                contact_phone="+1234567890",
                organization="Acme Corp",
                email="bob@example.com"
            )
            assert "successfully" in result
            payload = mock_post.call_args[1]["json"]
            assert payload["organization"] == "Acme Corp"
            assert payload["email"] == "bob@example.com"


def test_whatsapp_send_contact_with_reply_to(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True}

    with patch("requests.get", return_value=mock_status_resp):
        with patch("requests.post", return_value=mock_post_resp) as mock_post:
            result = tool.execute(
                "send_contact",
                target="Alice",
                contact_name="Bob",
                contact_phone="+1234567890",
                reply_to="msg_contact_ref"
            )
            assert "successfully" in result
            assert mock_post.call_args[1]["json"]["reply_to"] == "msg_contact_ref"


def test_whatsapp_send_contact_with_raw_vcard(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True}

    sample_vcard = "BEGIN:VCARD\nVERSION:3.0\nFN:Dr. Charlie\nTEL;waid=999:+999\nEND:VCARD"
    with patch("requests.get", return_value=mock_status_resp):
        with patch("requests.post", return_value=mock_post_resp) as mock_post:
            result = tool.execute(
                "send_contact",
                target="Dave",
                vcard=sample_vcard
            )
            assert "successfully" in result
            assert mock_post.call_args[1]["json"]["vcard"] == sample_vcard


def test_whatsapp_send_contact_multi_batch(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"success": True}

    batch = [
        {"name": "Alice", "phone": "+111"},
        {"name": "Bob", "phone": "+222"}
    ]
    with patch("requests.get", return_value=mock_status_resp):
        with patch("requests.post", return_value=mock_post_resp) as mock_post:
            result = tool.execute(
                "send_contact",
                target="Group1",
                contacts=batch
            )
            assert "2 contacts sent to Group1 successfully." in result
            assert mock_post.call_args[1]["json"]["contacts"] == batch


def test_whatsapp_send_contact_missing_parameters(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    with patch("requests.get", return_value=mock_status_resp):
        res1 = tool.execute("send_contact", target="")
        assert "Usage: send_contact" in res1

        res2 = tool.execute("send_contact", target="Dave")
        assert "Usage: send_contact" in res2


def test_whatsapp_send_contact_bridge_error(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"error": "Target contact not found on WhatsApp"}

    with patch("requests.get", return_value=mock_status_resp):
        with patch("requests.post", return_value=mock_post_resp):
            res = tool.execute("send_contact", target="Invalid", contact_name="Ghost", contact_phone="+000")
            assert res == "Error: Target contact not found on WhatsApp"


def test_whatsapp_unread_with_single_contact_card(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_unread_resp = MagicMock()
    mock_unread_resp.json.return_value = {
        "unread": [
            {
                "id": "false_12345@c.us_VCARD001",
                "chatId": "123456789@c.us",
                "chatName": "Andrew",
                "senderName": "Andrew",
                "senderNumber": "123456789",
                "content": "[Contact Card: Name: Bob Smith | Phone: +1234567890 | WhatsApp ID: 1234567890@c.us | Org: Acme Corp]",
                "type": "vcard",
                "hasMedia": False,
                "contacts": [
                    {
                        "name": "Bob Smith",
                        "phone": "+1234567890",
                        "whatsappId": "1234567890@c.us",
                        "org": "Acme Corp"
                    }
                ]
            }
        ]
    }

    def fake_get(url, *args, **kwargs):
        if url.endswith("/status"):
            return mock_status_resp
        elif url.endswith("/unread"):
            return mock_unread_resp
        return MagicMock()

    with patch("requests.get", side_effect=fake_get):
        res = tool.execute("unread")
        # Contact cards must not be treated as binary media downloads
        assert isinstance(res, str)
        assert "[Contact Card: Name: Bob Smith | Phone: +1234567890 | WhatsApp ID: 1234567890@c.us | Org: Acme Corp]" in res
        assert "WhatsApp_send_contact" in res


def test_whatsapp_unread_with_multi_contact_cards(mock_whatsapp_tool):
    tool, _ = mock_whatsapp_tool
    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"ready": True}

    mock_unread_resp = MagicMock()
    mock_unread_resp.json.return_value = {
        "unread": [
            {
                "id": "false_12345@c.us_VCARD002",
                "chatId": "123456789@c.us",
                "chatName": "Andrew",
                "senderName": "Andrew",
                "senderNumber": "123456789",
                "content": "[Contact Cards (2): 1. Name: Alice | Phone: +111 | WhatsApp ID: 111@c.us ; 2. Name: Bob | Phone: +222 | WhatsApp ID: 222@c.us]",
                "type": "multi_vcard",
                "hasMedia": False,
                "contacts": [
                    {"name": "Alice", "phone": "+111", "whatsappId": "111@c.us"},
                    {"name": "Bob", "phone": "+222", "whatsappId": "222@c.us"}
                ]
            }
        ]
    }

    def fake_get(url, *args, **kwargs):
        if url.endswith("/status"):
            return mock_status_resp
        elif url.endswith("/unread"):
            return mock_unread_resp
        return MagicMock()

    with patch("requests.get", side_effect=fake_get):
        res = tool.execute("unread")
        assert isinstance(res, str)
        assert "[Contact Cards (2): 1. Name: Alice | Phone: +111 | WhatsApp ID: 111@c.us ; 2. Name: Bob | Phone: +222 | WhatsApp ID: 222@c.us]" in res
        assert "WhatsApp_send_contact" in res

