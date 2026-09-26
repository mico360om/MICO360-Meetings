"""Company Profiles: branding + document-layout settings used when exporting
meeting minutes (letterhead, logo placement, footer, page numbering).

Profiles are stored as one JSON file each; logos are copied into the profiles
directory so a profile is self-contained and portable. Supports import/export
to JSON, CSV and XLSX.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from ..config import PROFILES_DIR, atomic_write_text

log = logging.getLogger("mico360.profiles")

LOGO_POSITIONS = ["left", "center", "right"]
PAGENUM_POSITIONS = [
    "footer-left", "footer-center", "footer-right",
    "header-left", "header-center", "header-right",
]
ALIGNMENTS = ["left", "center", "right"]


@dataclass
class CompanyProfile:
    id: str = ""
    name: str = "New Company"
    address: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    logo_path: str = ""              # absolute path to copied logo
    logo_position: str = "left"      # left | center | right
    logo_width_mm: float = 35.0      # printed width; height auto-scales (quality kept)
    footer_text: str = ""
    footer_alignment: str = "center"
    show_page_numbers: bool = True
    page_number_position: str = "footer-right"
    page_number_format: str = "Page {n} of {total}"
    accent_color: str = "#8B1E1E"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CompanyProfile":
        valid = {f.name for f in fields(cls)}
        # coerce so a number stored in JSON (e.g. phone) can't crash exporters
        return cls(**{k: _coerce(k, v) for k, v in d.items() if k in valid})


class ProfileStore:
    def __init__(self, directory: Path = PROFILES_DIR):
        self.dir = directory
        self.logos = directory / "logos"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.logos.mkdir(parents=True, exist_ok=True)

    # -- CRUD ---------------------------------------------------------------
    def list(self) -> list[CompanyProfile]:
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                out.append(CompanyProfile.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except Exception:
                log.warning("bad profile %s", f, exc_info=True)
        out.sort(key=lambda p: p.name.lower())
        return out

    def get(self, profile_id: str) -> CompanyProfile | None:
        f = self.dir / f"{profile_id}.json"
        if f.exists():
            return CompanyProfile.from_dict(json.loads(f.read_text(encoding="utf-8")))
        return None

    def updated_at(self, profile_id: str) -> float:
        """Last-modified time of a profile (its JSON file's mtime), or 0."""
        f = self.dir / f"{profile_id}.json"
        try:
            return f.stat().st_mtime if f.exists() else 0.0
        except Exception:
            return 0.0

    def save(self, profile: CompanyProfile) -> CompanyProfile:
        if not (profile.name or "").strip():
            profile.name = "Company"            # never persist a blank company name
        if not profile.id:
            profile.id = uuid.uuid4().hex[:10]
        atomic_write_text(self.dir / f"{profile.id}.json",
                          json.dumps(profile.to_dict(), indent=2))
        return profile

    # -- logos --------------------------------------------------------------
    def _in_store(self, logo_path: str) -> bool:
        try:
            return bool(logo_path) and Path(logo_path).resolve().parent == self.logos.resolve()
        except Exception:
            return False

    def _logo_in_use(self, logo_path: str, exclude_id: str = "") -> bool:
        """True if any profile other than `exclude_id` points at this logo file."""
        try:
            target = Path(logo_path).resolve()
        except Exception:
            return False
        for p in self.list():
            if p.id == exclude_id or not p.logo_path:
                continue
            try:
                if Path(p.logo_path).resolve() == target:
                    return True
            except Exception:
                continue
        return False

    def discard_logo(self, logo_path: str, exclude_id: str = "") -> bool:
        """Delete a logo file we own (inside the store's logos folder) once no
        other profile references it. Never touches files outside the store."""
        if not self._in_store(logo_path) or self._logo_in_use(logo_path, exclude_id):
            return False
        try:
            Path(logo_path).unlink(missing_ok=True)
            return True
        except Exception:
            log.warning("could not delete logo %s", logo_path, exc_info=True)
            return False

    def delete(self, profile_id: str) -> bool:
        f = self.dir / f"{profile_id}.json"
        if f.exists():
            try:
                p = self.get(profile_id)
            except Exception:
                p = None
            f.unlink()
            # only this profile's OWN logo file, and only if no other profile
            # (e.g. an imported copy) still uses it
            if p and p.logo_path:
                self.discard_logo(p.logo_path, exclude_id=profile_id)
            return True
        return False

    def set_logo(self, profile: CompanyProfile, source_image: str | Path) -> CompanyProfile:
        """Copy a logo into the profile store (kept full-resolution for quality).

        The copy gets a NEW unique file name, so it never overwrites the logo a
        saved profile (or another profile) currently uses; the caller discards
        the old file with discard_logo() once the change is saved."""
        src = Path(source_image)
        if not src.is_file():
            raise FileNotFoundError(src)
        pid = profile.id or uuid.uuid4().hex[:10]
        profile.id = pid
        self.logos.mkdir(parents=True, exist_ok=True)
        dest = self.logos / f"{pid}-{uuid.uuid4().hex[:8]}{src.suffix.lower()}"
        shutil.copy2(src, dest)
        profile.logo_path = str(dest)
        return profile

    # -- Import -------------------------------------------------------------
    def import_file(self, path: str | Path) -> list[CompanyProfile]:
        path = Path(path)
        ext = path.suffix.lower()
        if ext == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            records = data if isinstance(data, list) else [data]
        elif ext == ".csv":
            with path.open(newline="", encoding="utf-8-sig") as fh:
                records = list(csv.DictReader(fh))
        elif ext in (".xlsx", ".xlsm"):
            records = _read_xlsx(path)
        else:
            raise ValueError(f"Unsupported import format: {ext}")

        imported: list[CompanyProfile] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            rec = _normalize_record(rec)
            if not rec:                         # blank / formatted-empty row
                continue
            rec = {k: _coerce(k, v) for k, v in rec.items()}
            prof = CompanyProfile.from_dict(rec)
            prof.id = ""  # always assign a fresh id on import
            # never share another profile's logo FILE: give the import its own
            # copy (deleting either profile then can't break the other)
            src_logo = prof.logo_path
            prof.logo_path = ""
            if src_logo:
                try:
                    self.set_logo(prof, src_logo)
                except Exception:
                    log.info("imported profile %r: logo %s not found, skipped",
                             prof.name, src_logo)
            imported.append(self.save(prof))
        log.info("imported %d profile(s) from %s", len(imported), path.name)
        return imported

    # -- Export -------------------------------------------------------------
    def export_file(self, profiles: list[CompanyProfile], path: str | Path) -> Path:
        path = Path(path)
        ext = path.suffix.lower()
        rows = [p.to_dict() for p in profiles]
        if ext == ".json":
            path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        elif ext == ".csv":
            cols = [f.name for f in fields(CompanyProfile)]
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                w.writerows(rows)
        elif ext == ".xlsx":
            _write_xlsx(rows, path)
        else:
            raise ValueError(f"Unsupported export format: {ext}")
        return path


# -- helpers ----------------------------------------------------------------
_BOOL_FIELDS = {"show_page_numbers"}
_FLOAT_FIELDS = {"logo_width_mm"}


_FIELD_NAMES = {f.name for f in fields(CompanyProfile)}
# Friendly spreadsheet headers → field names (after lower-casing and turning
# spaces/hyphens into underscores).
_HEADER_ALIASES = {
    "company": "name", "company_name": "name", "organisation": "name",
    "organization": "name", "footer": "footer_text", "logo": "logo_path",
    "logo_file": "logo_path", "accent": "accent_color", "accent_colour": "accent_color",
    "colour": "accent_color", "color": "accent_color", "telephone": "phone",
    "tel": "phone", "e_mail": "email", "web": "website", "url": "website",
    "page_numbers": "show_page_numbers", "logo_width": "logo_width_mm",
}


def _norm_header(h) -> str:
    k = re.sub(r"[\s\-]+", "_", str(h or "").strip().lower())
    return _HEADER_ALIASES.get(k, k)


def _normalize_record(rec: dict) -> dict:
    """Case/space-insensitive headers ("Company Name", " Phone ") and drop the
    record entirely when every known field is blank (empty xlsx rows)."""
    out: dict = {}
    for k, v in rec.items():
        nk = _norm_header(k)
        if nk in _FIELD_NAMES and (nk not in out or out[nk] in (None, "")):
            out[nk] = v
    meaningful = {k: v for k, v in out.items()
                  if k != "id" and v is not None and str(v).strip() != ""}
    return out if meaningful else {}


_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{3,8}$")


def _coerce(key: str, value):
    if key == "accent_color":
        # an imported colour ends up inside HTML/CSS — only accept #hex
        s = str(value or "").strip()
        return s if _COLOR_RE.match(s) else "#8B1E1E"
    if key in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        s = "" if value is None else str(value).strip().lower()
        if not s:
            return True                        # blank cell → the default (on)
        return s in ("1", "true", "yes", "y", "on")
    if key in _FLOAT_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 35.0
    if value is None:
        return ""
    # Every other field is text. Excel/JSON can hand back numbers (a phone stored
    # as a number) — keeping an int crashed every export that used the profile.
    if isinstance(value, float) and value.is_integer():
        value = int(value)                     # 96824123456.0 -> "96824123456"
    return str(value).strip()


def _read_xlsx(path: Path) -> list[dict]:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()        # read_only workbooks hold the file open until closed
    if not rows:
        return []
    headers = [str(h) if h is not None else "" for h in rows[0]]
    out = []
    for r in rows[1:]:
        out.append({headers[i]: r[i] for i in range(len(headers)) if i < len(r)})
    return out


def _write_xlsx(rows: list[dict], path: Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Company Profiles"
    cols = [f.name for f in fields(CompanyProfile)]
    ws.append(cols)
    for r in rows:
        ws.append([r.get(c, "") for c in cols])
    wb.save(path)
