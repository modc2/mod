import mod as c
import requests
from typing import List, Dict, Optional, Any
from bs4 import BeautifulSoup
import json
from urllib.parse import quote_plus, urlencode
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

print = c.print

class WebSearch:
    """Advanced multi-engine web search tool with ChatGPT-style content extraction and context building."""
    
    def __init__(self, 
                 default_engine: str = 'duckduckgo',
                 timeout: int = 10,
                 max_retries: int = 3):
        """
        Initialize the WebSearch tool with multiple search engine support.
        
        Args:
            default_engine: Default search engine ('duckduckgo', 'searx', 'brave', 'google', 'bing', 'all')
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
        """
        self.default_engine = default_engine
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        
        # Public SearX instances for fallback
        self.searx_instances = [
            'https://searx.be',
            'https://searx.work',
            'https://search.sapti.me',
            'https://searx.tiekoetter.com'
        ]

    def forward(self,
                query: str = "how do i write a web search tool?",
                engine: Optional[str] = None,
                num_results: int = 10,
                safe_search: bool = True,
                extract_content: bool = True,
                max_content_length: int = 3000,
                include_metadata: bool = True,
                parallel_extraction: bool = True,
                deduplicate: bool = True,
                verbose: bool = True) -> Dict[str, Any]:
        """
        Perform a web search and return results with extracted content (ChatGPT-style).
        
        Args:
            query: Search query string
            engine: Search engine to use ('duckduckgo', 'searx', 'brave', 'google', 'bing', 'all')
            num_results: Number of results to return
            safe_search: Enable safe search filtering
            extract_content: Whether to extract page content from results
            max_content_length: Maximum content length per page
            include_metadata: Include metadata like publish date, author
            parallel_extraction: Extract content in parallel for speed
            deduplicate: Remove duplicate URLs across engines
            verbose: Print detailed information
            
        Returns:
            Dictionary containing:
            - success: Whether the search was successful
            - query: The search query used
            - engine: The search engine(s) used
            - results: List of search results with title, url, snippet, content
            - context: Aggregated context from all results
            - error: Error message if failed
        """
        engine = engine or self.default_engine
        
        if verbose:
            print(f"🔍 Searching for '{query}' using {engine}...", color='cyan')
        
        try:
            all_results = []
            engines_used = []
            
            # Multi-engine search
            if engine == 'all':
                search_engines = ['duckduckgo', 'searx', 'brave', 'bing']
                for eng in search_engines:
                    try:
                        results = self._search_engine(eng, query, num_results, safe_search, verbose)
                        if results:
                            all_results.extend(results)
                            engines_used.append(eng)
                    except Exception as e:
                        if verbose:
                            print(f"  ⚠️  {eng} failed: {str(e)[:50]}", color='yellow')
            else:
                all_results = self._search_engine(engine, query, num_results, safe_search, verbose)
                engines_used = [engine]
            
            # Deduplicate by URL
            if deduplicate:
                seen_urls = set()
                unique_results = []
                for r in all_results:
                    if r['url'] not in seen_urls:
                        seen_urls.add(r['url'])
                        unique_results.append(r)
                all_results = unique_results[:num_results]
            
            if extract_content and all_results:
                if parallel_extraction:
                    all_results = self._extract_content_parallel(all_results, max_content_length, include_metadata, verbose)
                else:
                    all_results = self._extract_page_content(all_results, max_content_length, include_metadata, verbose)
            
            # Build aggregated context like ChatGPT
            context = self._build_context(all_results, query)
            
            if verbose:
                print(f"✅ Found {len(all_results)} results from {', '.join(engines_used)}", color='green')
            
            return {
                'success': True,
                'query': query,
                'engine': engines_used if len(engines_used) > 1 else engines_used[0] if engines_used else engine,
                'results': all_results,
                'context': context,
                'count': len(all_results)
            }
            
        except Exception as e:
            error_msg = str(e)
            if verbose:
                print(f"❌ Search failed: {error_msg}", color='red')
            
            return {
                'success': False,
                'error': error_msg,
                'query': query,
                'engine': engine,
                'results': [],
                'context': ''
            }

    def _search_engine(self, engine: str, query: str, num_results: int, safe_search: bool, verbose: bool = False) -> List[Dict[str, str]]:
        """Route to appropriate search engine."""
        if engine == 'duckduckgo':
            return self._search_duckduckgo(query, num_results, safe_search)
        elif engine == 'searx':
            return self._search_searx(query, num_results, safe_search)
        elif engine == 'brave':
            return self._search_brave(query, num_results, safe_search)
        elif engine == 'google':
            return self._search_google(query, num_results, safe_search)
        elif engine == 'bing':
            return self._search_bing(query, num_results, safe_search)
        else:
            raise ValueError(f"Unsupported search engine: {engine}")

    def _search_duckduckgo(self, query: str, num_results: int, safe_search: bool) -> List[Dict[str, str]]:
        """Search using DuckDuckGo HTML interface."""
        url = 'https://html.duckduckgo.com/html/'
        params = {'q': query, 'kl': 'us-en'}
        if safe_search:
            params['kp'] = '1'
        
        for attempt in range(self.max_retries):
            try:
                response = self.session.post(url, data=params, timeout=self.timeout)
                response.raise_for_status()
                break
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(1)
        
        soup = BeautifulSoup(response.text, 'html.parser')
        results = []
        
        for result in soup.find_all('div', class_='result')[:num_results]:
            title_elem = result.find('a', class_='result__a')
            snippet_elem = result.find('a', class_='result__snippet')
            
            if title_elem:
                results.append({
                    'title': title_elem.get_text(strip=True),
                    'url': title_elem.get('href', ''),
                    'snippet': snippet_elem.get_text(strip=True) if snippet_elem else '',
                    'source': 'duckduckgo'
                })
        
        return results

    def _search_searx(self, query: str, num_results: int, safe_search: bool) -> List[Dict[str, str]]:
        """Search using public SearX instances with fallback."""
        params = {
            'q': query,
            'format': 'json',
            'safesearch': '1' if safe_search else '0'
        }
        
        for instance in self.searx_instances:
            try:
                url = f"{instance}/search"
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                
                results = []
                for item in data.get('results', [])[:num_results]:
                    results.append({
                        'title': item.get('title', ''),
                        'url': item.get('url', ''),
                        'snippet': item.get('content', ''),
                        'source': 'searx'
                    })
                return results
            except:
                continue
        
        raise Exception("All SearX instances failed")

    def _search_brave(self, query: str, num_results: int, safe_search: bool) -> List[Dict[str, str]]:
        """Search using Brave Search (web scraping fallback)."""
        url = 'https://search.brave.com/search'
        params = {'q': query, 'source': 'web'}
        if safe_search:
            params['safesearch'] = 'strict'
        
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        results = []
        
        for result in soup.find_all('div', class_=re.compile(r'snippet'))[:num_results]:
            title_elem = result.find('a')
            snippet_elem = result.find('p', class_=re.compile(r'snippet-description'))
            
            if title_elem and title_elem.get('href'):
                results.append({
                    'title': title_elem.get_text(strip=True),
                    'url': title_elem.get('href', ''),
                    'snippet': snippet_elem.get_text(strip=True) if snippet_elem else '',
                    'source': 'brave'
                })
        
        return results

    def _search_google(self, query: str, num_results: int, safe_search: bool) -> List[Dict[str, str]]:
        """Search using Google (web scraping)."""
        url = 'https://www.google.com/search'
        params = {'q': query, 'num': num_results}
        if safe_search:
            params['safe'] = 'active'
        
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        results = []
        
        for g in soup.find_all('div', class_='g')[:num_results]:
            anchor = g.find('a')
            title = g.find('h3')
            snippet = g.find('div', class_=re.compile(r'VwiC3b'))
            
            if anchor and title:
                href = anchor.get('href', '')
                if href.startswith('/url?q='):
                    href = href.split('/url?q=')[1].split('&')[0]
                
                results.append({
                    'title': title.get_text(strip=True),
                    'url': href,
                    'snippet': snippet.get_text(strip=True) if snippet else '',
                    'source': 'google'
                })
        
        return results

    def _search_bing(self, query: str, num_results: int, safe_search: bool) -> List[Dict[str, str]]:
        """Search using Bing."""
        url = 'https://www.bing.com/search'
        params = {'q': query, 'count': num_results}
        if safe_search:
            params['safesearch'] = 'strict'
        
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        results = []
        
        for result in soup.find_all('li', class_='b_algo')[:num_results]:
            title_elem = result.find('h2')
            link_elem = title_elem.find('a') if title_elem else None
            snippet_elem = result.find('p') or result.find('div', class_='b_caption')
            
            if link_elem:
                results.append({
                    'title': link_elem.get_text(strip=True),
                    'url': link_elem.get('href', ''),
                    'snippet': snippet_elem.get_text(strip=True) if snippet_elem else '',
                    'source': 'bing'
                })
        
        return results

    def _extract_content_parallel(self, results: List[Dict[str, str]], max_length: int, include_metadata: bool, verbose: bool = False) -> List[Dict[str, str]]:
        """Extract content from multiple URLs in parallel."""
        def extract_single(result):
            return self._extract_single_page(result, max_length, include_metadata)
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(extract_single, r): i for i, r in enumerate(results)}
            
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                    if verbose:
                        print(f"  📄 Extracted [{idx+1}/{len(results)}]: {results[idx]['url'][:60]}...", color='yellow')
                except Exception as e:
                    if verbose:
                        print(f"  ⚠️  Failed [{idx+1}/{len(results)}]: {str(e)[:50]}", color='yellow')
        
        return results

    def _extract_page_content(self, results: List[Dict[str, str]], max_length: int, include_metadata: bool, verbose: bool = False) -> List[Dict[str, str]]:
        """Extract content from result URLs sequentially."""
        for idx, result in enumerate(results):
            if verbose:
                print(f"  📄 Extracting content from [{idx+1}/{len(results)}]: {result['url'][:60]}...", color='yellow')
            
            results[idx] = self._extract_single_page(result, max_length, include_metadata)
        
        return results

    def _extract_single_page(self, result: Dict[str, str], max_length: int, include_metadata: bool) -> Dict[str, str]:
        """Extract content from a single page."""
        try:
            response = self.session.get(result['url'], timeout=self.timeout, allow_redirects=True)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract metadata
            if include_metadata:
                result['metadata'] = self._extract_metadata(soup)
            
            # Remove unwanted elements
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
                tag.decompose()
            
            # Try to find main content
            main_content = (
                soup.find('main') or 
                soup.find('article') or 
                soup.find('div', class_=re.compile(r'content|article|post|main', re.I)) or
                soup.find('div', id=re.compile(r'content|article|post|main', re.I))
            )
            
            if main_content:
                text = main_content.get_text(separator='\n', strip=True)
            else:
                text = soup.get_text(separator='\n', strip=True)
            
            # Clean up text
            lines = [line.strip() for line in text.splitlines() if line.strip() and len(line.strip()) > 3]
            text = '\n'.join(lines)
            
            # Limit length
            result['content'] = text[:max_length]
            result['content_length'] = len(text)
            result['extracted'] = True
            
        except Exception as e:
            result['content'] = result.get('snippet', '')
            result['extracted'] = False
            result['error'] = str(e)
        
        return result

    def _extract_metadata(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract metadata from page."""
        metadata = {}
        
        # Open Graph tags
        og_title = soup.find('meta', property='og:title')
        if og_title:
            metadata['og_title'] = og_title.get('content', '')
        
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            metadata['og_description'] = og_desc.get('content', '')
        
        og_image = soup.find('meta', property='og:image')
        if og_image:
            metadata['og_image'] = og_image.get('content', '')
        
        # Published date
        date_meta = (
            soup.find('meta', property='article:published_time') or 
            soup.find('meta', attrs={'name': 'date'}) or
            soup.find('meta', attrs={'name': 'publish_date'})
        )
        if date_meta:
            metadata['published_date'] = date_meta.get('content', '')
        
        # Author
        author_meta = (
            soup.find('meta', attrs={'name': 'author'}) or 
            soup.find('meta', property='article:author')
        )
        if author_meta:
            metadata['author'] = author_meta.get('content', '')
        
        # Keywords
        keywords_meta = soup.find('meta', attrs={'name': 'keywords'})
        if keywords_meta:
            metadata['keywords'] = keywords_meta.get('content', '')
        
        return metadata

    def _build_context(self, results: List[Dict[str, str]], query: str) -> str:
        """Build aggregated context from all results (ChatGPT-style)."""
        context_parts = [f"Search results for: {query}\n"]
        
        for idx, result in enumerate(results, 1):
            context_parts.append(f"\n[{idx}] {result['title']}")
            context_parts.append(f"URL: {result['url']}")
            context_parts.append(f"Source: {result.get('source', 'unknown')}")
            
            if result.get('content'):
                # Take first 500 chars of content
                content_preview = result['content'][:500].strip()
                context_parts.append(f"Content: {content_preview}...")
            elif result.get('snippet'):
                context_parts.append(f"Snippet: {result['snippet']}")
            
            if result.get('metadata'):
                meta = result['metadata']
                if meta.get('published_date'):
                    context_parts.append(f"Published: {meta['published_date']}")
                if meta.get('author'):
                    context_parts.append(f"Author: {meta['author']}")
        
        return '\n'.join(context_parts)

    def test(self):
        """Test the WebSearch tool with multiple engines."""
        query = "Python programming best practices"
        
        # Test single engine
        result = self.forward(query, engine='duckduckgo', num_results=3, extract_content=True, verbose=True)
        assert result['success'], "Search should succeed"
        assert len(result['results']) > 0, "Should return results"
        
        # Test multi-engine
        result_multi = self.forward(query, engine='all', num_results=5, extract_content=True, verbose=True)
        assert result_multi['success'], "Multi-engine search should succeed"
        
        print(f"\n✅ All tests passed!", color='green')
        print(f"\n📝 Context preview:\n{result['context'][:500]}...", color='cyan')
        return result
