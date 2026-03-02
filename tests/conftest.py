import os
import pytest

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def load_fixture(name):
    path = os.path.join(FIXTURE_DIR, name)
    with open(path, 'rb') as f:
        return f.read()
