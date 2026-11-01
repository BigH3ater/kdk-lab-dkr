import os
import sys

import yaml
import pytest

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, STACK)


@pytest.fixture
def cfg():
    return yaml.safe_load(open(os.path.join(STACK, "config.yaml")))
