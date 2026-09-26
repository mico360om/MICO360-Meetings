"""Meeting-type presets — bundle a prompt + output style + company profile so a
whole meeting setup is one click. Stored as a single JSON file; references are
by name so they survive prompt/profile edits."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ..config import DATA_DIR, atomic_write_text, quarantine_bad_file

log = logging.getLogger("mico360.meeting_types")

STORE_FILE = DATA_DIR / "meeting_types.json"


@dataclass
class MeetingType:
    name: str
    prompt_name: str = ""       # matches a prompt in the library (by name)
    style: str = ""             # one of OUTPUT_STYLES
    profile_name: str = ""      # matches a company profile (by name), optional

    def to_dict(self):
        return asdict(self)


# Seeded on first run; each references a built-in prompt/style.
BUILTIN_TYPES = [
    MeetingType("Formal / Board", "Board Meeting Minutes", "Formal Minutes"),
    MeetingType("Project / Standup", "Daily Standup Bullets", "Short Summary"),
    MeetingType("Client Call", "Client Meeting Recap", "Formal Minutes"),
    MeetingType("Internal Team", "Internal Team Meeting Notes", "Detailed Minutes"),
    MeetingType("Action Items Only", "Action Item Report", "Action Item Report"),
    MeetingType("Executive Summary", "Executive Summary", "Executive Summary"),
]


class MeetingTypeStore:
    def __init__(self, store_file: Path = STORE_FILE):
        self.file = store_file
        self._items = self._load()
        if not self._items:
            self._items = [t for t in BUILTIN_TYPES]
            self._write()

    def _load(self) -> list[MeetingType]:
        """Load the saved types. One malformed entry is skipped (not the whole
        file); a file that can't be read at all is kept aside as *.bad so
        re-seeding the built-ins can't overwrite the user's custom types."""
        if not self.file.exists():
            return []
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("meeting types file is not a JSON list")
        except Exception:
            log.warning("could not load meeting types", exc_info=True)
            quarantine_bad_file(self.file)
            return []
        valid = {f.name for f in fields(MeetingType)}
        out: list[MeetingType] = []
        skipped = 0
        for d in data:
            try:
                if not isinstance(d, dict):
                    raise TypeError(f"entry is {type(d).__name__}, not an object")
                kw = {k: ("" if v is None else str(v)) for k, v in d.items() if k in valid}
                if not kw.get("name", "").strip():
                    raise ValueError("entry has no name")
                out.append(MeetingType(**kw))
            except Exception as exc:
                skipped += 1
                log.warning("skipping bad meeting type entry %r: %s", d, exc)
        if data and not out:
            # nothing usable at all — keep the original before it's re-seeded
            quarantine_bad_file(self.file)
        elif skipped:
            log.warning("skipped %d malformed meeting type(s)", skipped)
            try:                        # keep a copy: the next save drops them
                import shutil
                shutil.copy2(self.file, self.file.with_name(self.file.name + ".bad"))
            except Exception:
                pass
        return out

    def _write(self):
        try:
            atomic_write_text(self.file,
                              json.dumps([t.to_dict() for t in self._items], indent=2))
        except Exception:
            log.warning("could not save meeting types", exc_info=True)

    def list(self) -> list[MeetingType]:
        return list(self._items)

    def get(self, name: str) -> MeetingType | None:
        return next((t for t in self._items if t.name == name), None)

    def save(self, mt: MeetingType):
        existing = self.get(mt.name)
        if existing:
            self._items[self._items.index(existing)] = mt
        else:
            self._items.append(mt)
        self._write()

    def delete(self, name: str) -> bool:
        mt = self.get(name)
        if mt:
            self._items.remove(mt)
            self._write()
            return True
        return False
