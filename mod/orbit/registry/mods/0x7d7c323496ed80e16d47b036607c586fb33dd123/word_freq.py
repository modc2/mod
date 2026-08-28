import sys
import re
from collections import Counter

def main():
    text = sys.stdin.read()
    # Find all words (maximal runs of letters), case-insensitive
    words = re.findall(r'[A-Za-z]+', text)
    # Convert to lowercase for counting
    words_lower = [w.lower() for w in words]
    
    # Count frequencies
    freq = Counter(words_lower)
    
    # Sort by frequency descending, then alphabetically ascending
    sorted_words = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    
    # Print top 3 (or fewer)
    for word, count in sorted_words[:3]:
        print(f"{word} {count}")

if __name__ == "__main__":
    main()
