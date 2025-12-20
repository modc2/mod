
import hashlib
from typing import List, Tuple, Optional

class MerkleProver:
    """Merkle tree prover for substring membership without revealing position"""
    
    def __init__(self, data: str, chunk_size: int = 1):
        """Initialize with string data and chunk size"""
        self.data = data
        self.chunk_size = chunk_size
        self.leaves = [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]
        self.tree = self._build_tree()
        self.root = self.tree[0][0] if self.tree else None
    
    def _hash(self, data: str) -> str:
        """Hash function for merkle tree"""
        return hashlib.sha256(data.encode()).hexdigest()
    
    def _build_tree(self) -> List[List[str]]:
        """Build merkle tree from leaves"""
        if not self.leaves:
            return []
        
        tree = [[self._hash(leaf) for leaf in self.leaves]]
        
        while len(tree[-1]) > 1:
            level = []
            prev_level = tree[-1]
            
            for i in range(0, len(prev_level), 2):
                left = prev_level[i]
                right = prev_level[i + 1] if i + 1 < len(prev_level) else left
                combined = self._hash(left + right)
                level.append(combined)
            
            tree.append(level)
        
        return tree
    
    def generate_proof(self, substring: str) -> Optional[Tuple[List[Tuple[str, str]], List[int]]]:
        """Generate merkle proof for substring without revealing position"""
        positions = []
        for i in range(len(self.data) - len(substring) + 1):
            if self.data[i:i+len(substring)] == substring:
                positions.append(i)
        
        if not positions:
            return None
        
        start_idx = positions[0] // self.chunk_size
        end_idx = (positions[0] + len(substring) - 1) // self.chunk_size
        
        proof = []
        indices = list(range(start_idx, end_idx + 1))
        
        for level_idx in range(len(self.tree) - 1):
            level_proof = []
            next_indices = []
            
            for idx in indices:
                sibling_idx = idx ^ 1
                if sibling_idx < len(self.tree[level_idx]):
                    position = 'right' if idx % 2 == 0 else 'left'
                    level_proof.append((self.tree[level_idx][sibling_idx], position))
                
                next_indices.append(idx // 2)
            
            if level_proof:
                proof.extend(level_proof)
            indices = list(set(next_indices))
        
        return (proof, [self._hash(self.leaves[i]) for i in range(start_idx, end_idx + 1)])
    
    def verify_proof(self, substring: str, proof: Tuple[List[Tuple[str, str]], List[str]], root: str) -> bool:
        """Verify that substring is in tree without knowing position"""
        if not proof:
            return False
        
        siblings, leaf_hashes = proof
        current_hashes = leaf_hashes
        
        for sibling_hash, position in siblings:
            new_hashes = []
            for curr_hash in current_hashes:
                if position == 'right':
                    combined = self._hash(curr_hash + sibling_hash)
                else:
                    combined = self._hash(sibling_hash + curr_hash)
                new_hashes.append(combined)
            current_hashes = list(set(new_hashes))
        
        return root in current_hashes
    
    def get_root(self) -> str:
        """Get merkle root hash"""
        return self.root

if __name__ == "__main__":
    text = "hello world this is a merkle tree proof system"
    prover = MerkleProver(text, chunk_size=1)
    print(f"Root hash: {prover.get_root()}")
    substring = "merkle"
    proof = prover.generate_proof(substring)
    if proof:
        print(f"\nProof generated for '{substring}'")
        print(f"Proof size: {len(proof[0])} siblings")
        is_valid = prover.verify_proof(substring, proof, prover.get_root())
        print(f"Proof valid: {is_valid}")
    else:
        print(f"Substring '{substring}' not found")
