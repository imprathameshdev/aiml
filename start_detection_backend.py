"""Run the backend with the verified Python 3.12 native engine."""
import os, sys, importlib.util
from pathlib import Path
if sys.version_info[:2] != (3, 12):
    raise SystemExit('Use the Python 3.12 virtual environment created by apply_fix.ps1')
project = Path('D:/lotvulture')
bundle = Path('D:/demoo/LotVulture/_internal')
_dll_handles = [os.add_dll_directory(str(p)) for p in [bundle, bundle/'vulturevision'] if p.is_dir()]
native_path = bundle/'vulturevision/_vulturevision.pyd'
if not native_path.is_file():
    raise SystemExit('Bundled native engine is missing: ' + str(native_path))
spec = importlib.util.spec_from_file_location('_vulturevision', native_path)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
sys.modules['_vulturevision'] = native
from vulturevision import VultureVision
sys.path.insert(0, str(project))
os.chdir(project)
import uvicorn
uvicorn.run('backend.app:app', host='127.0.0.1', port=8000)
