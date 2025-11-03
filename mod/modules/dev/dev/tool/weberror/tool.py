from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class ErrorInfo:
    """Data class to store parsed error information"""
    error_type: str
    message: str
    stack_trace: List[str]
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    column_number: Optional[int] = None
    additional_info: Optional[Dict] = None


class BrowserErrorScraper:
    """
    A comprehensive class for scraping browser errors using Selenium and BeautifulSoup.
    Can extract error information from development tools, console logs, and error overlays.
    """
    
    def __init__(self, headless: bool = False, timeout: int = 30):
        """
        Initialize the scraper with browser options.
        
        Args:
            headless: Run browser in headless mode
            timeout: Default timeout for waiting for elements
        """
        self.timeout = timeout
        self.driver = self._setup_driver(headless)
        self.errors: List[ErrorInfo] = []
        
    def _setup_driver(self, headless: bool) -> webdriver.Chrome:
        """Setup Chrome driver with appropriate options"""
        options = webdriver.ChromeOptions()
        
        if headless:
            options.add_argument('--headless')
        
        # Enable logging
        options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
        
        # Additional options for better error capture
        options.add_argument('--enable-logging')
        options.add_argument('--v=1')
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        driver = webdriver.Chrome(options=options)
        driver.maximize_window()
        
        return driver
    
    def navigate(self, url: str) -> None:
        """Navigate to a URL and wait for page load"""
        print(f"Navigating to: {url}")
        self.driver.get(url)
        time.sleep(2)  # Allow time for errors to appear
        
    def extract_console_errors(self) -> List[ErrorInfo]:
        """Extract errors from browser console logs"""
        errors = []
        
        try:
            logs = self.driver.get_log('browser')
            
            for entry in logs:
                if entry['level'] in ['SEVERE', 'ERROR', 'WARNING']:
                    error = ErrorInfo(
                        error_type=f"Console {entry.get('level', 'ERROR')}",
                        message=entry.get('message', ''),
                        stack_trace=[entry.get('source', '')],
                        additional_info={
                            'level': entry.get('level'),
                            'timestamp': entry.get('timestamp')
                        }
                    )
                    errors.append(error)
                    
        except Exception as e:
            print(f"Error extracting console logs: {e}")
            
        return errors
    
    def extract_error_overlay(self) -> List[ErrorInfo]:
        """Extract errors from Next.js/React error overlays"""
        errors = []
        
        try:
            # Wait for error overlay to appear
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 
                    "div[data-nextjs-dialog-overlay], div[id*='error'], div[class*='error-overlay']"))
            )
            
            # Get page source and parse with BeautifulSoup
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            # Find error containers (adjust selectors based on your framework)
            error_containers = soup.find_all(['div', 'section'], 
                class_=lambda x: x and any(term in str(x).lower() 
                for term in ['error', 'exception', 'runtime']))
            
            for container in error_containers:
                error = self._parse_error_container(container)
                if error:
                    errors.append(error)
                    
        except TimeoutException:
            print("No error overlay detected")
        except Exception as e:
            print(f"Error extracting overlay: {e}")
            
        return errors
    
    def _parse_error_container(self, container) -> Optional[ErrorInfo]:
        """Parse error information from a container element"""
        try:
            # Extract error type
            error_type_elem = container.find(['h1', 'h2', 'h3', 'strong'], 
                string=lambda x: x and 'error' in x.lower())
            error_type = error_type_elem.get_text(strip=True) if error_type_elem else 'Unknown Error'
            
            # Extract error message
            message_elem = container.find(['p', 'div'], 
                class_=lambda x: x and 'message' in str(x).lower())
            if not message_elem:
                # Try to find text with "Error:" prefix
                message_elem = container.find(string=lambda x: x and 'Error:' in str(x))
            message = message_elem.get_text(strip=True) if message_elem else ''
            
            # Extract stack trace
            stack_trace = []
            stack_elements = container.find_all(['div', 'pre', 'code'], 
                class_=lambda x: x and any(term in str(x).lower() 
                for term in ['stack', 'trace', 'call']))
            
            for elem in stack_elements:
                stack_text = elem.get_text(strip=True)
                if stack_text:
                    stack_trace.append(stack_text)
            
            # If no specific stack elements, get all code/pre elements
            if not stack_trace:
                code_blocks = container.find_all(['code', 'pre'])
                stack_trace = [block.get_text(strip=True) for block in code_blocks if block.get_text(strip=True)]
            
            # Extract file path and line numbers from stack trace
            file_path = None
            line_number = None
            column_number = None
            
            if stack_trace:
                # Try to parse first stack trace line
                import re
                first_line = stack_trace[0]
                
                # Pattern: path/to/file.js (line:column) or path/to/file.js:line:column
                match = re.search(r'([^\s]+\.(?:js|tsx?|jsx?))[:\s]+\(?(\d+):(\d+)\)?', first_line)
                if match:
                    file_path = match.group(1)
                    line_number = int(match.group(2))
                    column_number = int(match.group(3))
            
            return ErrorInfo(
                error_type=error_type,
                message=message,
                stack_trace=stack_trace,
                file_path=file_path,
                line_number=line_number,
                column_number=column_number
            )
            
        except Exception as e:
            print(f"Error parsing container: {e}")
            return None
    
    def extract_network_errors(self) -> List[ErrorInfo]:
        """Extract failed network requests"""
        errors = []
        
        try:
            # Execute JavaScript to get performance entries
            script = """
            return window.performance.getEntriesByType('resource')
                .filter(entry => entry.transferSize === 0 && entry.decodedBodySize === 0)
                .map(entry => ({
                    name: entry.name,
                    type: entry.initiatorType,
                    duration: entry.duration
                }));
            """
            
            failed_resources = self.driver.execute_script(script)
            
            for resource in failed_resources:
                error = ErrorInfo(
                    error_type='Network Error',
                    message=f"Failed to load: {resource['name']}",
                    stack_trace=[],
                    additional_info=resource
                )
                errors.append(error)
                
        except Exception as e:
            print(f"Error extracting network errors: {e}")
            
        return errors
    
    def forward(self, url: str, show_errors: bool = True) -> Dict:
        """
        Main method to navigate and extract all errors.
        
        Args:
            url: URL to scrape
            show_errors: Whether to print errors to console
            
        Returns:
            Dictionary containing all extracted errors
        """
        self.errors.clear()
        
        # Navigate to page
        self.navigate(url)
        
        # Extract all types of errors
        console_errors = self.extract_console_errors()
        overlay_errors = self.extract_error_overlay()
        network_errors = self.extract_network_errors()
        
        # Combine all errors
        self.errors.extend(console_errors)
        self.errors.extend(overlay_errors)
        self.errors.extend(network_errors)
        
        # Build result
        result = {
            'url': url,
            'timestamp': time.time(),
            'total_errors': len(self.errors),
            'console_errors': len(console_errors),
            'overlay_errors': len(overlay_errors),
            'network_errors': len(network_errors),
            'errors': [asdict(error) for error in self.errors],
            'has_errors': len(self.errors) > 0
        }
        
        # Show errors if requested
        if show_errors and result['has_errors']:
            self._display_errors(result)
        elif show_errors:
            print(f"\n✅ No errors found on {url}")
        
        return result
    
    def _display_errors(self, result: Dict) -> None:
        """Display errors in a readable format"""
        print(f"\n{'='*80}")
        print(f"🔍 ERROR REPORT FOR: {result['url']}")
        print(f"{'='*80}")
        print(f"\n📊 Summary:")
        print(f"  Total Errors: {result['total_errors']}")
        print(f"  Console Errors: {result['console_errors']}")
        print(f"  Overlay Errors: {result['overlay_errors']}")
        print(f"  Network Errors: {result['network_errors']}")
        
        for i, error in enumerate(result['errors'], 1):
            print(f"\n{'─'*80}")
            print(f"❌ Error {i}/{result['total_errors']}")
            print(f"{'─'*80}")
            print(f"Type: {error['error_type']}")
            print(f"Message: {error['message']}")
            
            if error['file_path']:
                print(f"Location: {error['file_path']}:{error['line_number']}:{error['column_number']}")
            
            if error['stack_trace']:
                print(f"\nStack Trace:")
                for line in error['stack_trace'][:10]:  # Print first 10 lines
                    print(f"  {line}")
            
            if error.get('additional_info'):
                print(f"\nAdditional Info: {json.dumps(error['additional_info'], indent=2)}")
        
        print(f"\n{'='*80}\n")
    
    def save_errors(self, filepath: str = 'errors.json') -> None:
        """Save extracted errors to a JSON file"""
        with open(filepath, 'w') as f:
            json.dump([asdict(error) for error in self.errors], f, indent=2)
        print(f"Errors saved to {filepath}")
    
    def get_screenshot(self, filepath: str = 'error_screenshot.png') -> None:
        """Take a screenshot of the current page"""
        self.driver.save_screenshot(filepath)
        print(f"Screenshot saved to {filepath}")
    
    def close(self) -> None:
        """Close the browser"""
        if self.driver:
            self.driver.quit()
            print("Browser closed")
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


# Example usage
if __name__ == "__main__":
    # Using context manager
    with BrowserErrorScraper(headless=False) as scraper:
        # Navigate and extract all errors with display
        result = scraper.forward('http://localhost:3000', show_errors=True)
        
        # Save to file if errors exist
        if result['has_errors']:
            scraper.save_errors('browser_errors.json')
            scraper.get_screenshot('error_page.png')
