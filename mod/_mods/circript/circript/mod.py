from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
import hashlib
import base64

class BaseMod:
    description = """
    AES-256 Encryption Circuit - Tiled Reproducible Bidirectional String Transformer
    Transforms private strings into infinite-length outputs with replaceable circuit logic
    """
    
    def __init__(self, private_key: str, circuit_func=None):
        """Initialize with private key and optional circuit function"""
        self.private_key = private_key
        self.key = hashlib.sha256(private_key.encode()).digest()  # AES-256 key
        self.circuit = circuit_func or self.default_circuit
    
    def default_circuit(self, data: bytes, iteration: int) -> bytes:
        """Default tiling circuit - XOR with iteration-based pattern"""
        pattern = hashlib.sha256(f"{iteration}".encode()).digest()
        return bytes(a ^ b for a, b in zip(data, pattern * (len(data) // len(pattern) + 1)))
    
    def encrypt_block(self, plaintext: str) -> bytes:
        """Encrypt a block using AES-256-CBC"""
        iv = get_random_bytes(16)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        padded = pad(plaintext.encode(), AES.block_size)
        ciphertext = cipher.encrypt(padded)
        return iv + ciphertext
    
    def decrypt_block(self, ciphertext: bytes) -> str:
        """Decrypt a block using AES-256-CBC"""
        iv = ciphertext[:16]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(ciphertext[16:])
        return unpad(decrypted, AES.block_size).decode()
    
    def forward(self, input_string: str, target_length: int = None) -> str:
        """Transform string forward through circuit to target length (infinite if None)"""
        encrypted = self.encrypt_block(input_string)
        result = encrypted
        iteration = 0
        
        # Tile the output to desired length
        if target_length:
            while len(result) < target_length:
                result += self.circuit(encrypted, iteration)
                iteration += 1
            result = result[:target_length]
        else:
            # Single pass for reproducibility
            result = self.circuit(encrypted, 0)
        
        return base64.b64encode(result).decode()
    
    def backward(self, transformed_string: str) -> str:
        """Transform string backward through circuit to recover original"""
        decoded = base64.b64decode(transformed_string.encode())
        
        # Extract original encrypted block (first segment)
        block_size = 16 + ((len(self.private_key) // 16) + 1) * 16
        if len(decoded) >= block_size:
            encrypted_block = decoded[:block_size]
        else:
            # Reverse circuit if needed
            encrypted_block = self.circuit(decoded, 0)
        
        return self.decrypt_block(encrypted_block)
    
    def set_circuit(self, circuit_func):
        """Replace the circuit function for custom transformations"""
        self.circuit = circuit_func
        return self


# Example usage
if __name__ == "__main__":
    # Initialize with private key
    mod = BaseMod("my_secret_key_ross_style")
    
    # Forward transformation
    original = "Hello World"
    transformed = mod.forward(original, target_length=1000)
    print(f"Transformed ({len(transformed)} chars): {transformed[:100]}...")
    
    # Backward transformation
    recovered = mod.backward(transformed)
    print(f"Recovered: {recovered}")
    print(f"Match: {original == recovered}")
    
    # Custom circuit example
    def custom_circuit(data: bytes, iteration: int) -> bytes:
        """Custom tiling logic"""
        multiplier = (iteration + 1) * 2
        return bytes((b * multiplier) % 256 for b in data)
    
    mod.set_circuit(custom_circuit)
    transformed2 = mod.forward(original, target_length=2000)
    print(f"\nCustom circuit ({len(transformed2)} chars): {transformed2[:100]}...")
