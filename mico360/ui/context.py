"""Shared application context: the services every page needs, in one place."""
from __future__ import annotations

from ..config import Settings, connect_api_key
from ..core.history import History
from ..core.cloud_client import CloudGenerator, check_cloud_status
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

    # -- AI provider ("mode") ----------------------------------------------
    def provider(self) -> str:
        """'local' (Ollama) or 'cloud' (MICO360 Connect)."""
        return self.settings.get("ai_provider", "local")

    def ai_status(self, max_age: float = 4.0):
        """Reachability + model list for the active provider (uniform shape:
        .running / .models / .error). Cached briefly so a page navigation that
        reads it more than once doesn't make repeated (possibly remote) calls."""
        import time
        prov = self.provider()
        cache = getattr(self, "_ai_status_cache", None)
        if cache and cache[0] == prov and cache[1] > time.monotonic():
            return cache[2]
        st = (check_cloud_status(key=connect_api_key()) if prov == "cloud"
              else check_status(self.settings.get("ollama_host")))
        self._ai_status_cache = (prov, time.monotonic() + max_age, st)
        return st

    def ai_model(self) -> str:
        """The model chosen for the active provider."""
        if self.provider() == "cloud":
            return self.settings.get("cloud_model", "")
        return self.settings.get("ollama_model", "")

    def set_ai_model(self, model: str) -> None:
        self.settings.set("cloud_model" if self.provider() == "cloud" else "ollama_model", model)

    # -- ollama (local-only surfaces: install model, environment panel) -----
    def ollama_status(self):
        return check_status(self.settings.get("ollama_host"))

    def generator(self):
        """A minutes generator for the active provider (both expose
        .model and .generate_minutes())."""
        if self.provider() == "cloud":
            return CloudGenerator(key=connect_api_key(),
                                  model=self.settings.get("cloud_model", ""))
        return OllamaGenerator(self.settings.get("ollama_host"),
                               self.settings.get("ollama_model"))

    def active_profile(self):
        pid = self.settings.get("active_profile", "")
        return self.profiles.get(pid) if pid else None

    def close(self):
        self.history.close()
