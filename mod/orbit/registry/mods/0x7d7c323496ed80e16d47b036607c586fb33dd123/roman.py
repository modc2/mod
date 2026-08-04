import sys

def roman_to_int(s: str) -> int:
    roman_values = {
        'I': 1, 'V': 5, 'X': 10, 'L': 50,
        'C': 100, 'D': 500, 'M': 1000
    }
    total = 0
    prev_value = 0
    for char in reversed(s):
        value = roman_values[char]
        if value < prev_value:
            total -= value
        else:
            total += value
        prev_value = value
    return total

def main():
    data = sys.stdin.read().strip().split()
    if not data:
        return
    n = int(data[0])
    for i in range(1, n + 1):
        print(roman_to_int(data[i]))

if __name__ == "__main__":
    main()
