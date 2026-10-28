"""Explicit isolated server for DOM tests. Never used by app/main.py or production startup."""
import tempfile
import uvicorn
from app.config import settings
from app.conversation.engine import engine
from tests.mock_provider import MockProvider


def main():
    with tempfile.TemporaryDirectory() as data:
        settings.data_dir = data
        settings.fish_api_key = 'fixture-fish-key'
        settings.groq_api_key = 'fixture-groq-key'
        settings.gemini_api_key = 'fixture-gemini-key'
        settings.llm_default_response_mode = 'gemini_live'
        from app.providers.tts.fish import FishProvider
        async def synthesize(self, text, voice_id=None):
            return b"\xff\xfb\x90\x00" + b"fixture" * 100, None
        async def open_stream(self, text, voice_id=None):
            async def chunks():
                yield b"\xff\xfb\x90\x00" + b"fixture" * 100
            return chunks(), None
        FishProvider.synthesize = synthesize
        FishProvider.open_stream = open_stream
        engine.providers = {'groq_fish': MockProvider(), 'gemini_fish': MockProvider(), 'gemini_live': MockProvider()}
        uvicorn.run('app.main:app', host='127.0.0.1', port=8020, log_level='warning')


if __name__ == '__main__':
    main()
