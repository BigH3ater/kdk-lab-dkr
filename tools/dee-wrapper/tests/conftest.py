import os
import sys

# Import the wrapper module (tools/dee-wrapper/main.py) as `main`.
WRAPPER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WRAPPER_DIR)
