"""source.yaml: what each PDF in a source folder is, and which topic it covers.

data/sources/<subject>/<level>/<slug>/source.yaml (INGESTION_SPEC.md section 3).
Errors stop the run; warnings are printed and shown on the review page.
"""

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MATERIAL_TYPES = {
    "questions", "answer_key", "exam_paper_with_key", "revision_tables",
    "teaching_text", "school_notes", "worksheets",
}
STAGE_A_TYPES = {"questions", "answer_key", "exam_paper_with_key"}
RELIABILITY = {"assessment_book", "publisher_paper", "school"}
KNOWN_KEYS = {"title", "publisher", "subject", "level", "files", "key_reliability", "units"}
LEVEL_PATTERN = re.compile(r"^sec[1-4]$")
# qns_c01.pdf, qns_u01.pdf, rev_u01.pdf, qns_c05a.pdf -> "01" / "05"
UNIT_PATTERN = re.compile(r"_[a-z]*(\d{2})[a-z]?\.pdf$", re.IGNORECASE)


class SourceError(ValueError):
    def __init__(self, path, problems):
        self.problems = problems
        super().__init__(f"{path}: " + "; ".join(problems))


@dataclass
class SourceConfig:
    folder: Path
    slug: str
    title: str
    publisher: str
    subject: str
    level: str
    files: list  # [(glob pattern, material type)], first match wins
    key_reliability: str
    units: dict  # "01" -> topic id
    warnings: list = field(default_factory=list)


def _unit_key(key) -> str:
    text = str(key).strip()
    return f"{int(text):02d}" if text.isdigit() else text


def load_source(folder) -> SourceConfig:
    folder = Path(folder)
    path = folder / "source.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SourceError(path, ["source.yaml not found"]) from None
    except yaml.YAMLError as e:
        raise SourceError(path, [f"not valid YAML: {e}"]) from None
    if not isinstance(data, dict):
        raise SourceError(path, ["must be a mapping of keys to values"])

    errors, warnings = [], []
    for key in data:
        if key not in KNOWN_KEYS:
            warnings.append(f"unknown key '{key}' ignored")

    subject = str(data.get("subject") or "").strip()
    level = str(data.get("level") or "").strip()
    if not subject:
        errors.append("'subject' is missing")
    if not level:
        errors.append("'level' is missing")
    elif not LEVEL_PATTERN.match(level):
        errors.append(f"'level' must be sec1 to sec4, not '{level}'")

    files = data.get("files")
    patterns = []
    if not files:
        errors.append("'files' is missing")
    elif not isinstance(files, dict):
        errors.append("'files' must map file patterns to material types")
    else:
        for pattern, mtype in files.items():
            if mtype not in MATERIAL_TYPES:
                errors.append(f"'{pattern}': unknown material type '{mtype}'")
            else:
                patterns.append((str(pattern), mtype))

    reliability = data.get("key_reliability")
    if reliability is None:
        reliability = "publisher_paper"
        warnings.append("'key_reliability' missing: using publisher_paper")
    elif reliability not in RELIABILITY:
        errors.append(f"'key_reliability' must be one of {sorted(RELIABILITY)}")

    units = {}
    raw_units = data.get("units") or {}
    if not isinstance(raw_units, dict):
        errors.append("'units' must map file numbers to topic ids")
    else:
        units = {_unit_key(k): str(v) for k, v in raw_units.items()}

    if errors:
        raise SourceError(path, errors)
    title = str(data.get("title") or "").strip()
    if not title:
        title = folder.name
        warnings.append("'title' missing: using the folder name")
    return SourceConfig(
        folder=folder, slug=folder.name, title=title, publisher=str(data.get("publisher") or ""),
        subject=subject, level=level, files=patterns, key_reliability=reliability,
        units=units, warnings=warnings,
    )


def material_type(cfg: SourceConfig, filename: str):
    for pattern, mtype in cfg.files:
        if fnmatch.fnmatch(filename, pattern):
            return mtype
    return None


def unit_of(filename: str):
    m = UNIT_PATTERN.search(filename)
    return m.group(1) if m else None


def topic_for_file(cfg: SourceConfig, filename: str):
    """(topic id or None, warning or None). None topic means the model or reviewer decides."""
    unit = unit_of(filename)
    if unit is None:
        return None, None
    topic = cfg.units.get(unit)
    if topic is None:
        return "unassigned", f"{filename}: no 'units' entry for {unit}; its items are marked topic 'unassigned'"
    return topic, None


def check_unit_topics(cfg: SourceConfig, known_topics: set) -> list[str]:
    """Warnings for units pointing at topics that aren't in the topic tree (typos)."""
    return [f"unit {u}: topic '{t}' is not in topics.yaml" for u, t in sorted(cfg.units.items())
            if t not in known_topics]
