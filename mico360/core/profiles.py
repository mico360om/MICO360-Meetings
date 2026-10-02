"""Company Profiles: branding + document-layout settings used when exporting
meeting minutes (letterhead, logo placement, footer, page numbering).

Profiles are stored as one JSON file each; logos are copied into the profiles
directory so a profile is self-contained and portable. Supports import/export
to JSON, CSV and XLSX.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from ..config import PROFILES_DIR, atomic_write_text

log = logging.getLogger("mico360.profiles")

LOGO_POSITIONS = ["left", "center", "right"]
PAGENUM_POSITIONS = [
    "footer-left", "footer-center", "footer-right",
    "header-left", "header-center", "header-right",
]
ALIGNMENTS = ["left", "center", "right"]
# PDF designs a company can choose (see export/pdf_designs.py for what each is).
PDF_DESIGNS = ["classic", "modern", "formal", "compact"]
DEFAULT_PDF_DESIGN = "classic"
LOGO_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}


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
    pdf_design: str = DEFAULT_PDF_DESIGN   # this company's PDF layout (PDF_DESIGNS)

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
        if not profile_id or not f.exists():
            return None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("profile file is not a JSON object")
            return CompanyProfile.from_dict(data)
        except Exception:
            # an unreadable profile must not take the export (or the app) down
            log.warning("unreadable profile %s", f, exc_info=True)
            return None

    def updated_at(self, profile_id: str) -> float:
        """Last-modified time of a profile (its JSON file's mtime), or 0."""
        f = self.dir / f"{profile_id}.json"
        try:
            return f.stat().st_mtime if f.exists() else 0.0
        except Exception:
            return 0.0

    def save(self, profile: CompanyProfile) -> CompanyProfile:
        # Never persist a value the exporters / editor can't use (an imported
        # "Centre", a design from a newer version, a logo width of 0 ...).
        for f in fields(CompanyProfile):
            if f.name != "id":
                setattr(profile, f.name, _coerce(f.name, getattr(profile, f.name)))
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

    # -- Copy ---------------------------------------------------------------
    def unique_name(self, name: str, exclude_id: str = "") -> str:
        """`name`, or "name (2)", "name (3)" ... if another profile has it."""
        taken = {p.name.strip().lower() for p in self.list() if p.id != exclude_id}
        base = (name or "").strip() or "Company"
        if base.lower() not in taken:
            return base
        n = 2
        while f"{base} ({n})".lower() in taken:
            n += 1
        return f"{base} ({n})"

    def duplicate(self, profile_id: str) -> CompanyProfile | None:
        """A full copy - every setting, the PDF design and its OWN copy of the
        logo file - under a new id and a "(copy)" name."""
        src = self.get(profile_id)
        if not src:
            return None
        taken = {p.name.strip().lower() for p in self.list()}
        name, n = f"{src.name} (copy)", 2
        while name.lower() in taken:                 # "X (copy)", "X (copy 2)" ...
            name, n = f"{src.name} (copy {n})", n + 1
        new = replace(src, id="", logo_path="", name=name)
        if src.logo_path and Path(src.logo_path).is_file():
            try:
                self.set_logo(new, src.logo_path)
            except Exception:
                log.warning("duplicate: could not copy logo %s", src.logo_path, exc_info=True)
        return self.save(new)

    def _content_key(self, p: CompanyProfile) -> str:
        """Identity of a profile's CONTENT (not its id or where its logo lives)."""
        d = p.to_dict()
        d.pop("id", None)
        logo = d.pop("logo_path", "")
        digest = ""
        try:
            if logo and Path(logo).is_file():
                digest = hashlib.sha1(Path(logo).read_bytes()).hexdigest()
        except OSError:
            pass
        return json.dumps(d, sort_keys=True, default=str) + "|" + digest

    # -- Import -------------------------------------------------------------
    def import_file(self, path: str | Path) -> list[CompanyProfile]:
        """Import every profile in the file (always as new profiles)."""
        return self.import_report(path, dedupe=False).imported

    def import_report(self, path: str | Path, dedupe: bool = True) -> "ImportResult":
        """Import profiles from JSON / CSV / XLSX and say exactly what happened.

        Nothing existing is ever overwritten. With `dedupe`, a profile identical
        to one already here is skipped, and one that only shares a NAME is
        imported under "Name (2)". Logos come from embedded data (JSON export),
        or from the logo path - absolute, or relative to the import file (the
        "<file>_logos" folder a CSV/Excel export writes)."""
        path = Path(path)
        ext = path.suffix.lower()
        if ext == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            records = data if isinstance(data, list) else [data]
        elif ext == ".csv":
            with path.open(newline="", encoding="utf-8-sig") as fh:
                records = list(csv.DictReader(fh))
        elif ext in (".xlsx", ".xlsm"):
            records = _read_xlsx(path)
        else:
            raise ValueError(f"Unsupported import format: {ext or '(no extension)'} - "
                             "use a .json, .csv or .xlsx file")

        res = ImportResult()
        existing = {self._content_key(p) for p in self.list()} if dedupe else set()
        for rec in records:
            if not isinstance(rec, dict):
                res.invalid += 1
                continue
            try:                               # one bad record never aborts the rest
                extra = {_norm_header(k): v for k, v in rec.items()
                         if _norm_header(k) in ("logo_data", "logo_name")}
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
                had_logo = bool(src_logo or extra.get("logo_data"))
                try:
                    self._import_logo(prof, src_logo, extra, path)
                except Exception:
                    log.info("imported profile %r: logo %s not usable, skipped",
                             prof.name, src_logo or "(embedded)", exc_info=True)
                if dedupe:
                    key = self._content_key(prof)
                    if key in existing:
                        res.skipped.append(prof.name)
                        self.discard_logo(prof.logo_path)      # the copy we just made
                        continue
                    existing.add(key)
                    new_name = self.unique_name(prof.name)
                    if new_name != prof.name:
                        res.renamed.append((prof.name, new_name))
                        prof.name = new_name
                if had_logo and not prof.logo_path:
                    res.missing_logos.append(prof.name)
                res.imported.append(self.save(prof))
            except Exception:
                log.warning("profile import: record skipped", exc_info=True)
                res.invalid += 1
        log.info("imported %d profile(s) from %s (skipped %d, logos missing %d)",
                 len(res.imported), path.name, len(res.skipped), len(res.missing_logos))
        return res

    def _import_logo(self, prof: CompanyProfile, src_logo: str, extra: dict, file: Path) -> None:
        data = extra.get("logo_data")
        if data:
            raw = base64.b64decode(str(data), validate=False)
            if raw and len(raw) <= 25 * 1024 * 1024:
                suffix = Path(str(extra.get("logo_name") or "logo.png")).suffix.lower()
                suffix = suffix if suffix in LOGO_EXTS else ".png"
                prof.id = prof.id or uuid.uuid4().hex[:10]
                self.logos.mkdir(parents=True, exist_ok=True)
                dest = self.logos / f"{prof.id}-{uuid.uuid4().hex[:8]}{suffix}"
                dest.write_bytes(raw)
                prof.logo_path = str(dest)
                return
        if not src_logo:
            return
        src = Path(src_logo)
        candidates = [src] if src.is_absolute() else []
        candidates += [file.parent / src_logo,
                       file.parent / f"{file.stem}_logos" / src.name,
                       file.parent / src.name]
        for c in candidates:
            if c.is_file() and c.suffix.lower() in LOGO_EXTS:
                self.set_logo(prof, c)
                return

    # -- Export -------------------------------------------------------------
    def export_file(self, profiles: list[CompanyProfile], path: str | Path) -> Path:
        """Export profiles so they can be imported on another PC with nothing
        lost. JSON embeds each logo; CSV / Excel put the logo files in a
        "<file>_logos" folder next to the file and refer to them by relative
        path. The file is written under a temporary name and moved into place."""
        path = Path(path)
        ext = path.suffix.lower()
        if ext not in (".json", ".csv", ".xlsx"):
            raise ValueError(f"Unsupported export format: {ext or '(no extension)'} - "
                             "use .json, .csv or .xlsx")
        rows = [p.to_dict() for p in profiles]
        if ext == ".json":
            for row in rows:
                logo = Path(row.get("logo_path") or "")
                if row.get("logo_path") and logo.is_file():
                    row["logo_name"] = logo.name
                    row["logo_data"] = base64.b64encode(logo.read_bytes()).decode("ascii")
            atomic_write_text(path, json.dumps(rows, indent=2, ensure_ascii=False))
            return path
        logo_dir = path.with_name(f"{path.stem}_logos")
        for row in rows:
            logo = Path(row.get("logo_path") or "")
            if row.get("logo_path") and logo.is_file():
                logo_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(logo, logo_dir / logo.name)
                row["logo_path"] = f"{logo_dir.name}/{logo.name}"
        tmp = path.with_name(f".{path.stem}.{uuid.uuid4().hex[:8]}.tmp{ext}")
        try:
            if ext == ".csv":
                cols = [f.name for f in fields(CompanyProfile)]
                # utf-8-sig: Excel opens it with Arabic / accented names intact
                with tmp.open("w", newline="", encoding="utf-8-sig") as fh:
                    w = csv.DictWriter(fh, fieldnames=cols)
                    w.writeheader()
                    w.writerows(rows)
            else:
                _write_xlsx(rows, tmp)
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return path


@dataclass
class ImportResult:
    """What an import did (shown to the user)."""
    imported: list = field(default_factory=list)        # CompanyProfile
    skipped: list = field(default_factory=list)         # names identical to an existing profile
    renamed: list = field(default_factory=list)         # (old name, new name)
    missing_logos: list = field(default_factory=list)   # names whose logo file wasn't found
    invalid: int = 0                                    # records that weren't profiles

    def summary(self) -> str:
        parts = [f"Imported {len(self.imported)} profile(s)"]
        if self.skipped:
            more = "..." if len(self.skipped) > 3 else ""
            parts.append(f"skipped {len(self.skipped)} already here "
                         f"({', '.join(self.skipped[:3])}{more})")
        if self.renamed:
            parts.append("renamed " + ", ".join(f'"{a}" to "{b}"' for a, b in self.renamed[:3]))
        if self.missing_logos:
            more = "..." if len(self.missing_logos) > 3 else ""
            parts.append(f"logo not found for {', '.join(self.missing_logos[:3])}{more} "
                         "(add it with Edit)")
        if self.invalid:
            parts.append(f"{self.invalid} unreadable record(s) ignored")
        return "; ".join(parts) + "."


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
    "design": "pdf_design", "pdf_layout": "pdf_design", "pdf_template": "pdf_design",
    "layout": "pdf_design", "template": "pdf_design",
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


_POSITION_ALIASES = {"centre": "center", "middle": "center", "centered": "center",
                     "centred": "center"}


def _choice(value, allowed: list, default: str) -> str:
    """A case/spelling-tolerant pick from a fixed list ("Centre" -> "center")."""
    s = re.sub(r"[\s_]+", "-", str(value or "").strip().lower())
    s = "-".join(_POSITION_ALIASES.get(part, part) for part in s.split("-"))
    s = s.replace("top-", "header-").replace("bottom-", "footer-")
    return s if s in allowed else default


def _coerce(key: str, value):
    if key in ("logo_position", "footer_alignment"):
        return _choice(value, ALIGNMENTS, "left" if key == "logo_position" else "center")
    if key == "page_number_position":
        return _choice(value, PAGENUM_POSITIONS, "footer-right")
    if key == "pdf_design":
        return _choice(value, PDF_DESIGNS, DEFAULT_PDF_DESIGN)
    if key == "page_number_format":
        s = "" if value is None else str(value).strip()
        return s or "Page {n} of {total}"
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
            v = float(value)
        except (TypeError, ValueError):
            return 35.0
        return min(max(v, 10.0), 90.0) if v == v else 35.0     # (v != v: NaN)
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
