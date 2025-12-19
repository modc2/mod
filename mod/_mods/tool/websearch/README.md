# WebSearch Tool - Universal Web Search & Content Extraction

## Overview
A powerful, language-agnostic web search tool with configurable depth, multi-engine support, and intelligent content extraction.

## Key Features
- **Multi-Engine Support**: DuckDuckGo, SearX, Brave, Google, Bing
- **Configurable Search Depth**: 1-3 levels (surface, follow links, deep crawl)
- **Parallel Processing**: Concurrent searches and content extraction
- **Smart Content Extraction**: Main content detection, metadata, structured data
- **Custom Filtering**: Content and link filters for precise results
- **Priority Domains**: Prioritize results from specific domains
- **Deduplication**: Automatic URL deduplication

## Quick Start

```python
import mod as c

# Initialize
search = c.WebSearch(search_depth=2, max_workers=10)

# Basic search
result = search.forward(
    query="Python web scraping",
    engine="duckduckgo",
    num_results=10
)

# Multi-engine deep search
result = search.forward(
    query="machine learning tutorials",
    engine="all",
    num_results=20,
    search_depth=3,
    extract_structured_data=True,
    priority_domains=["github.com", "arxiv.org"]
)
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | required | Search query string |
| `engine` | str | "all" | Search engine: duckduckgo, searx, brave, google, bing, all |
| `num_results` | int | 10 | Number of results per engine |
| `search_depth` | int | 1 | Depth level (1=surface, 2=follow links, 3=deep) |
| `extract_content` | bool | True | Extract full page content |
| `extract_structured_data` | bool | False | Extract code, tables, lists |
| `parallel_search` | bool | True | Search engines in parallel |
| `parallel_extraction` | bool | True | Extract content in parallel |
| `priority_domains` | list | None | Domains to prioritize |
| `content_filter` | callable | None | Custom content filter function |
| `link_filter` | callable | None | Custom link filter function |

## Advanced Usage

### Custom Filters
```python
# Content filter
def clean_content(text):
    return text.replace("\n\n", "\n").strip()[:5000]

# Link filter for deep search
def relevant_links(url):
    return "docs" in url or "tutorial" in url

result = search.forward(
    query="API documentation",
    search_depth=2,
    content_filter=clean_content,
    link_filter=relevant_links
)
```

### Structured Data Extraction
```python
result = search.forward(
    query="Python code examples",
    extract_structured_data=True
)

# Access structured data
for r in result["results"]:
    if "structured_data" in r:
        print(f"Code blocks: {len(r['structured_data']['code'])}")
        print(f"Tables: {len(r['structured_data']['tables'])}")
```

## Return Format

```python
{
    "success": True,
    "query": "search query",
    "engine": ["duckduckgo", "searx"],
    "results": [
        {
            "title": "Page Title",
            "url": "https://example.com",
            "snippet": "Preview text...",
            "content": "Full extracted content...",
            "source": "duckduckgo",
            "depth": 1,
            "is_priority": False,
            "metadata": {...},
            "structured_data": {...},
            "links": ["https://...", ...]
        }
    ],
    "context": "Aggregated context string",
    "count": 15,
    "depth": 2
}
```

## Performance Tips

1. **Use parallel processing** for faster results (enabled by default)
2. **Limit search depth** to 1-2 for speed, 3 for comprehensive research
3. **Set priority_domains** to focus on trusted sources
4. **Use content_filter** to reduce processing overhead
5. **Adjust max_workers** based on your system (default: 10)

## Examples

### Research Assistant
```python
result = search.forward(
    query="quantum computing breakthroughs 2024",
    engine="all",
    num_results=30,
    search_depth=2,
    priority_domains=["arxiv.org", "nature.com", "science.org"],
    extract_structured_data=True
)
```

### Code Discovery
```python
result = search.forward(
    query="FastAPI authentication example",
    priority_domains=["github.com", "stackoverflow.com"],
    extract_structured_data=True,
    link_filter=lambda url: "github.com" in url or "docs" in url
)
```

### News Aggregation
```python
result = search.forward(
    query="AI news today",
    engine="all",
    num_results=50,
    search_depth=1,
    parallel_search=True
)
```

## Testing

```python
search = c.WebSearch()
search.test()  # Runs comprehensive test suite
```

## Architecture

- **Multi-Engine Router**: Intelligently routes to different search engines
- **Parallel Executor**: ThreadPoolExecutor for concurrent operations
- **Content Extractor**: BeautifulSoup-based intelligent content detection
- **Deep Crawler**: Recursive link following with depth control
- **Context Builder**: Aggregates results into coherent context

## License
MIT License - Built with ❤️ for the free world