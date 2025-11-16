import requests
from typing import List, Dict, Any

class BaseMod:
    description = """
    GitHub Repository Search - Find the coolest repos on GitHub
    Searches GitHub for trending and popular repositories based on stars, forks, and activity
    """
    
    def __init__(self):
        self.base_url = "https://api.github.com/search/repositories"
        self.headers = {"Accept": "application/vnd.github.v3+json"}
    
    def search_coolest_repos(self, query: str = "stars:>10000", sort: str = "stars", order: str = "desc", per_page: int = 10) -> List[Dict[str, Any]]:
        """
        Search GitHub for the coolest repositories
        
        Args:
            query: Search query (default: repos with >10k stars)
            sort: Sort by 'stars', 'forks', or 'updated'
            order: 'desc' or 'asc'
            per_page: Number of results to return (max 100)
        
        Returns:
            List of repository dictionaries with key info
        """
        params = {
            "q": query,
            "sort": sort,
            "order": order,
            "per_page": per_page
        }
        
        try:
            response = requests.get(self.base_url, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            repos = []
            for item in data.get("items", []):
                repos.append({
                    "name": item["name"],
                    "full_name": item["full_name"],
                    "description": item["description"],
                    "stars": item["stargazers_count"],
                    "forks": item["forks_count"],
                    "language": item["language"],
                    "url": item["html_url"],
                    "topics": item.get("topics", []),
                    "updated_at": item["updated_at"]
                })
            
            return repos
        
        except requests.exceptions.RequestException as e:
            print(f"Error searching GitHub: {e}")
            return []
    
    def search_trending(self, language: str = "", days: int = 7) -> List[Dict[str, Any]]:
        """
        Search for trending repositories in the last N days
        
        Args:
            language: Filter by programming language
            days: Number of days to look back
        
        Returns:
            List of trending repositories
        """
        from datetime import datetime, timedelta
        date_threshold = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        query = f"created:>{date_threshold}"
        if language:
            query += f" language:{language}"
        
        return self.search_coolest_repos(query=query, sort="stars", per_page=10)
    
    def forward(self, query: str = "", language: str = "", min_stars: int = 1000) -> Dict[str, Any]:
        """
        Main entry point - search for cool repos
        
        Args:
            query: Custom search query
            language: Filter by language
            min_stars: Minimum stars threshold
        
        Returns:
            Dictionary with search results
        """
        if not query:
            query = f"stars:>{min_stars}"
            if language:
                query += f" language:{language}"
        
        repos = self.search_coolest_repos(query=query)
        
        return {
            "success": True,
            "count": len(repos),
            "repositories": repos,
            "message": f"Found {len(repos)} cool repositories!"
        }
