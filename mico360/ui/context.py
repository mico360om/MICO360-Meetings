"""Shared application context: the services every page needs, in one place."""
from __future__ import annotations

from ..config import Settings
from ..core.history import History
from ..core.ollama_client import OllamaGenerator, check_status
from ..core.profiles import ProfileStore
from ..core.prompts import PromptLibrary
from ..core.transcription import TranscriptionEngine
from ..core.tasks import ActionItemStore
from ..core.meeting_types import MeetingTypeStore


class AppContext:
    def __init__(self):
        self.settings = Settings()
        self.history = History()
        self.profiles = ProfileStore()
        self.prompts = PromptLibrary()
        self.action_items = ActionItemStore(self.history)
        self.meeting_types = MeetingTypeStore()
        self._engine: TranscriptionEngine | None = None

    # -- transcription engine (rebuilt when model settings change) ----------
    def transcription_engine(self) -> TranscriptionEngine:
        size = self.settings.get("whisper_model", "base")
        device = self.settings.get("whisper_device", "auto")
        compute = self.settings.get("whisper_compute", "int8")
        if (self._engine is None or self._engine.model_size != size
                or self._engine.device != device or self._engine.compute_type != compute):
            self._engine = TranscriptionEngine(size, device, compute)
        return self._engine

    # -- ollama -------------------------------------------------------------
    def ollama_status(self):
        return check_status(self.settings.get("ollama_host"))

    def generator(self) -> OllamaGenerator:
        return OllamaGenerator(self.settings.get("ollama_host"),
                               self.settings.get("ollama_model"))

    def active_profile(self):
        pid = self.settings.get("active_profile", "")
        return self.profiles.get(pid) if pid else None

    def close(self):
        self.history.close()
