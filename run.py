import sys
import os

# pywebview + Windows 접근성 API 재귀 이슈 방지
sys.setrecursionlimit(50000)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import main

if __name__ == "__main__":
    main()
