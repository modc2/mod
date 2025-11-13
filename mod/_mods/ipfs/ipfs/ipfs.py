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
    node_name = 'ipfs.node'
    host_options = ['0.0.0.0', node_name]
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

    def add(self, data: Dict[str, Any] = None, pin=True) -> Dict[str, Any]:
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
    put = add
    def rm(self, ipfs_hash: str) -> Dict[str, Any]:
        """Remove a JSON object from IPFS by its hash.
        
        Args:
            ipfs_hash: IPFS hash of the JSON object
        Returns:
            Dictionary with removal status
        """
        try:
            self.pin_rm(ipfs_hash)
        except Exception as e:
            print(f"Error unpinning {ipfs_hash}: {e}")
        return {"Status": "Removed"}


    def get(self, ipfs_hash: str) -> Dict[str, Any]:
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

    def cid(self, data: Dict[str, Any] = None) -> str:
        """Add data to IPFS and return its CID.
        
        Args:
            data: Dictionary to add as JSON 

        """
        return self.add(data, pin=False)

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


    def test(self) -> bool:
        """Test connection to IPFS node by adding and retrieving test data.
        
        Returns:
            True if test is successful, False otherwise
        """
        test_obj = {"test_key": "test_value"}
        print("Testing IPFS data connection...", test_obj)
        ipfs_hash = self.add(test_obj)
        retrieved_obj = self.get(ipfs_hash)
        return retrieved_obj == test_obj

    def __str__(self):
        return f"IpfsClient(url={self.url})"

    def ensure_env(self):
        """Ensure that the IPFS environment is set up."""
        m.fn('pm/up')(self.node_name)