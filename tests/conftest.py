"""Load pure calculation modules without installing Home Assistant."""
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType('niba_test')
package.__path__ = [str(ROOT / 'custom_components' / 'niba')]
sys.modules['niba_test'] = package
