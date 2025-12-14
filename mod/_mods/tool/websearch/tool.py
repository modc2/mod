import mod as c
import requests
from typing import List, Dict, Optional, Any, Callable
from bs4 import BeautifulSoup
import json
from urllib.parse import quote_plus, urlencode
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

print = c.print

class WebSearch:
    """Universal web search and content extraction tool with configurable depth and language-agnostic design."""
    
    def __init__(self, 
                 default_engine: str = 'all',
                 timeout: int = 10,
                 max_retries: int = 3,
                 max_workers: int = 10,
                 search_depth: int = 1):
        """
        Initialize the WebSearch tool with configurable search depth.
        
        Args:
            default_engine: Default search engine ('duckduckgo', 'searx', 'brave', 'google', 'bing', 'all')
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            max_workers: Maximum number of parallel threads
            search_depth: Depth of search (1=surface, 2=follow links, 3=deep crawl)
        """
        self.default_engine = default_engine
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_workers = max_workers
        self.search_depth = search_depth
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
        
        self.searx_instances = [
            'https://searx.be',
            'https://searx.work',
            'https://search.sapti.me',
            'https://searx.tiekoetter.com'
        ]
        
        self.priority_domains = []
        self.visited_urls = set()

    def forward(self,
                query: str = "universal search query",
                engine: Optional[str] = None,
                num_results: int = 10,
                safe_search: bool = True,
                extract_content: bool = True,
                max_content_length: int = 5000,
                include_metadata: bool = True,
                parallel_extraction: bool = True,
                parallel_search: bool = True,
                deduplicate: bool = True,
                search_depth: Optional[int] = None,
                content_filter: Optional[Callable] = None,
                link_filter: Optional[Callable] = None,
                priority_domains: Optional[List[str]] = None,
                extract_structured_data: bool = False,
                verbose: bool = True) -> Dict[str, Any]:
        """
        Universal search with configurable depth and filtering.
        
        Args:
            query: Search query string
            engine: Search engine to use (None uses default)
            num_results: Number of results per engine
            safe_search: Enable safe search filtering
            extract_content: Extract page content from results
            max_content_length: Maximum content length per page
            include_metadata: Include metadata extraction
            parallel_extraction: Extract content in parallel
            parallel_search: Search multiple engines in parallel
            deduplicate: Remove duplicate URLs
            search_depth: Override default search depth (1-3)
            content_filter: Custom function to filter/transform content
            link_filter: Custom function to filter links for deeper crawling
            priority_domains: List of domains to prioritize
            extract_structured_data: Extract structured data (code, tables, lists)
            verbose: Print detailed information
            
        Returns:
            Dictionary containing search results, context, and metadata
        """
        engine = engine or self.default_engine
        depth = search_depth or self.search_depth
        self.priority_domains = priority_domains or []
        self.visited_urls.clear()
        
        if verbose:
            print(f"🔍 Searching '{query}' | Engine: {engine} | Depth: {depth}", color='cyan')
        
        try:
            all_results = []
            engines_used = []
            
            # Level 1: Initial search
            if engine == 'all':
                search_engines = ['duckduckgo', 'searx', 'brave', 'bing']
                if parallel_search:
                    all_results, engines_used = self._parallel_multi_engine_search(
                        search_engines, query, num_results, safe_search, verbose
                    )
                else:
                    for eng in search_engines:
                        try:
                            results = self._search_engine(eng, query, num_results, safe_search)
                            if results:
                                all_results.extend(results)
                                engines_used.append(eng)
                        except Exception as e:
                            if verbose:
                                print(f"  ⚠️  {eng} failed: {str(e)[:50]}", color='yellow')
            else:
                all_results = self._search_engine(engine, query, num_results, safe_search)
                engines_used = [engine]
            
            # Prioritize domains if specified
            if self.priority_domains:
                all_results = self._prioritize_results(all_results, self.priority_domains)
            
            # Deduplicate
            if deduplicate:
                all_results = self._deduplicate_results(all_results)[:num_results]
            
            # Level 1: Extract content
            if extract_content and all_results:
                if parallel_extraction:
                    all_results = self._extract_content_parallel(
                        all_results, max_content_length, include_metadata, 
                        extract_structured_data, content_filter, verbose
                    )
                else:
                    all_results = self._extract_page_content(
                        all_results, max_content_length, include_metadata, 
                        extract_structured_data, content_filter, verbose
                    )
            
            # Level 2+: Deep search (follow links)
            if depth > 1 and all_results:
                all_results = self._deep_search(
                    all_results, depth, link_filter, content_filter, 
                    max_content_length, include_metadata, extract_structured_data,
                    parallel_extraction, verbose
                )
            
            # Build context
            context = self._build_context(all_results, query, extract_structured_data)
            
            if verbose:
                print(f"✅ Found {len(all_results)} results from {', '.join(engines_used)}", color='green')
            
            return {
                'success': True,
                'query': query,
                'engine': engines_used if len(engines_used) > 1 else engines_used[0] if engines_used else engine,
                'results': all_results,
                'context': context,
                'count': len(all_results),
                'depth': depth
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
                'context': '',
                'depth': depth
            }

    def _deep_search(self, results: List[Dict], depth: int, link_filter: Optional[Callable],
                     content_filter: Optional[Callable], max_content_length: int,
                     include_metadata: bool, extract_structured: bool,
                     parallel: bool, verbose: bool) -> List[Dict]:
        """Recursively follow links to specified depth."""
        current_level = results
        all_results = list(results)
        
        for level in range(2, depth + 1):
            if verbose:
                print(f"  🔎 Level {level} search...", color='cyan')
            
            next_level_urls = []
            for result in current_level:
                if result.get('links'):
                    for link in result['links'][:5]:  # Limit links per page
                        if link not in self.visited_urls:
                            if link_filter is None or link_filter(link):
                                next_level_urls.append({'url': link, 'title': '', 'snippet': '', 'depth': level})
                                self.visited_urls.add(link)
            
            if not next_level_urls:
                break
            
            # Extract content from next level
            if parallel:
                next_level_results = self._extract_content_parallel(
                    next_level_urls, max_content_length, include_metadata,
                    extract_structured, content_filter, verbose
                )
            else:
                next_level_results = self._extract_page_content(
                    next_level_urls, max_content_length, include_metadata,
                    extract_structured, content_filter, verbose
                )
            
            all_results.extend(next_level_results)
            current_level = next_level_results
        
        return all_results

    def _parallel_multi_engine_search(self, engines: List[str], query: str, 
                                     num_results: int, safe_search: bool, 
                                     verbose: bool = False) -> tuple:
        """Execute searches across multiple engines in parallel."""
        all_results = []
        engines_used = []
        
        def search_single_engine(engine_name):
            try:
                results = self._search_engine(engine_name, query, num_results, safe_search)
                return (engine_name, results, None)
            except Exception as e:
                return (engine_name, [], str(e))
        
        with ThreadPoolExecutor(max_workers=min(len(engines), self.max_workers)) as executor:
            futures = {executor.submit(search_single_engine, eng): eng for eng in engines}
            
            for future in as_completed(futures):
                engine_name, results, error = future.result()
                if results:
                    all_results.extend(results)
                    engines_used.append(engine_name)
                    if verbose:
                        print(f"  ✓ {engine_name}: {len(results)} results", color='green')
                elif error and verbose:
                    print(f"  ⚠️  {engine_name} failed: {error[:50]}", color='yellow')
        
        return all_results, engines_used

    def _prioritize_results(self, results: List[Dict], priority_domains: List[str]) -> List[Dict]:
        """Prioritize results from specified domains."""
        priority_results = []
        other_results = []
        
        for result in results:
            url = result.get('url', '').lower()
            if any(domain in url for domain in priority_domains):
                result['is_priority'] = True
                priority_results.append(result)
            else:
                result['is_priority'] = False
                other_results.append(result)
        
        return priority_results + other_results

    def _deduplicate_results(self, results: List[Dict]) -> List[Dict]:
        """Remove duplicate URLs."""
        seen_urls = set()
        unique_results = []
        for r in results:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                unique_results.append(r)
        return unique_results

    def _search_engine(self, engine: str, query: str, num_results: int, safe_search: bool) -> List[Dict[str, str]]:
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
        """Search using DuckDuckGo."""
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
        """Search using SearX."""
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
        """Search using Brave."""
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
        """Search using Google."""
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

    def _extract_content_parallel(self, results: List[Dict], max_length: int, 
                                  include_metadata: bool, extract_structured: bool,
                                  content_filter: Optional[Callable], verbose: bool = False) -> List[Dict]:
        """Extract content from multiple URLs in parallel."""
        def extract_single(result):
            return self._extract_single_page(result, max_length, include_metadata, 
                                            extract_structured, content_filter)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(extract_single, r): i for i, r in enumerate(results)}
            
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                    if verbose:
                        print(f"  📄 [{idx+1}/{len(results)}]: {results[idx]['url'][:60]}...", color='yellow')
                except Exception as e:
                    if verbose:
                        print(f"  ⚠️  [{idx+1}/{len(results)}]: {str(e)[:50]}", color='yellow')
        
        return results

    def _extract_page_content(self, results: List[Dict], max_length: int, 
                             include_metadata: bool, extract_structured: bool,
                             content_filter: Optional[Callable], verbose: bool = False) -> List[Dict]:
        """Extract content sequentially."""
        for idx, result in enumerate(results):
            if verbose:
                print(f"  📄 [{idx+1}/{len(results)}]: {result['url'][:60]}...", color='yellow')
            results[idx] = self._extract_single_page(result, max_length, include_metadata,
                                                     extract_structured, content_filter)
        return results

    def _extract_single_page(self, result: Dict, max_length: int, 
                            include_metadata: bool, extract_structured: bool,
                            content_filter: Optional[Callable]) -> Dict:
        """Extract content from a single page."""
        try:
            response = self.session.get(result['url'], timeout=self.timeout, allow_redirects=True)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            if include_metadata:
                result['metadata'] = self._extract_metadata(soup)
            
            if extract_structured:
                result['structured_data'] = self._extract_structured_data(soup)
            
            # Extract links for deep search
            result['links'] = [a.get('href') for a in soup.find_all('a', href=True)][:20]
            result['links'] = [link for link in result['links'] if link.startswith('http')]
            
            # Remove unwanted elements
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
                tag.decompose()
            
            # Find main content
            main_content = (
                soup.find('main') or 
                soup.find('article') or 
                soup.find('div', class_=re.compile(r'content|article|post|main', re.I)) or
                soup.find('div', id=re.compile(r'content|article|post|main', re.I))
            )
            
            text = main_content.get_text(separator='\n', strip=True) if main_content else soup.get_text(separator='\n', strip=True)
            
            # Clean text
            lines = [line.strip() for line in text.splitlines() if line.strip() and len(line.strip()) > 3]
            text = '\n'.join(lines)
            
            # Apply custom filter
            if content_filter:
                text = content_filter(text)
            
            result['content'] = text[:max_length]
            result['content_length'] = len(text)
            result['extracted'] = True
            
        except Exception as e:
            result['content'] = result.get('snippet', '')
            result['extracted'] = False
            result['error'] = str(e)
            if extract_structured:
                result['structured_data'] = {}
        
        return result

    def _extract_structured_data(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract structured data (code, tables, lists, etc.)."""
        structured = {}
        
        # Code blocks
        code_blocks = soup.find_all(['pre', 'code'])
        structured['code'] = []
        for idx, block in enumerate(code_blocks):
            code_text = block.get_text(strip=True)
            if len(code_text) > 20:
                language = 'unknown'
                class_attr = block.get('class', [])
                if class_attr:
                    for cls in class_attr:
                        if 'language-' in str(cls):
                            language = str(cls).replace('language-', '')
                structured['code'].append({'text': code_text[:2000], 'language': language, 'index': idx})
        
        # Tables
        tables = soup.find_all('table')
        structured['tables'] = []
        for idx, table in enumerate(tables[:5]):
            rows = []
            for tr in table.find_all('tr')[:10]:
                cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                if cells:
                    rows.append(cells)
            if rows:
                structured['tables'].append({'index': idx, 'rows': rows})
        
        # Lists
        lists = soup.find_all(['ul', 'ol'])
        structured['lists'] = []
        for idx, lst in enumerate(lists[:10]):
            items = [li.get_text(strip=True) for li in lst.find_all('li', recursive=False)[:20]]
            if items:
                structured['lists'].append({'index': idx, 'type': lst.name, 'items': items})
        
        return structured

    def _extract_metadata(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract metadata from page."""
        metadata = {}
        
        og_title = soup.find('meta', property='og:title')
        if og_title:
            metadata['og_title'] = og_title.get('content', '')
        
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            metadata['og_description'] = og_desc.get('content', '')
        
        og_image = soup.find('meta', property='og:image')
        if og_image:
            metadata['og_image'] = og_image.get('content', '')
        
        date_meta = (
            soup.find('meta', property='article:published_time') or 
            soup.find('meta', attrs={'name': 'date'}) or
            soup.find('meta', attrs={'name': 'publish_date'})
        )
        if date_meta:
            metadata['published_date'] = date_meta.get('content', '')
        
        author_meta = (
            soup.find('meta', attrs={'name': 'author'}) or 
            soup.find('meta', property='article:author')
        )
        if author_meta:
            metadata['author'] = author_meta.get('content', '')
        
        keywords_meta = soup.find('meta', attrs={'name': 'keywords'})
        if keywords_meta:
            metadata['keywords'] = keywords_meta.get('content', '')
        
        return metadata

    def _build_context(self, results: List[Dict], query: str, include_structured: bool = False) -> str:
        """Build aggregated context from results."""
        context_parts = [f"Search results for: {query}\n"]
        
        for idx, result in enumerate(results, 1):
            context_parts.append(f"\n[{idx}] {result['title']}")
            context_parts.append(f"URL: {result['url']}")
            context_parts.append(f"Source: {result.get('source', 'unknown')}")
            
            if result.get('depth'):
                context_parts.append(f"Depth: Level {result['depth']}")
            
            if result.get('is_priority'):
                context_parts.append("⭐ Priority Domain")
            
            if result.get('content'):
                content_preview = result['content'][:500].strip()
                context_parts.append(f"Content: {content_preview}...")
            elif result.get('snippet'):
                context_parts.append(f"Snippet: {result['snippet']}")
            
            if include_structured and result.get('structured_data'):
                sd = result['structured_data']
                if sd.get('code'):
                    context_parts.append(f"💻 Code Blocks: {len(sd['code'])}")
                if sd.get('tables'):
                    context_parts.append(f"📊 Tables: {len(sd['tables'])}")
                if sd.get('lists'):
                    context_parts.append(f"📝 Lists: {len(sd['lists'])}")
            
            if result.get('metadata'):
                meta = result['metadata']
                if meta.get('published_date'):
                    context_parts.append(f"Published: {meta['published_date']}")
                if meta.get('author'):
                    context_parts.append(f"Author: {meta['author']}")
        
        return '\n'.join(context_parts)

    def test(self):
        """Test the WebSearch tool."""
        # Test 1: Basic search
        result1 = self.forward(
            "Python tutorial", 
            engine='duckduckgo', 
            num_results=3,
            search_depth=1,
            verbose=True
        )
        assert result1['success'], "Basic search should succeed"
        print(f"\n✅ Test 1 passed: Basic search", color='green')
        
        # Test 2: Multi-engine with depth
        result2 = self.forward(
            "web scraping",
            engine='all',
            num_results=5,
            search_depth=2,
            extract_structured_data=True,
            parallel_search=True,
            verbose=True
        )
        assert result2['success'], "Multi-engine search should succeed"
        print(f"\n✅ Test 2 passed: Multi-engine with depth", color='green')
        
        # Test 3: Custom filters
        def my_filter(content):
            return content[:1000]
        
        result3 = self.forward(
            "data science",
            num_results=3,
            content_filter=my_filter,
            priority_domains=['github.com', 'stackoverflow.com'],
            verbose=True
        )
        assert result3['success'], "Custom filter search should succeed"
        print(f"\n✅ Test 3 passed: Custom filters", color='green')
        
        print(f"\n🎉 All tests passed!", color='green')
        return result2
