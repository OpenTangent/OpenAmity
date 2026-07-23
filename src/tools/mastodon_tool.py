import os
import json
from typing import List, Dict, Any
from core.cerebrum import Tool
from mastodon import Mastodon
from core.settings_manager import SettingsManager

class MastodonTool(Tool):
    name = "Mastodon"
    description = "Interact with the Mastodon social media network (post statuses, read timelines, get notifications, reply)."
    commands = [
        "post_status",
        "get_notifications",
        "get_status_context",
        "reply_to_status",
        "search",
        "favorite_status",
        "boost_status",
        "get_timeline"
    ]

    def __init__(self):
        super().__init__()
        # Initialize mastodon client
        client_key = os.getenv("MASTODON_CLIENT_KEY")
        client_secret = os.getenv("MASTODON_CLIENT_SECRET")
        access_token = os.getenv("MASTODON_ACCESS_TOKEN")
        api_base_url = os.getenv("MASTODON_API_BASE_URL", "https://mastodon.social")
        
        if access_token and client_key and client_secret:
            self.client = Mastodon(
                client_id=client_key,
                client_secret=client_secret,
                access_token=access_token,
                api_base_url=api_base_url
            )
        elif access_token:
            self.client = Mastodon(
                access_token=access_token,
                api_base_url=api_base_url
            )
        else:
            self.client = None

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Mastodon_post_status",
                "description": "Post a new status update to Mastodon.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {"type": "STRING", "description": "The text content of the post."}
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "Mastodon_get_notifications",
                "description": "Get recent notifications, including mentions and replies.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "limit": {"type": "INTEGER", "description": "Number of notifications to return. Default 10."}
                    }
                }
            },
            {
                "name": "Mastodon_get_status_context",
                "description": "Get the context of a status, i.e., its ancestors (previous posts in thread) and descendants (replies).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "status_id": {"type": "STRING", "description": "The ID of the status."}
                    },
                    "required": ["status_id"]
                }
            },
            {
                "name": "Mastodon_reply_to_status",
                "description": "Reply to a specific status.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "status_id": {"type": "STRING", "description": "The ID of the status to reply to."},
                        "text": {"type": "STRING", "description": "The text content of the reply."}
                    },
                    "required": ["status_id", "text"]
                }
            },
            {
                "name": "Mastodon_search",
                "description": "Search for statuses, accounts, or hashtags on Mastodon.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {"type": "STRING", "description": "The search query."},
                        "result_type": {"type": "STRING", "description": "Type of result to return: 'accounts', 'statuses', or 'hashtags'. Default is 'statuses'."}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "Mastodon_favorite_status",
                "description": "Favorite (like) a status on Mastodon.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "status_id": {"type": "STRING", "description": "The ID of the status to favorite."}
                    },
                    "required": ["status_id"]
                }
            },
            {
                "name": "Mastodon_boost_status",
                "description": "Boost (reblog) a status on Mastodon.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "status_id": {"type": "STRING", "description": "The ID of the status to boost."}
                    },
                    "required": ["status_id"]
                }
            },
            {
                "name": "Mastodon_get_timeline",
                "description": "Get a timeline of statuses. Timeline types: 'home', 'public', 'local'.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "timeline_type": {"type": "STRING", "description": "Type of timeline ('home', 'public', 'local'). Default is 'home'."},
                        "limit": {"type": "INTEGER", "description": "Number of statuses to return. Default 10."}
                    }
                }
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> str:
        if not self.client:
            return "Error: Mastodon credentials not found in environment."
            
        try:
            is_low_token = SettingsManager().get("core.low-token-mode", False)
            
            if command == "post_status":
                text = kwargs.get("text")
                if not text:
                    return "Error: 'text' parameter is required."
                result = self.client.status_post(status=text)
                return f"Successfully posted status. ID: {result.get('id')}, URL: {result.get('url')}"
                
            elif command == "get_notifications":
                limit = kwargs.get("limit", 10)
                if is_low_token:
                    limit = min(int(limit), 5)
                
                # Retrieve the last read marker
                try:
                    markers = self.client.markers_get(["notifications"])
                    last_read_id = markers.get("notifications", {}).get("last_read_id")
                except Exception:
                    last_read_id = None
                
                # Fetch notifications since the last read id
                notifications = self.client.notifications(limit=limit, since_id=last_read_id)
                
                # Update the marker if there are new notifications
                if notifications:
                    max_id = max(n.get("id") for n in notifications)
                    try:
                        self.client.markers_set("notifications", max_id)
                    except Exception:
                        pass
                
                res = []
                for n in notifications:
                    item = {
                        "id": n.get("id"),
                        "type": n.get("type"),
                        "created_at": str(n.get("created_at")),
                        "account": n.get("account", {}).get("acct")
                    }
                    if "status" in n and n["status"]:
                        item["status_id"] = n["status"].get("id")
                        item["content"] = n["status"].get("content")
                    res.append(item)
                return json.dumps(res, indent=2)
                
            elif command == "get_status_context":
                status_id = kwargs.get("status_id")
                if not status_id:
                    return "Error: 'status_id' parameter is required."
                context = self.client.status_context(status_id)
                
                def format_status(s):
                    return {
                        "id": s.get("id"),
                        "account": s.get("account", {}).get("acct"),
                        "content": s.get("content"),
                        "created_at": str(s.get("created_at"))
                    }
                
                res = {
                    "ancestors": [format_status(s) for s in context.get("ancestors", [])],
                    "descendants": [format_status(s) for s in context.get("descendants", [])]
                }
                return json.dumps(res, indent=2)
                
            elif command == "reply_to_status":
                status_id = kwargs.get("status_id")
                text = kwargs.get("text")
                if not status_id or not text:
                    return "Error: 'status_id' and 'text' parameters are required."
                result = self.client.status_post(status=text, in_reply_to_id=status_id)
                return f"Successfully replied. ID: {result.get('id')}, URL: {result.get('url')}"
                
            elif command == "search":
                query = kwargs.get("query")
                result_type = kwargs.get("result_type", "statuses")
                if not query:
                    return "Error: 'query' parameter is required."
                results = self.client.search(q=query)
                
                items = results.get(result_type, [])
                res = []
                max_items = 5 if is_low_token else 10
                for item in items[:max_items]:
                    if result_type == "statuses":
                        res.append({
                            "id": item.get("id"),
                            "account": item.get("account", {}).get("acct"),
                            "content": item.get("content")
                        })
                    elif result_type == "accounts":
                        res.append({
                            "id": item.get("id"),
                            "acct": item.get("acct"),
                            "display_name": item.get("display_name"),
                            "note": item.get("note")
                        })
                    elif result_type == "hashtags":
                        res.append(item.get("name"))
                return json.dumps(res, indent=2)
                
            elif command == "favorite_status":
                status_id = kwargs.get("status_id")
                if not status_id:
                    return "Error: 'status_id' parameter is required."
                self.client.status_favorite(status_id)
                return f"Successfully favorited status {status_id}."
                
            elif command == "boost_status":
                status_id = kwargs.get("status_id")
                if not status_id:
                    return "Error: 'status_id' parameter is required."
                self.client.status_reblog(status_id)
                return f"Successfully boosted status {status_id}."
                
            elif command == "get_timeline":
                timeline_type = kwargs.get("timeline_type", "home")
                limit = kwargs.get("limit", 10)
                if is_low_token:
                    limit = min(int(limit), 5)
                if timeline_type == "home":
                    statuses = self.client.timeline_home(limit=limit)
                elif timeline_type == "local":
                    statuses = self.client.timeline_local(limit=limit)
                elif timeline_type == "public":
                    statuses = self.client.timeline_public(limit=limit)
                else:
                    return "Error: Invalid timeline_type. Choose from 'home', 'local', 'public'."
                
                res = []
                for s in statuses:
                    res.append({
                        "id": s.get("id"),
                        "account": s.get("account", {}).get("acct"),
                        "content": s.get("content"),
                        "created_at": str(s.get("created_at"))
                    })
                return json.dumps(res, indent=2)
                
            return f"Unknown command: {command}"
            
        except Exception as e:
            return f"Error executing Mastodon command '{command}': {str(e)}"
