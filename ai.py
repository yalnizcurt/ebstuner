"""Load backend-only local settings and select one explicit AI provider."""
import os
import shlex
from pathlib import Path
from bedrock import AmazonBedrockProvider, AIError


def load_environment(path):
    if not Path(path).is_file(): return
    for line in Path(path).read_text().splitlines():
        key, separator, value = line.partition('=')
        key = key.strip()
        if not separator or not key.startswith(('BEDROCK_', 'AWS_', 'STUDIO_', 'LLM_PROVIDER', 'AI_PROVIDER', 'APP_ENV', 'GROQ_')): continue
        try: parts = shlex.split(value, comments=True)
        except ValueError: raise AIError('CONFIGURATION', 'Invalid quoting in backend .env file') from None
        if len(parts) > 1: raise AIError('CONFIGURATION', 'Quote values containing spaces in backend .env file')
        os.environ.setdefault(key, parts[0] if parts else '')


def production_mode():
    return 'production' in (os.getenv('APP_ENV', '').lower(), os.getenv('STUDIO_ENV', '').lower())


def configured_provider():
    app_env = 'production' if production_mode() else os.getenv('APP_ENV', '').lower()
    name = (os.getenv('AI_PROVIDER') or os.getenv('LLM_PROVIDER') or 'bedrock').lower()
    mock_mode = (os.getenv('BEDROCK_MOCK', '') or '').lower() in ('true', '1', 'yes')

    # Production gate: strictly forbid Groq and Mock providers
    if app_env == 'production':
        if name in ('groq', 'mock', 'mock_bedrock') or mock_mode:
            raise AIError('CONFIGURATION', 'Production environment permits Amazon Bedrock only; Groq and mocks are prohibited')
        if name == 'bedrock':
            return AmazonBedrockProvider()
        raise AIError('CONFIGURATION', f'Production environment permits Amazon Bedrock only, requested {name}')

    if mock_mode or name in ('mock', 'mock_bedrock'):
        from bedrock import MockBedrockProvider
        return MockBedrockProvider()
    if name == 'bedrock':
        return AmazonBedrockProvider()
    if name == 'groq':
        if app_env != 'development':
            raise AIError('CONFIGURATION', 'This workflow supports Amazon Bedrock only')
        from groq_provider import GroqProvider
        return GroqProvider()
    raise AIError('CONFIGURATION', f'Unknown or unsupported LLM_PROVIDER: {name}')
