import os
import json
import requests
from typing import List, Dict, Any
from core.cerebrum import Tool
from core.settings_manager import SettingsManager

class MoltbookTool(Tool):
    name = "Moltbook"
    description = "Interact with the Moltbook social network for AI agents. Allows registering, posting, commenting, voting, and reading feeds."
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
        "verify_challenge"
    ]
    
    BASE_URL = "https://www.moltbook.com/api/v1"

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("MOLTBOOK_API_KEY", "")

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
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> str:
        try:
            current_key = os.getenv("MOLTBOOK_API_KEY", "")
            if current_key != self.api_key:
                self.api_key = current_key

            if command == "register_account":
                agent_name = kwargs.get("agent_name")
                description = kwargs.get("description")
                payload = {"name": agent_name, "description": description}
                
                resp = requests.post(f"{self.BASE_URL}/agents/register", json=payload, headers=self._get_headers())
                if resp.status_code != 200:
                    return f"Error registering: {resp.text}"
                
                data = resp.json()
                agent_data = data.get("agent", {})
                new_key = agent_data.get("api_key")
                claim_url = agent_data.get("claim_url")
                
                if new_key:
                    settings = SettingsManager()
                    settings.set_env("MOLTBOOK_API_KEY", new_key)
                    settings.save()
                    self.api_key = new_key
                
                return f"Successfully registered! Instruct the human to visit this URL to verify the account: {claim_url}"

            if not self.api_key:
                return "Error: Moltbook API Key is not set. Please register an account first."

            if command == "check_claim_status":
                resp = requests.get(f"{self.BASE_URL}/agents/status", headers=self._get_headers())
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
                    
                resp = requests.post(f"{self.BASE_URL}/posts", json=payload, headers=self._get_headers())
                return resp.text
                
            elif command == "verify_challenge":
                payload = {
                    "verification_code": kwargs.get("verification_code"),
                    "answer": kwargs.get("answer")
                }
                resp = requests.post(f"{self.BASE_URL}/verify", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "get_feed":
                params = {}
                if kwargs.get("sort"): params["sort"] = kwargs.get("sort")
                if kwargs.get("filter"): params["filter"] = kwargs.get("filter")
                if kwargs.get("limit"): params["limit"] = kwargs.get("limit")
                if kwargs.get("cursor"): params["cursor"] = kwargs.get("cursor")
                
                resp = requests.get(f"{self.BASE_URL}/feed", params=params, headers=self._get_headers())
                return resp.text

            elif command == "get_submolt_feed":
                submolt = kwargs.get("submolt_name")
                params = {}
                if kwargs.get("sort"): params["sort"] = kwargs.get("sort")
                if kwargs.get("limit"): params["limit"] = kwargs.get("limit")
                if kwargs.get("cursor"): params["cursor"] = kwargs.get("cursor")
                
                resp = requests.get(f"{self.BASE_URL}/submolts/{submolt}/feed", params=params, headers=self._get_headers())
                return resp.text

            elif command == "comment":
                post_id = kwargs.get("post_id")
                payload = {"content": kwargs.get("content")}
                if kwargs.get("parent_id"):
                    payload["parent_id"] = kwargs.get("parent_id")
                    
                resp = requests.post(f"{self.BASE_URL}/posts/{post_id}/comments", json=payload, headers=self._get_headers())
                return resp.text

            elif command == "get_comments":
                post_id = kwargs.get("post_id")
                params = {}
                if kwargs.get("sort"): params["sort"] = kwargs.get("sort")
                if kwargs.get("cursor"): params["cursor"] = kwargs.get("cursor")
                
                resp = requests.get(f"{self.BASE_URL}/posts/{post_id}/comments", params=params, headers=self._get_headers())
                return resp.text

            elif command == "search":
                params = {"q": kwargs.get("query")}
                if kwargs.get("type"): params["type"] = kwargs.get("type")
                if kwargs.get("limit"): params["limit"] = kwargs.get("limit")
                
                resp = requests.get(f"{self.BASE_URL}/search", params=params, headers=self._get_headers())
                return resp.text

            elif command == "get_home":
                resp = requests.get(f"{self.BASE_URL}/home", headers=self._get_headers())
                return resp.text

            elif command == "upvote_post":
                post_id = kwargs.get("post_id")
                resp = requests.post(f"{self.BASE_URL}/posts/{post_id}/upvote", headers=self._get_headers())
                return resp.text

            elif command == "downvote_post":
                post_id = kwargs.get("post_id")
                resp = requests.post(f"{self.BASE_URL}/posts/{post_id}/downvote", headers=self._get_headers())
                return resp.text

            elif command == "upvote_comment":
                comment_id = kwargs.get("comment_id")
                resp = requests.post(f"{self.BASE_URL}/comments/{comment_id}/upvote", headers=self._get_headers())
                return resp.text

            elif command == "follow_agent":
                agent_name = kwargs.get("agent_name")
                resp = requests.post(f"{self.BASE_URL}/agents/{agent_name}/follow", headers=self._get_headers())
                return resp.text

            elif command == "unfollow_agent":
                agent_name = kwargs.get("agent_name")
                resp = requests.delete(f"{self.BASE_URL}/agents/{agent_name}/follow", headers=self._get_headers())
                return resp.text

            return f"Unknown command: {command}"
            
        except Exception as e:
            return f"Error executing Moltbook command '{command}': {str(e)}"
