import requests
import os
import json
from typing import Optional, Dict, Any, List
from pathlib import Path
import time
import mod as m

class  IpfsClient:


    endpoints = ['pin', 'add_mod', 'reg', 'mod', 'pins']
    """Simple IPFS client using requests library only."""
    host_options = ['0.0.0.0', 'ipfs_node']
    def __init__(self, url: str = None):
        self.set_url(url)
        self.session = requests.Session()

    def set_url(self, url: str = None): 
        if url is None:
            for host in self.host_options:
                url = f"http://{host}:5001/api/v0"
                try:
                    response = requests.post(f"{url}/id", timeout=2)
                    if response.status_code == 200:
                        break
                except requests.exceptions.RequestException:
                    print(f"Could not connect to IPFS node at {url}")
        self.url = url 
        print(f"Using IPFS node at {self.url}")
        return {"url": self.url}
    
    def add_file(self, file_path: str) -> Dict[str, Any]:
        """Add a single file to IPFS.
        
        Args:
            file_path: Path to the file to add
            
        Returns:
            Dictionary with IPFS hash and other metadata
        """
        url = f"{self.url}/add"
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f)}
            response = self.session.post(url, files=files)
            response.raise_for_status()
            return response.json()

    def add_data(self, data: Dict[str, Any] = None, pin=True) -> Dict[str, Any]:
        """Add a JSON object to IPFS.
        
        Args:
            data: Dictionary to add as JSON
            
        Returns:
            Dictionary with IPFS hash and other metadata
        """
        url = f"{self.url}/add"
        json_str = json.dumps(data)
        files = {'file': ('data.json', json_str)}
        response = self.session.post(url, files=files)
        response.raise_for_status()
        cid =  response.json()["Hash"]
        if pin:
            self.pin_add(cid)
        return cid

    def rm(self, ipfs_hash: str) -> Dict[str, Any]:
        """Remove a JSON object from IPFS by its hash.
        
        Args:
            ipfs_hash: IPFS hash of the JSON object
        Returns:
            Dictionary with removal status
        """
        self.pin_rm(ipfs_hash)
        return {"Status": "Removed"}


    def get_data(self, ipfs_hash: str) -> Dict[str, Any]:
        """Retrieve a JSON object from IPFS by its hash.
        
        Args:
            ipfs_hash: IPFS hash of the JSON object
        Returns:
            Dictionary with the JSON content
        """
        content = self.get_file(ipfs_hash)
        return json.loads(content)

    def get_file(self, ipfs_hash: str) -> bytes:
        """Retrieve a file from IPFS by its hash.
        
        Args:
            ipfs_hash: IPFS hash of the file
        Returns:
            File content as bytes
        """
        url = f"{self.url}/cat"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        return response.content
    
    def add_folder(self, folder_path: str, recursive: bool = True, ignore_terms = ['__pycache__', '/.']) -> List[Dict[str, Any]]:
        """Add a folder to IPFS.
        
        Args:
            folder_path: Path to the folder to add
            recursive: Whether to add files recursively
            
        Returns:
            List of dictionaries with IPFS hashes for each file
        """
        url = f"{self.url}/add"
        
        if not os.path.exists(folder_path):
            raise FileNotFoundError(f"Folder not found: {folder_path}")
        
        if not os.path.isdir(folder_path):
            raise ValueError(f"Path is not a directory: {folder_path}")
        
        results = []
        folder_path = Path(folder_path)
        
        # Collect all files
        files_to_upload = []
        file_filter = lambda p: all(term not in str(p) for term in ignore_terms)
        
        if recursive:
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    file_full_path = os.path.join(root, file)
                    if not file_filter(file_full_path):
                        continue
                    relative_path = os.path.relpath(file_full_path, folder_path.parent)
                    files_to_upload.append((file_full_path, relative_path))
        else:
            for item in os.listdir(folder_path):
                item_path = os.path.join(folder_path, item)
                if os.path.isfile(item_path):
                    if not file_filter(file_full_path):
                        continue
                    relative_path = os.path.relpath(item_path, folder_path.parent)
                    files_to_upload.append((item_path, relative_path))
        
        # Upload files
        files = []
        for file_path, relative_path in files_to_upload:
            print(f"Adding file to upload list: {relative_path}")
            files.append(('file', (relative_path, open(file_path, 'rb'))))
        
        try:
            params = {'wrap-with-directory': 'true'} if recursive else {}
            response = self.session.post(url, files=files, params=params)
            response.raise_for_status()
            
            # Parse response - IPFS returns newline-delimited JSON
            for line in response.text.strip().split('\n'):
                if line:
                    results.append(json.loads(line))
        finally:
            # Close all file handles
            for _, (_, file_obj) in files:
                file_obj.close()
        
        return results[-2]["Hash"]

    def get_folder(self, ipfs_hash: str) -> Dict[str, Any]:
        """Retrieve folder information from IPFS by hash.
        
        Args:
            ipfs_hash: IPFS hash of the folder
        Returns:
            Dictionary with folder metadata
        """
        url = f"{self.url}/ls"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        file2content = {}
        for item in response.json().get('Objects', []):
            for link in item.get('Links', []):
                try:
                    file2content[link['Name']] = self.cat(link['Hash'])
                except Exception as e:
                    print(f"Error retrieving {link['Name']}: {e}")
        return file2content

    def cat(self, ipfs_hash: str) -> bytes:
        """Retrieve content from IPFS by hash.
        
        Args:
            ipfs_hash: IPFS hash of the content
            
        Returns:
            Content as bytes
        """
        url = f"{self.url}/cat"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        return response.content
    
        
        return {'success': True, 'path': output_path}

    def pin_rm(self, ipfs_hash: str) -> Dict[str, Any]:
        """Unpin content from local IPFS node.
        
        Args:
            ipfs_hash: IPFS hash to unpin
        Returns:
            Dictionary with unpin status

        """
        url = f"{self.url}/pin/rm"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        return response.json()
    def pinned(self, ipfs_hash: str) -> bool:
        """Check if content is pinned on local IPFS node.
        
        Args:
            ipfs_hash: IPFS hash to check

        Returns:
            True if pinned, False otherwise
        """
        pins = self.pins()
        return ipfs_hash in pins.get('Keys', {})

    
    def pin_add(self, ipfs_hash: str) -> Dict[str, Any]:
        """Pin content to local IPFS node.
        
        Args:
            ipfs_hash: IPFS hash to pin
            
        Returns:
            Dictionary with pin status
        """
        url = f"{self.url}/pin/add"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        return response.json()

    def pins(self, cid: str = None) -> Dict[str, Any]:
        """List pinned content on local IPFS node.
        
        Returns:
            Dictionary with pinned content
        """
        url = f"{self.url}/pin/ls"
        response = self.session.post(url)
        response.raise_for_status()
        if cid:
            pins = response.json().get('Keys', {})
            return {cid: pins.get(cid)} if cid in pins else {}
        return response.json()
    
    def pin_rm(self, ipfs_hash: str) -> Dict[str, Any]:
        """Unpin content from local IPFS node.
        
        Args:
            ipfs_hash: IPFS hash to unpin
            
        Returns:
            Dictionary with unpin status
        """
        url = f"{self.url}/pin/rm"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        return response.json()

    def _rm_all_pins(self) -> None:
        """Unpin all content from local IPFS node."""
        pins = self.pins()
        for cid in pins.get('Keys', {}).keys():
            print( f"Unpinning CID: {cid}")
            try:
                self.pin_rm(cid)
            except Exception as e:
                print(f"Error unpinning {cid}: {e}")
    
    def id(self) -> Dict[str, Any]:
        """Get IPFS node identity information.
        
        Returns:
            Dictionary with node ID and addresses
        """
        url = f"{self.url}/id"
        response = self.session.post(url)
        response.raise_for_status()
        return response.json()
    
    def version(self) -> Dict[str, Any]:
        """Get IPFS version information.
        
        Returns:
            Dictionary with version details
        """
        url = f"{self.url}/version"
        response = self.session.post(url)
        response.raise_for_status()
        return response.json()

    def start_node(self) -> None:
    
        # This is a placeholder implementation.
        # Actual implementation would depend on the environment and setup.
        dirpath = Path(__file__).parent.parent
        return os.system(f"cd {dirpath} && docker-compose up -d")

    def stop_node(self) -> None:
        """Stop the IPFS node if it's running."""
        # This is a placeholder implementation.
        # Actual implementation would depend on the environment and setup.
        dirpath = Path(__file__).parent.parent
        return os.system(f"cd {dirpath} && docker-compose down")

    def node_running(self) -> bool:
        """Check if the IPFS node is running."""
        try:
            self.id()
            return True
        except requests.exceptions.RequestException:
            return False

    def ensure_node(self, stepback_time=1):
        while not self.node_running():
            print("IPFS node not running. Starting node...")
            self.start_node()
            time.sleep(1)

    def test(self) -> bool:
        """Test connection to IPFS node by adding and retrieving test data.
        
        Returns:
            True if test is successful, False otherwise
        """
        test_obj = {"test_key": "test_value"}
        print("Testing IPFS data connection...", test_obj)
        ipfs_hash = self.add_data(test_obj)
        retrieved_obj = self.get_data(ipfs_hash)
        return retrieved_obj == test_obj


    def __str__(self):
        return f"IpfsClient(url={self.url})"