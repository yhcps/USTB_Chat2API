"""Fix server.py - remove old functions now in utils, adjust imports"""
import re

path = "src/ustb_chat2api/server.py"
content = open(path, "r", encoding="utf-8").read()

# Remove old functions that are now in utils
# Remove log_error function
content = re.sub(
    r"def log_error\(context: str\).*?(?=\n\n|\ndef |\n#|\napp =|\n@app)",
    "", content, flags=re.DOTALL
)

# Remove content_to_text function
content = re.sub(
    r"def content_to_text\(content\).*?(?=\n\n|\ndef |\n#|\napp =|\n@app)",
    "", content, flags=re.DOTALL
)

# Remove _log_tools function
content = re.sub(
    r"def _log_tools\(calls\).*?(?=\n\n|\ndef |\n#|\napp =|\n@app)",
    "", content, flags=re.DOTALL
)

# Remove DEFAULT_CONFIG block
content = re.sub(r"DEFAULT_CONFIG = \{.*?\n\}", "", content, flags=re.DOTALL)

# Remove load_config function
content = re.sub(
    r"def load_config\(\) -> dict:.*?(?=\n\n|\ndef |\n#|\napp =|\n@app)",
    "", content, flags=re.DOTALL
)

# Remove get_host function
content = re.sub(
    r"def get_host\(\) -> str:.*?(?=\n\n|\ndef |\n#|\napp =|\n@app)",
    "", content, flags=re.DOTALL
)

# Remove check_auth function
content = re.sub(
    r"def check_auth\(request: Request\).*?(?=\n\n|\ndef |\n#|\napp =|\n@app)",
    "", content, flags=re.DOTALL
)

# Remove old constants
for const in ["BASE_DIR", "CONFIG_FILE", "KEYS_FILE", "ERROR_LOG"]:
    content = re.sub(rf"^{const} = .*?\n", "", content, flags=re.MULTILINE)

# Add proper imports from utils
content = content.replace(
    'from . import dashboard  # noqa: E402  (同目录模块: 本地仪表盘)',
    'from .utils import load_config, get_host, log_error, log_tools, content_to_text, verify_key\nfrom . import dashboard  # noqa: E402'
)

# Fix references
content = content.replace("_log_tools(parser.events)", "log_tools(parser.events)")
content = content.replace("_log_tools(calls)", "log_tools(calls)")

open(path, "w", encoding="utf-8").write(content)
print("server.py fixed successfully")
