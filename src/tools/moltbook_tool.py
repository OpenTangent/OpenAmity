import requests
from typing import List, Dict, Any
from core.cerebrum import Tool


class MoltbookTool(Tool):
    name = "Moltbook"
    icon = "🦞"
    color = "#FF9800"
    async_commands = []
    description = "Interact with the Moltbook social network for AI agents. Allows registering, posting, commenting, voting, reading feeds, managing communities (submolts), labels/roles, and notifications."
    commands = [
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

    BASE_URL = "https://www.moltbook.com/api/v1"

    def __init__(self, orchestrator=None):
        super().__init__(orchestrator)
        self.db = None
        self.api_key = ""
        if self.orchestrator and getattr(self.orchestrator, 'settings_manager', None):
            self.api_key = self.orchestrator.settings_manager.get_env(
                "MOLTBOOK_API_KEY") or ""

    def _get_headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Moltbook_register_account",
                "description": "Register a new agent account on Moltbook and obtain an API key. Returns a claim URL to give to the human user.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "agent_name": {"type": "STRING", "description": "Name of the agent."},
                        "description": {"type": "STRING", "description": "Short bio or description of the agent."}
                    },
                    "required": ["agent_name", "description"]
                }
            },
            {
                "name": "Moltbook_check_claim_status",
                "description": "Check if the human has claimed the Moltbook account yet.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Moltbook_post",
                "description": "Create a new post in a submolt on Moltbook.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt to post in (e.g. 'general')."},
                        "title": {"type": "STRING", "description": "Title of the post."},
                        "content": {"type": "STRING", "description": "Body text of the post."},
                        "url": {"type": "STRING", "description": "URL if it's a link post."},
                        "type": {"type": "STRING", "description": "'text', 'link', or 'image'. Default 'text'."}
                    },
                    "required": ["submolt_name", "title"]
                }
            },
            {
                "name": "Moltbook_verify_challenge",
                "description": "Submit an answer to an AI Verification Challenge to publish pending content.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "verification_code": {"type": "STRING", "description": "The verification code from the pending post/comment."},
                        "answer": {"type": "STRING", "description": "The math answer (with 2 decimal places, e.g., '525.00')."}
                    },
                    "required": ["verification_code", "answer"]
                }
            },
            {
                "name": "Moltbook_get_feed",
                "description": "Get the general feed or following feed.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "sort": {"type": "STRING", "description": "'hot', 'new', 'top'. Default 'hot'."},
                        "filter": {"type": "STRING", "description": "'all' or 'following'."},
                        "limit": {"type": "INTEGER", "description": "Limit of posts. Default 25."},
                        "cursor": {"type": "STRING", "description": "Cursor for next page."}
                    }
                }
            },
            {
                "name": "Moltbook_get_submolt_feed",
                "description": "Get posts from a specific submolt.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt."},
                        "sort": {"type": "STRING", "description": "'hot', 'new', 'top'. Default 'hot'."},
                        "limit": {"type": "INTEGER", "description": "Limit of posts."},
                        "cursor": {"type": "STRING", "description": "Cursor for next page."}
                    },
                    "required": ["submolt_name"]
                }
            },
            {
                "name": "Moltbook_comment",
                "description": "Add a comment to a post or reply to a comment.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "post_id": {"type": "STRING", "description": "The ID of the post to comment on."},
                        "content": {"type": "STRING", "description": "The text of the comment."},
                        "parent_id": {"type": "STRING", "description": "The ID of the parent comment if replying to a comment."}
                    },
                    "required": ["post_id", "content"]
                }
            },
            {
                "name": "Moltbook_get_comments",
                "description": "Get the comment tree for a specific post.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "post_id": {"type": "STRING", "description": "The ID of the post."},
                        "sort": {"type": "STRING", "description": "'best', 'new', 'old'. Default 'best'."},
                        "cursor": {"type": "STRING", "description": "Pagination cursor."}
                    },
                    "required": ["post_id"]
                }
            },
            {
                "name": "Moltbook_search",
                "description": "Perform an AI semantic search on Moltbook.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {"type": "STRING", "description": "Natural language search query."},
                        "type": {"type": "STRING", "description": "'posts', 'comments', or 'all'. Default 'all'."},
                        "limit": {"type": "INTEGER", "description": "Max results to return."}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "Moltbook_get_home",
                "description": "Check in at the Moltbook home endpoint. Can provide role briefings or moderator actions.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Moltbook_upvote_post",
                "description": "Upvote a post.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "post_id": {"type": "STRING", "description": "ID of the post."}
                    },
                    "required": ["post_id"]
                }
            },
            {
                "name": "Moltbook_downvote_post",
                "description": "Downvote a post.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "post_id": {"type": "STRING", "description": "ID of the post."}
                    },
                    "required": ["post_id"]
                }
            },
            {
                "name": "Moltbook_upvote_comment",
                "description": "Upvote a comment.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "comment_id": {"type": "STRING", "description": "ID of the comment."}
                    },
                    "required": ["comment_id"]
                }
            },
            {
                "name": "Moltbook_follow_agent",
                "description": "Follow another Molty (agent).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "agent_name": {"type": "STRING", "description": "Name of the agent to follow."}
                    },
                    "required": ["agent_name"]
                }
            },
            {
                "name": "Moltbook_unfollow_agent",
                "description": "Unfollow another Molty (agent).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "agent_name": {"type": "STRING", "description": "Name of the agent to unfollow."}
                    },
                    "required": ["agent_name"]
                }
            },
            {
                "name": "Moltbook_delete_post",
                "description": "Delete a post you created.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "post_id": {"type": "STRING", "description": "ID of the post to delete."}
                    },
                    "required": ["post_id"]
                }
            },
            {
                "name": "Moltbook_delete_comment",
                "description": "Delete a comment you created.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "comment_id": {"type": "STRING", "description": "ID of the comment to delete."}
                    },
                    "required": ["comment_id"]
                }
            },
            {
                "name": "Moltbook_get_agent_profile",
                "description": "Get your own agent profile or inspect another agent's profile by name.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "agent_name": {"type": "STRING", "description": "Optional name of the agent whose profile to retrieve. If omitted, returns your own profile."}
                    }
                }
            },
            {
                "name": "Moltbook_mark_notifications_read_by_post",
                "description": "Mark all notifications associated with a specific post as read.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "post_id": {"type": "STRING", "description": "ID of the post whose notifications should be marked as read."}
                    },
                    "required": ["post_id"]
                }
            },
            {
                "name": "Moltbook_mark_all_notifications_read",
                "description": "Mark all notifications across all posts and activity as read.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Moltbook_get_notifications",
                "description": "Get notifications for the agent (such as mentions, replies, upvotes).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "limit": {"type": "INTEGER", "description": "Limit of notifications to return. Default 20."},
                        "cursor": {"type": "STRING", "description": "Cursor for next page of notifications."}
                    }
                }
            },
            {
                "name": "Moltbook_get_post",
                "description": "Get details and content of a single post by its ID.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "post_id": {"type": "STRING", "description": "ID of the post to retrieve."}
                    },
                    "required": ["post_id"]
                }
            },
            {
                "name": "Moltbook_pin_post",
                "description": "Pin a post in its submolt (submolt moderator/owner only, max 3 pinned posts per submolt).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "post_id": {"type": "STRING", "description": "ID of the post to pin."}
                    },
                    "required": ["post_id"]
                }
            },
            {
                "name": "Moltbook_unpin_post",
                "description": "Unpin a pinned post in a submolt (submolt moderator/owner only).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "post_id": {"type": "STRING", "description": "ID of the post to unpin."}
                    },
                    "required": ["post_id"]
                }
            },
            {
                "name": "Moltbook_create_submolt",
                "description": "Create a new submolt community on Moltbook.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING", "description": "URL-safe name of the submolt (lowercase with hyphens, 2-30 chars)."},
                        "display_name": {"type": "STRING", "description": "Human-readable display name shown in the UI."},
                        "description": {"type": "STRING", "description": "Description of the submolt community."},
                        "allow_crypto": {"type": "BOOLEAN", "description": "Whether cryptocurrency content is allowed in this submolt. Default false."}
                    },
                    "required": ["name", "display_name"]
                }
            },
            {
                "name": "Moltbook_list_submolts",
                "description": "List all submolt communities on Moltbook.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Moltbook_get_submolt",
                "description": "Get information and metadata for a specific submolt community.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt to retrieve."},
                        "requester_id": {"type": "STRING", "description": "Your agent ID to include your role and moderator actions."}
                    },
                    "required": ["submolt_name"]
                }
            },
            {
                "name": "Moltbook_subscribe_submolt",
                "description": "Subscribe to a submolt to include its posts in your personal feed.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt to subscribe to."}
                    },
                    "required": ["submolt_name"]
                }
            },
            {
                "name": "Moltbook_unsubscribe_submolt",
                "description": "Unsubscribe from a submolt community.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt to unsubscribe from."}
                    },
                    "required": ["submolt_name"]
                }
            },
            {
                "name": "Moltbook_update_submolt_settings",
                "description": "Update settings for a submolt (submolt owner/moderator only).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt to update."},
                        "description": {"type": "STRING", "description": "New description for the submolt."},
                        "banner_color": {"type": "STRING", "description": "Banner hex color (e.g. '#1a1a2e')."},
                        "theme_color": {"type": "STRING", "description": "Theme hex color (e.g. '#ff4500')."}
                    },
                    "required": ["submolt_name"]
                }
            },
            {
                "name": "Moltbook_add_moderator",
                "description": "Add a moderator to a submolt community (submolt owner only).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt."},
                        "agent_name": {"type": "STRING", "description": "Name of the agent to appoint as moderator."},
                        "role": {"type": "STRING", "description": "Role to grant (default 'moderator')."}
                    },
                    "required": ["submolt_name", "agent_name"]
                }
            },
            {
                "name": "Moltbook_remove_moderator",
                "description": "Remove a moderator from a submolt community (submolt owner only).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt."},
                        "agent_name": {"type": "STRING", "description": "Name of the agent to remove from moderation."}
                    },
                    "required": ["submolt_name", "agent_name"]
                }
            },
            {
                "name": "Moltbook_list_moderators",
                "description": "List all appointed moderators for a submolt community.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt."}
                    },
                    "required": ["submolt_name"]
                }
            },
            {
                "name": "Moltbook_define_label",
                "description": "Define a new label (tag, status, or role) for a submolt (submolt moderator/owner only).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt to define label for."},
                        "key": {"type": "STRING", "description": "Identifier key (e.g. 'bug', 'open', 'triager')."},
                        "label": {"type": "STRING", "description": "Display label (e.g. 'Bug', 'Open', 'Bug Triager')."},
                        "color": {"type": "STRING", "description": "Color name: emerald, rose, amber, sky, violet, slate, indigo, teal, pink, orange."},
                        "kind": {"type": "STRING", "description": "'tag' (freeform multi-select on posts), 'status' (single-select on posts), or 'role' (assigned to agents)."},
                        "prompt": {"type": "STRING", "description": "For roles: recurring standing instruction prompt shown on /home check-in."},
                        "cadence_minutes": {"type": "INTEGER", "description": "For roles: minutes between reappearing briefings (e.g. 1440 for daily, 0 for every check-in)."}
                    },
                    "required": ["submolt_name", "key", "label", "color", "kind"]
                }
            },
            {
                "name": "Moltbook_list_labels",
                "description": "List all label definitions (tags, statuses, roles) for a submolt.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt."}
                    },
                    "required": ["submolt_name"]
                }
            },
            {
                "name": "Moltbook_list_roles",
                "description": "List all roles and their current assigned agent holders for a submolt.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "submolt_name": {"type": "STRING", "description": "Name of the submolt."}
                    },
                    "required": ["submolt_name"]
                }
            },
            {
                "name": "Moltbook_attach_label",
                "description": "Attach a tag/status to a post, or assign a role to an agent (moderators can attach any, agents can tag own posts).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "label_definition_id": {"type": "STRING", "description": "ID of the label definition to attach."},
                        "target_type": {"type": "STRING", "description": "'post' or 'agent'."},
                        "target_id": {"type": "STRING", "description": "ID of the target post or target agent."},
                        "placement": {"type": "STRING", "description": "Optional placement (e.g. 'metadata' or 'inline')."}
                    },
                    "required": ["label_definition_id", "target_type", "target_id"]
                }
            },
            {
                "name": "Moltbook_remove_label",
                "description": "Remove an attached label or role assignment by its attachment ID.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "attachment_id": {"type": "STRING", "description": "ID of the label attachment to remove."}
                    },
                    "required": ["attachment_id"]
                }
            },
            {
                "name": "Moltbook_update_agent_profile",
                "description": "Update your agent profile description and/or metadata.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "description": {"type": "STRING", "description": "Updated agent bio or description."},
                        "metadata": {"type": "OBJECT", "description": "Optional metadata dictionary for the agent profile."}
                    }
                }
            },
            {
                "name": "Moltbook_setup_owner_email",
                "description": "Send a setup link to your human owner's email for access to the Moltbook owner dashboard to manage your account and rotate API keys.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "email": {"type": "STRING", "description": "Email address of your human owner."}
                    },
                    "required": ["email"]
                }
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> Any:
        try:
            settings_mgr = getattr(self.orchestrator, 'settings_manager', None) if self.orchestrator else None
            is_low_token = settings_mgr.get("core.low-token-mode", False) if settings_mgr else False

            current_key = ""
            if settings_mgr:
                current_key = settings_mgr.get_env("MOLTBOOK_API_KEY") or ""
            if current_key != self.api_key:
                self.api_key = current_key

            if command == "register_account":
                agent_name = kwargs.get("agent_name")
                description = kwargs.get("description")
                payload = {"name": agent_name, "description": description}

                resp = requests.post(
                    f"{self.BASE_URL}/agents/register", json=payload, headers=self._get_headers())
                if resp.status_code != 200:
                    return f"Error registering: {resp.text}"

                data = resp.json()
                agent_data = data.get("agent", {})
                new_key = agent_data.get("api_key")
                claim_url = agent_data.get("claim_url")

                if new_key:
                    settings = self.orchestrator.settings_manager if self.orchestrator else None
                    if settings:
                        settings.set_env("MOLTBOOK_API_KEY", new_key)
                        settings.save()
                    self.api_key = new_key

                return f"Successfully registered! Instruct the human to visit this URL to verify the account: {claim_url}"

            if not self.api_key:
                return "Error: Moltbook API Key is not set. Please register an account first."

            if command == "check_claim_status":
                resp = requests.get(
                    f"{self.BASE_URL}/agents/status", headers=self._get_headers())
                return resp.text

            elif command == "post":
                payload = {
                    "submolt_name": kwargs.get("submolt_name"),
                    "title": kwargs.get("title")
                }
                if kwargs.get("content"):
                    payload["content"] = kwargs.get("content")
                if kwargs.get("url"):
                    payload["url"] = kwargs.get("url")
                if kwargs.get("type"):
                    payload["type"] = kwargs.get("type")

                resp = requests.post(
                    f"{self.BASE_URL}/posts", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "verify_challenge":
                payload = {
                    "verification_code": kwargs.get("verification_code"),
                    "answer": kwargs.get("answer")
                }
                resp = requests.post(
                    f"{self.BASE_URL}/verify", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "get_feed":
                params = {}
                limit = kwargs.get("limit", 25)
                if is_low_token:
                    limit = min(int(limit), 5)
                params["limit"] = limit

                if kwargs.get("sort"):
                    params["sort"] = kwargs.get("sort")
                if kwargs.get("filter"):
                    params["filter"] = kwargs.get("filter")
                if kwargs.get("cursor"):
                    params["cursor"] = kwargs.get("cursor")

                resp = requests.get(
                    f"{self.BASE_URL}/feed", params=params, headers=self._get_headers())
                return resp.text

            elif command == "get_submolt_feed":
                submolt = kwargs.get("submolt_name")
                params = {}
                limit = kwargs.get("limit", 25)
                if is_low_token:
                    limit = min(int(limit), 5)
                params["limit"] = limit

                if kwargs.get("sort"):
                    params["sort"] = kwargs.get("sort")
                if kwargs.get("cursor"):
                    params["cursor"] = kwargs.get("cursor")

                resp = requests.get(
                    f"{self.BASE_URL}/submolts/{submolt}/feed", params=params, headers=self._get_headers())
                return resp.text

            elif command == "comment":
                post_id = kwargs.get("post_id")
                payload = {"content": kwargs.get("content")}
                if kwargs.get("parent_id"):
                    payload["parent_id"] = kwargs.get("parent_id")

                resp = requests.post(
                    f"{self.BASE_URL}/posts/{post_id}/comments", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "get_comments":
                post_id = kwargs.get("post_id")
                params = {}
                if kwargs.get("sort"):
                    params["sort"] = kwargs.get("sort")
                if kwargs.get("cursor"):
                    params["cursor"] = kwargs.get("cursor")

                resp = requests.get(
                    f"{self.BASE_URL}/posts/{post_id}/comments", params=params, headers=self._get_headers())
                return resp.text

            elif command == "search":
                params = {"q": kwargs.get("query")}
                limit = kwargs.get("limit", 10)
                if is_low_token:
                    limit = min(int(limit), 5)
                params["limit"] = limit

                if kwargs.get("type"):
                    params["type"] = kwargs.get("type")

                resp = requests.get(
                    f"{self.BASE_URL}/search", params=params, headers=self._get_headers())
                return resp.text

            elif command == "get_home":
                resp = requests.get(
                    f"{self.BASE_URL}/home", headers=self._get_headers())
                return resp.text

            elif command == "upvote_post":
                post_id = kwargs.get("post_id")
                resp = requests.post(
                    f"{self.BASE_URL}/posts/{post_id}/upvote", headers=self._get_headers())
                return resp.text

            elif command == "downvote_post":
                post_id = kwargs.get("post_id")
                resp = requests.post(
                    f"{self.BASE_URL}/posts/{post_id}/downvote", headers=self._get_headers())
                return resp.text

            elif command == "upvote_comment":
                comment_id = kwargs.get("comment_id")
                resp = requests.post(
                    f"{self.BASE_URL}/comments/{comment_id}/upvote", headers=self._get_headers())
                return resp.text

            elif command == "follow_agent":
                agent_name = kwargs.get("agent_name")
                resp = requests.post(
                    f"{self.BASE_URL}/agents/{agent_name}/follow", headers=self._get_headers())
                return resp.text

            elif command == "unfollow_agent":
                agent_name = kwargs.get("agent_name")
                resp = requests.delete(
                    f"{self.BASE_URL}/agents/{agent_name}/follow", headers=self._get_headers())
                return resp.text

            elif command == "delete_post":
                post_id = kwargs.get("post_id")
                resp = requests.delete(
                    f"{self.BASE_URL}/posts/{post_id}", headers=self._get_headers())
                return resp.text

            elif command == "delete_comment":
                comment_id = kwargs.get("comment_id")
                resp = requests.delete(
                    f"{self.BASE_URL}/comments/{comment_id}", headers=self._get_headers())
                return resp.text

            elif command == "get_agent_profile":
                agent_name = kwargs.get("agent_name")
                if agent_name:
                    resp = requests.get(
                        f"{self.BASE_URL}/agents/profile", params={"name": agent_name}, headers=self._get_headers())
                else:
                    resp = requests.get(
                        f"{self.BASE_URL}/agents/me", headers=self._get_headers())
                return resp.text

            elif command == "mark_notifications_read_by_post":
                post_id = kwargs.get("post_id")
                resp = requests.post(
                    f"{self.BASE_URL}/notifications/read-by-post/{post_id}", headers=self._get_headers())
                return resp.text

            elif command == "mark_all_notifications_read":
                resp = requests.post(
                    f"{self.BASE_URL}/notifications/read-all", headers=self._get_headers())
                return resp.text

            elif command == "get_notifications":
                params = {}
                limit = kwargs.get("limit", 20)
                if is_low_token:
                    limit = min(int(limit), 5)
                params["limit"] = limit
                if kwargs.get("cursor"):
                    params["cursor"] = kwargs.get("cursor")
                resp = requests.get(
                    f"{self.BASE_URL}/notifications", params=params, headers=self._get_headers())
                return resp.text

            elif command == "get_post":
                post_id = kwargs.get("post_id")
                resp = requests.get(
                    f"{self.BASE_URL}/posts/{post_id}", headers=self._get_headers())
                return resp.text

            elif command == "pin_post":
                post_id = kwargs.get("post_id")
                resp = requests.post(
                    f"{self.BASE_URL}/posts/{post_id}/pin", headers=self._get_headers())
                return resp.text

            elif command == "unpin_post":
                post_id = kwargs.get("post_id")
                resp = requests.delete(
                    f"{self.BASE_URL}/posts/{post_id}/pin", headers=self._get_headers())
                return resp.text

            elif command == "create_submolt":
                payload = {
                    "name": kwargs.get("name"),
                    "display_name": kwargs.get("display_name")
                }
                if kwargs.get("description") is not None:
                    payload["description"] = kwargs.get("description")
                if kwargs.get("allow_crypto") is not None:
                    payload["allow_crypto"] = bool(kwargs.get("allow_crypto"))
                resp = requests.post(
                    f"{self.BASE_URL}/submolts", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "list_submolts":
                resp = requests.get(
                    f"{self.BASE_URL}/submolts", headers=self._get_headers())
                return resp.text

            elif command == "get_submolt":
                submolt_name = kwargs.get("submolt_name")
                params = {}
                if kwargs.get("requester_id"):
                    params["requester_id"] = kwargs.get("requester_id")
                resp = requests.get(
                    f"{self.BASE_URL}/submolts/{submolt_name}", params=params, headers=self._get_headers())
                return resp.text

            elif command == "subscribe_submolt":
                submolt_name = kwargs.get("submolt_name")
                resp = requests.post(
                    f"{self.BASE_URL}/submolts/{submolt_name}/subscribe", headers=self._get_headers())
                return resp.text

            elif command == "unsubscribe_submolt":
                submolt_name = kwargs.get("submolt_name")
                resp = requests.delete(
                    f"{self.BASE_URL}/submolts/{submolt_name}/subscribe", headers=self._get_headers())
                return resp.text

            elif command == "update_submolt_settings":
                submolt_name = kwargs.get("submolt_name")
                payload = {}
                if kwargs.get("description") is not None:
                    payload["description"] = kwargs.get("description")
                if kwargs.get("banner_color") is not None:
                    payload["banner_color"] = kwargs.get("banner_color")
                if kwargs.get("theme_color") is not None:
                    payload["theme_color"] = kwargs.get("theme_color")
                resp = requests.patch(
                    f"{self.BASE_URL}/submolts/{submolt_name}/settings", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "add_moderator":
                submolt_name = kwargs.get("submolt_name")
                payload = {
                    "agent_name": kwargs.get("agent_name"),
                    "role": kwargs.get("role", "moderator")
                }
                resp = requests.post(
                    f"{self.BASE_URL}/submolts/{submolt_name}/moderators", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "remove_moderator":
                submolt_name = kwargs.get("submolt_name")
                payload = {"agent_name": kwargs.get("agent_name")}
                resp = requests.delete(
                    f"{self.BASE_URL}/submolts/{submolt_name}/moderators", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "list_moderators":
                submolt_name = kwargs.get("submolt_name")
                resp = requests.get(
                    f"{self.BASE_URL}/submolts/{submolt_name}/moderators", headers=self._get_headers())
                return resp.text

            elif command == "define_label":
                submolt_name = kwargs.get("submolt_name")
                payload = {
                    "key": kwargs.get("key"),
                    "label": kwargs.get("label"),
                    "color": kwargs.get("color"),
                    "kind": kwargs.get("kind")
                }
                if kwargs.get("prompt") is not None:
                    payload["prompt"] = kwargs.get("prompt")
                if kwargs.get("cadence_minutes") is not None:
                    payload["cadence_minutes"] = kwargs.get("cadence_minutes")
                resp = requests.post(
                    f"{self.BASE_URL}/submolts/{submolt_name}/labels", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "list_labels":
                submolt_name = kwargs.get("submolt_name")
                resp = requests.get(
                    f"{self.BASE_URL}/submolts/{submolt_name}/labels", headers=self._get_headers())
                return resp.text

            elif command == "list_roles":
                submolt_name = kwargs.get("submolt_name")
                resp = requests.get(
                    f"{self.BASE_URL}/submolts/{submolt_name}/roles", headers=self._get_headers())
                return resp.text

            elif command == "attach_label":
                payload = {
                    "label_definition_id": kwargs.get("label_definition_id"),
                    "target_type": kwargs.get("target_type"),
                    "target_id": kwargs.get("target_id")
                }
                if kwargs.get("placement") is not None:
                    payload["placement"] = kwargs.get("placement")
                resp = requests.post(
                    f"{self.BASE_URL}/labels/attach", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "remove_label":
                attachment_id = kwargs.get("attachment_id")
                resp = requests.delete(
                    f"{self.BASE_URL}/labels/attach/{attachment_id}", headers=self._get_headers())
                return resp.text

            elif command == "update_agent_profile":
                payload = {}
                if kwargs.get("description") is not None:
                    payload["description"] = kwargs.get("description")
                if kwargs.get("metadata") is not None:
                    payload["metadata"] = kwargs.get("metadata")
                resp = requests.patch(
                    f"{self.BASE_URL}/agents/me", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "setup_owner_email":
                payload = {"email": kwargs.get("email")}
                resp = requests.post(
                    f"{self.BASE_URL}/agents/me/setup-owner-email", json=payload, headers=self._get_headers())
                return resp.text

            return f"Unknown command: {command}"

        except Exception as e:
            return f"Error executing Moltbook command '{command}': {str(e)}"
