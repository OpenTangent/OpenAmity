import os
import requests
import logging
from typing import List, Dict, Any
from core.cerebrum import Tool
from config import paths
from core.cache_manager import CacheManager
try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

class WebSearchSkill(Tool):
    name = "WebSearch"
    description = "Allows you to search the web, read webpages, and download images for viewing. Use this to find up-to-date information or answer questions you don't know the answer to."
    commands = ["search", "view_page", "download_image"]

    def __init__(self):
        super().__init__()
        # Ensure the web cache directory exists
        self.cache_dir = os.path.join(paths.get_app_data_dir(), "web_cache")
        os.makedirs(self.cache_dir, exist_ok=True)

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "WebSearch_search",
                "description": "Searches the web using DuckDuckGo and returns a list of relevant results, including page titles, URLs, and a brief snippet of the content.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "The search query."
                        },
                        "max_results": {
                            "type": "INTEGER",
                            "description": "The maximum number of results to return (default is 5, max is 10)."
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "WebSearch_view_page",
                "description": "Fetches the content of a specific web page URL and converts it into clean, readable Markdown using the Jina Reader API. Use this to read the full content of an article or page after finding it via search.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "url": {
                            "type": "STRING",
                            "description": "The absolute URL of the web page to read."
                        }
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "WebSearch_download_image",
                "description": "Downloads an image from a given URL to a temporary cache so you can visually examine it.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "url": {
                            "type": "STRING",
                            "description": "The absolute URL of the image to download."
                        }
                    },
                    "required": ["url"]
                }
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> Any:
        # Before any command, do a quick cleanup of old cached files
        CacheManager.clear_expired(self.cache_dir, max_age_days=1.0)

        if command == "search":
            if DDGS is None:
                return "Error: The 'ddgs' package is not installed. Please run 'pip install ddgs'."
                
            query = kwargs.get('query') or (args[0] if args else None)
            max_results = kwargs.get('max_results', 5)
            
            if not query:
                return "Error: Missing search query."
                
            try:
                # Cap max_results to 10 to avoid too much context usage
                max_results = min(int(max_results), 10)
                
                with DDGS() as ddgs:
                    # Note: we need to coerce DDGS generator to list since we iterate
                    results = list(ddgs.text(query, max_results=max_results))
                    
                if not results:
                    return f"No results found for query: {query}"
                    
                output = f"Search Results for '{query}':\n\n"
                for i, r in enumerate(results, 1):
                    output += f"{i}. {r.get('title', 'No Title')}\n"
                    output += f"   URL: {r.get('href', 'No URL')}\n"
                    output += f"   Snippet: {r.get('body', 'No Snippet')}\n\n"
                return output
            except Exception as e:
                logging.error(f"WebSearch (search) error: {e}", exc_info=True)
                return f"An error occurred during search: {e}"

        elif command == "view_page":
            url = kwargs.get('url') or (args[0] if args else None)
            if not url:
                return "Error: Missing URL."
                
            try:
                headers = {'User-Agent': 'Open Amity Web Search Tool'}
                # Use Jina Reader API
                jina_url = f"https://r.jina.ai/{url}"
                response = requests.get(jina_url, headers=headers, timeout=15)
                response.raise_for_status()
                return response.text
            except Exception as e:
                logging.error(f"WebSearch (view_page) error: {e}", exc_info=True)
                return f"Failed to fetch page {url}: {e}"

        elif command == "download_image":
            url = kwargs.get('url') or (args[0] if args else None)
            if not url:
                return "Error: Missing image URL."
                
            try:
                headers = {'User-Agent': 'Open Amity Web Search Tool'}
                response = requests.get(url, headers=headers, stream=True, timeout=15)
                response.raise_for_status()
                
                # Determine a filename. Use a hash or the last part of the URL.
                import hashlib
                import time
                # Create a unique but readable filename
                hash_str = hashlib.md5(f"{url}{time.time()}".encode('utf-8')).hexdigest()[:8]
                filename = url.split('/')[-1]
                if not filename or '?' in filename:
                    filename = "image.jpg"
                else:
                    # Clean filename if it has query params
                    filename = filename.split('?')[0]
                
                safe_filename = f"{hash_str}_{filename}"
                filepath = os.path.join(self.cache_dir, safe_filename)
                
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                        
                return {
                    "result": f"Image downloaded successfully from {url}.",
                    "media": [filepath]
                }
            except Exception as e:
                logging.error(f"WebSearch (download_image) error: {e}", exc_info=True)
                return f"Failed to download image {url}: {e}"

        return f"Unknown command: {command}"
