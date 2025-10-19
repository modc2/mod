import requests
import os
import json
from typing import Optional, Dict, Any, List
from pathlib import Path


class IPFSClient:
    """Simple IPFS client using requests library only."""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 5001, protocol: str = "http"):
        self.base_url = f"{protocol}://{host}:{port}/api/v0"
        self.session = requests.Session()
    
    def add_file(self, file_path: str) -> Dict[str, Any]:
        """Add a single file to IPFS.
        
        Args:
            file_path: Path to the file to add
            
        Returns:
            Dictionary with IPFS hash and other metadata
        """
        url = f"{self.base_url}/add"
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f)}
            response = self.session.post(url, files=files)
            response.raise_for_status()
            return response.json()

    def get_file(self, ipfs_hash: str) -> bytes:
        """Retrieve a file from IPFS by its hash.
        
        Args:
            ipfs_hash: IPFS hash of the file
        Returns:
            File content as bytes
        """
        url = f"{self.base_url}/cat"
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
        url = f"{self.base_url}/add"
        
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
        
        return results

    def get_folder(self, ipfs_hash: str) -> Dict[str, Any]:
        """Retrieve folder information from IPFS by hash.
        
        Args:
            ipfs_hash: IPFS hash of the folder
        Returns:
            Dictionary with folder metadata
        """
        url = f"{self.base_url}/ls"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        file2content = {}
        for item in response.json().get('Objects', []):
            for link in item.get('Links', []):
                file2content[link['Name']] = self.cat(link['Hash'])
        return file2content


    
    def cat(self, ipfs_hash: str) -> bytes:
        """Retrieve content from IPFS by hash.
        
        Args:
            ipfs_hash: IPFS hash of the content
            
        Returns:
            Content as bytes
        """
        url = f"{self.base_url}/cat"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        return response.content
    
    def get(self, ipfs_hash: str, output_path: str) -> Dict[str, Any]:
        """Download content from IPFS to a file.
        
        Args:
            ipfs_hash: IPFS hash of the content
            output_path: Path where to save the content
            
        Returns:
            Dictionary with operation status
        """
        url = f"{self.base_url}/get"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params, stream=True)
        response.raise_for_status()
        
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return {'success': True, 'path': output_path}
    
    def pin_add(self, ipfs_hash: str) -> Dict[str, Any]:
        """Pin content to local IPFS node.
        
        Args:
            ipfs_hash: IPFS hash to pin
            
        Returns:
            Dictionary with pin status
        """
        url = f"{self.base_url}/pin/add"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def pin_rm(self, ipfs_hash: str) -> Dict[str, Any]:
        """Unpin content from local IPFS node.
        
        Args:
            ipfs_hash: IPFS hash to unpin
            
        Returns:
            Dictionary with unpin status
        """
        url = f"{self.base_url}/pin/rm"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def id(self) -> Dict[str, Any]:
        """Get IPFS node identity information.
        
        Returns:
            Dictionary with node ID and addresses
        """
        url = f"{self.base_url}/id"
        response = self.session.post(url)
        response.raise_for_status()
        return response.json()
    
    def version(self) -> Dict[str, Any]:
        """Get IPFS version information.
        
        Returns:
            Dictionary with version details
        """
        url = f"{self.base_url}/version"
        response = self.session.post(url)
        response.raise_for_status()
        return response.json()
