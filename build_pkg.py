"""
Build script: transform legacy flat files into src/ustb_chat2api/ package
"""
import os
import re
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src", "ustb_chat2api")
os.makedirs(SRC_DIR, exist_ok=True)

def transform_server():
    """Transform chat2api.py -> server.py with proper imports"""
    content = open(os.path.join(BASE_DIR, "chat2api.py"), "r", encoding="utf-8").read()

    # Replace imports for relative package
    content = content.replace('import dashboard', 'from . import dashboard')
    content = content.replace('from dashboard import record_request', 'from .dashboard import record_request')

    # Remove old config/log functions (now in utils)
    lines = content.split('\n')
    result = []
    skip = False
    in_config_func = False
    for i, line in enumerate(lines):
        # Skip the old config/log functions that are now in utils
        stripped = line.strip()
        if any(stripped.startswith(x) for x in [
            'def log_error', 'def content_to_text', 'def _log_tools',
            'DEFAULT_CONFIG', 'def load_config', 'def get_host',
            'def check_auth',
        ]):
            skip = True
            if stripped.startswith('DEFAULT_CONFIG'):
                # skip the whole dict literal
                in_config_func = True
                continue
        if skip:
            # Check if we're past the function body
            if stripped.startswith('def ') or stripped.startswith('#'):
                if stripped in ['def log_error', 'def content_to_text', 'def _log_tools',
                                'def load_config', 'def get_host', 'def check_auth']:
                    continue  # still in the skip list
            if in_config_func:
                if stripped == '' and not any(l.strip() for l in lines[max(0,i-2):i]):
                    continue
                if stripped == '}':
                    in_config_func = False
                    skip = False
                    continue
                continue
            if stripped == '' or stripped.startswith('#'):
                continue
            # Check if next line is a non-empty, non-indented line (function boundary)
            if i + 1 < len(lines) and lines[i+1].strip() and not lines[i+1].startswith((' ', '\t')):
                if not stripped.startswith(('def ', '#')):
                    skip = False
                    result.append(line)
                    continue
            if stripped.startswith('def ') or stripped.startswith('@'):
                skip = False
                result.append(line)
                continue
            continue
        result.append(line)
    content = '\n'.join(result)

    # Remove old constants
    for old_const in ['BASE_DIR', 'CONFIG_FILE', 'KEYS_FILE', 'ERROR_LOG']:
        content = re.sub(
            rf'^{old_const}\s*=\s*.*\n?',
            '',
            content,
            flags=re.MULTILINE
        )

    # Add proper imports
    content = content.replace(
        'from . import utils',
        'from . import utils\nfrom .utils import load_config, get_host, log_error, log_tools, content_to_text, verify_key'
    )

    out_path = os.path.join(SRC_DIR, "server.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Written: {out_path}")

def transform_tray():
    """Transform tray.py -> tray.py with proper imports"""
    content = open(os.path.join(BASE_DIR, "tray.py"), "r", encoding="utf-8").read()
    content = content.replace('import chat2api', 'from . import server as chat2api')
    content = content.replace('from chat2api import', 'from .server import')
    content = content.replace('from cli import', 'from .cli import')
    out_path = os.path.join(SRC_DIR, "tray.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Written: {out_path}")

def transform_tui():
    """Transform tui.py -> tui.py with proper imports"""
    content = open(os.path.join(BASE_DIR, "tui.py"), "r", encoding="utf-8").read()
    content = content.replace('import chat2api', 'from . import server as chat2api')
    content = content.replace('from chat2api import', 'from .server import')
    content = content.replace('from cli import', 'from .cli import')
    out_path = os.path.join(SRC_DIR, "tui.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Written: {out_path}")

def transform_cli():
    """Transform cli.py -> cli.py with proper imports"""
    content = open(os.path.join(BASE_DIR, "cli.py"), "r", encoding="utf-8").read()
    content = content.replace('from chat2api import', 'from .server import')
    out_path = os.path.join(SRC_DIR, "cli.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Written: {out_path}")

def transform_dashboard():
    """Transform dashboard.py -> dashboard.py with proper imports"""
    content = open(os.path.join(BASE_DIR, "dashboard.py"), "r", encoding="utf-8").read()
    content = content.replace('from chat2api import', 'from .server import')
    content = content.replace('from cli import', 'from .cli import')
    out_path = os.path.join(SRC_DIR, "dashboard.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Written: {out_path}")

if __name__ == "__main__":
    transform_server()
    transform_tray()
    transform_tui()
    transform_cli()
    transform_dashboard()
    print("All transforms complete!")
