import requests
import os
import json
from typing import Optional, Dict, Any, List
from pathlib import Path
import time
import commune as c

class  IpfsClient:
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

    def add_data(self, data: Dict[str, Any] = None, pin=False) -> Dict[str, Any]:
        """Add a JSON object to IPFS.
        
        Args:
            data: Dictionary to add as JSON
            
        Returns:
            Dictionary with IPFS hash and other metadata
        """
        url = f"{self.base_url}/add"
        json_str = json.dumps(data)
        files = {'file': ('data.json', json_str)}
        response = self.session.post(url, files=files)
        response.raise_for_status()
        cid =  response.json()["Hash"]
        if pin:
            print(f"Pinning data with CID: {cid}")
            self.pin_add(cid)
        return cid


    def get_data(self, ipfs_hash: str) -> Dict[str, Any]:
        """Retrieve a JSON object from IPFS by its hash.
        
        Args:
            ipfs_hash: IPFS hash of the JSON object
        Returns:
            Dictionary with the JSON content
        """
        content = self.get_file(ipfs_hash)
        return json.loads(content)

    def pin_mod(self, mod: c.Mod='store', pin=True) -> Dict[str, Any]:
        """Pin a commune Mod to IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Dictionary with pin status
        """
        file2cid = {}
        content = c.content(mod)
        for file,content in content.items():
            cid = self.add_data(content, pin=pin)
            file2cid[file] = cid
        print(f"Pinning mod from dirpath: {file2cid} to IPFS")
        mod_cid = self.add_data(file2cid, pin=pin)
        return mod_cid

    def get_mod(self, ipfs_hash: str) -> Dict[str, Any]:
        """Retrieve a commune Mod from IPFS by its hash.
        
        Args:
            ipfs_hash: IPFS hash of the Mod
        Returns:
            Mod content as dictionary
        """
        file2cid = self.get_data(ipfs_hash)
        file2content = {}
        for file,cid in file2cid.items():
            file2content[file] = self.get_data(cid)
        return file2content
    

    def reg(self, mod = 'store', key=None, comment=None, update=False) -> Dict[str, Any]:
        # het =wefeererwfwefhuwoefhiuhuihewds wfweferfgr
        path = f'~/.ipfs/mods/{mod}'
        cid = self.add_mod(mod)
        current_time = c.time()
        default = {'history': [],  'created':  current_time,  'updated': current_time}
        info = c.get(path, None, update=update)
        if info is None:
            info = default
        if len(info['history']) == 0:
            info['created'] = current_time
        prev_cid = info['history'][-1]['cid'] if len(info['history']) > 0 else None
        if cid != prev_cid:
            info['history'].append({'cid': cid, 'time': current_time})
            info['updated'] = current_time
            print(f"Registering mod {mod} with CID {cid} at time {current_time}")
            if comment:
                info['history'][-1]['comment'] = comment
            c.put(path, info)

        return info


    def add_mod(self, mod: c.Mod) -> Dict[str, Any]:
        """Add a commune Mod to IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Dictionary with IPFS hash and other metadata
        """
        path = f'~/.ipfs/mods/{mod}'
        file2cid = {}
        content = c.content(mod)
        for file,content in content.items():
            cid = self.add_data(content, pin=True)
            file2cid[file] = cid
            print(f"Added file: {file} with CID: {cid}")

            
        return self.add_data(file2cid)


    def file2cid(self, mod = 'store') -> Dict[str, str]:
        """Get mapping of files to their IPFS CIDs for a commune Mod.
        
        Args:
            mod: Commune Mod object
        Returns:
            Dictionary fam mapping file names to IPFS CIDs
        """
        dp = c.dirpath(mod)
        print(f"Getting file to CID mapping for mod from dirpath: {dp}")
        file2cid = {}
        content = c.content(mod)
        for file,content in content.items():
            cid = self.add_data(content)
            file2cid[file] = cid
        return file2cid
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
        url = f"{self.base_url}/ls"
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
        url = f"{self.base_url}/cat"
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
        url = f"{self.base_url}/pin/rm"
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
        url = f"{self.base_url}/pin/add"
        params = {'arg': ipfs_hash}
        response = self.session.post(url, params=params)
        response.raise_for_status()
        return response.json()

    def pins(self, cid: str = None) -> Dict[str, Any]:
        """List pinned content on local IPFS node.
        
        Returns:
            Dictionary with pinned content
        """
        url = f"{self.base_url}/pin/ls"
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

    def start_node(self) -> None:
        """Start the IPFS node if it's not running."""
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

    def test_mod(self, mod='store') -> bool:
        """Test connection to IPFS node by adding and retrieving a commune Mod.
        
        Returns:
            True if test is successful, False otherwise
        """
        print("Testing IPFS mod connection...", mod)
        add_result = self.add_mod(mod)
        ipfs_hash = add_result
        retrieved_mod = self.get_mod(ipfs_hash)
        original_file2cid = self.file2cid(mod)
        new_file2cid = {}
        for file,content in retrieved_mod.items():
            cid = self.add_data(content)
            new_file2cid[file] = cid
            assert original_file2cid[file] == cid, f"CID mismatch for file {file}: {original_file2cid[file]} != {cid}"
        return original_file2cid == new_file2cid


    def test_data(self) -> bool:
        """Test connection to IPFS node by adding and retrieving test data.
        
        Returns:
            True if test is successful, False otherwise
        """
        test_obj = {"test_key": "test_value"}
        print("Testing IPFS data connection...", test_obj)
        ipfs_hash = self.add_data(test_obj)
        retrieved_obj = self.get_data(ipfs_hash)
        return retrieved_obj == test_obj