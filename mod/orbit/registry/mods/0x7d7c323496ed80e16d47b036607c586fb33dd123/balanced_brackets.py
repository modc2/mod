import sys

def is_balanced(s: str) -> bool:
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    
    for char in s:
        if char in '([{':
            stack.append(char)
        elif char in ')]}':
            if not stack or stack[-1] != pairs[char]:
                return False
            stack.pop()
    
    return len(stack) == 0

def main():
    data = sys.stdin.read().strip().splitlines()
    if not data:
        return
    
    n = int(data[0])
    for i in range(1, n + 1):
        line = data[i].strip()
        print('YES' if is_balanced(line) else 'NO')

if __name__ == '__main__':
    main()
