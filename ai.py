"""Load backend-only local settings and select one explicit AI provider."""
import os
import shlex
from pathlib import Path
from bedrock import AmazonBedrockProvider, AIError
from groq_provider import GroqProvider


def load_environment(path):
    if not Path(path).is_file(): return
    for line in Path(path).read_text().splitlines():
        key, separator, value = line.partition('=')
        key = key.strip()
        if not separator or not key.startswith(('GROQ_', 'BEDROCK_', 'AWS_', 'STUDIO_', 'LLM_PROVIDER')): continue
        try: parts = shlex.split(value, comments=True)
        except ValueError: raise AIError('CONFIGURATION', 'Invalid quoting in backend .env file') from None
        if len(parts) > 1: raise AIError('CONFIGURATION', 'Quote values containing spaces in backend .env file')
        os.environ.setdefault(key, parts[0] if parts else '')


def configured_provider():
    name = os.getenv('LLM_PROVIDER', 'groq').lower()
    if name == 'groq': return GroqProvider()
    if name == 'bedrock': return AmazonBedrockProvider()
    raise AIError('CONFIGURATION', 'LLM_PROVIDER must be groq or bedrock')
