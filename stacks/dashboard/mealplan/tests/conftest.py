import os
import sys

# Put the mealplan app dir on the path so tests can `import newpi`.
MEALPLAN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, MEALPLAN)
