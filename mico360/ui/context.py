"""Shared application context: the services every page needs, in one place."""
from __future__ import annotations

from ..config import Settings, connect_api_key
from ..core.history import History
from ..core.cloud_client import CloudGenerator, check_cloud_status
from ..core.cloud_client import invalidate_status_cache as invalidate_cloud_status
from ..core.ollama_client import OllamaGenerator, check_status
from ..core.ollama_client import invalidate_status_cache as invalidate_ollama_status
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

    def ai_status(self, max_age: float | None = None, force: bool = False):
        """Reachability + model list for the active provider (uniform shape:
        .running / .models / .error).

        Cheap and bounded (H14): the provider checks use ~2-3 s network
        timeouts with a hard wall-clock limit, and results are cached per
        provider + host/key — a success for 30 s, a failure for 5 s — so page
        navigations never make repeated (possibly remote) calls. ``force``
        bypasses the cache; ``max_age`` is accepted for compatibility (0 forces).
        """
        use_cache = not force and not (max_age is not None and max_age <= 0)
        if self.provider() == "cloud":
            return check_cloud_status(key=connect_api_key(), use_cache=use_cache)
        return check_status(self.settings.get("ollama_host"), use_cache=use_cache)

    def invalidate_ai_status(self) -> None:
        """Drop cached provider status (e.g. after the host, key or models change)."""
        invalidate_cloud_status()
        invalidate_ollama_status()

    def ai_model(self) -> str:
        """The model chosen for the active provider."""
        if self.provider() == "cloud":
            return self.settings.get("cloud_model", "")
        return self.settings.get("ollama_model", "")

    def set_ai_model(self, model: str) -> None:
        self.settings.set("cloud_model" if self.provider() == "cloud" else "ollama_model", model)

    # -- ollama (local-only surfaces: install model, environment panel) -----
    def ollama_status(self, force: bool = False):
        return check_status(self.settings.get("ollama_host"), use_cache=not force)

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
