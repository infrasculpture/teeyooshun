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
NOTES_TYPES = {"school_notes", "teaching_text"}  # Stage C: derived revision cards and reference passages
HANDLED_TYPES = STAGE_A_TYPES | {"revision_tables"} | NOTES_TYPES  # everything the ingest command processes
RELIABILITY = {"assessment_book", "publisher_paper", "school"}
KNOWN_KEYS = {"title", "publisher", "subject", "level", "files", "key_reliability", "units",
              "numbering_overrides", "file_options"}
LAYOUTS = {"a4", "slides_4up"}
SLIDE_ORDERS = {"down_then_across", "across_then_down"}
OPTION_KEYS = {"layout", "slide_order", "topic"}
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
    # file name -> {question number in the file: number in the answer key}, as written in source.yaml
    # e.g. {"2024_jwss_eoy.pdf": {"C12": "C1"}}. Takes priority over automatic alignment.
    numbering_overrides: dict = field(default_factory=dict)
    # file name or glob -> {"layout", "slide_order", "topic"} as written in source.yaml (INGESTION_STAGE_C_SPEC section 6)
    file_options: dict = field(default_factory=dict)

    @property
    def render_key(self) -> str:
        """Where this source's page images live under data/renders/. Includes the subject and level,
        because two sources can share a folder name and file names (e.g. two "revision-guide" folders)."""
        return f"{self.subject}/{self.level}/{self.slug}"


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

    overrides = {}
    raw_overrides = data.get("numbering_overrides") or {}
    if not isinstance(raw_overrides, dict) or not all(isinstance(v, dict) for v in raw_overrides.values()):
        errors.append("'numbering_overrides' must map file names to {question number: answer-key number}")
    else:
        overrides = {str(name): {str(q): str(k) for q, k in pairs.items()} for name, pairs in raw_overrides.items()}

    file_options = {}
    raw_options = data.get("file_options") or {}
    if not isinstance(raw_options, dict) or not all(isinstance(v, dict) for v in raw_options.values()):
        errors.append("'file_options' must map file names (or patterns) to options")
    else:
        for name, options in raw_options.items():
            problems = _option_problems(str(name), options)
            errors += problems
            if not problems:
                file_options[str(name)] = {k: str(v) for k, v in options.items() if v is not None}

    if errors:
        raise SourceError(path, errors)
    title = str(data.get("title") or "").strip()
    if not title:
        title = folder.name
        warnings.append("'title' missing: using the folder name")
    return SourceConfig(
        folder=folder, slug=folder.name, title=title, publisher=str(data.get("publisher") or ""),
        subject=subject, level=level, files=patterns, key_reliability=reliability,
        units=units, warnings=warnings, numbering_overrides=overrides, file_options=file_options,
    )


def _option_problems(name: str, options: dict) -> list[str]:
    problems = [f"file_options '{name}': unknown option '{k}'" for k in options if k not in OPTION_KEYS]
    layout, order = options.get("layout", "a4"), options.get("slide_order")
    if layout not in LAYOUTS:
        problems.append(f"file_options '{name}': 'layout' must be one of {sorted(LAYOUTS)}")
    elif layout == "slides_4up" and order is None:
        problems.append(f"file_options '{name}': slides_4up needs 'slide_order' ({' or '.join(sorted(SLIDE_ORDERS))})")
    if order is not None and order not in SLIDE_ORDERS:
        problems.append(f"file_options '{name}': 'slide_order' must be one of {sorted(SLIDE_ORDERS)}")
    return problems


def file_options_for(cfg: SourceConfig, filename: str) -> dict:
    """{"layout", "slide_order", "topic"} for a file: an entry named exactly beats a pattern;
    otherwise the first matching pattern (in source.yaml order) is used. slide_order is "" for a4."""
    options = cfg.file_options.get(filename)
    if options is None:
        options = next((o for pattern, o in cfg.file_options.items() if fnmatch.fnmatch(filename, pattern)), {})
    layout = options.get("layout", "a4")
    return {"layout": layout, "slide_order": options.get("slide_order", "") if layout == "slides_4up" else "",
            "topic": options.get("topic")}


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
    set_topic = file_options_for(cfg, filename)["topic"]
    if set_topic:
        return set_topic, None
    unit = unit_of(filename)
    if unit is None:
        return None, None
    topic = cfg.units.get(unit)
    if topic is None:
        return "unassigned", f"{filename}: no 'units' entry for {unit}; its items are marked topic 'unassigned'"
    return topic, None


def check_unit_topics(cfg: SourceConfig, known_topics: set) -> list[str]:
    """Warnings for units pointing at topics that aren't in the topic tree (typos)."""
    return ([f"unit {u}: topic '{t}' is not in topics.yaml" for u, t in sorted(cfg.units.items())
             if t not in known_topics]
            + [f"file_options '{name}': topic '{o['topic']}' is not in topics.yaml"
               for name, o in sorted(cfg.file_options.items()) if o.get("topic") and o["topic"] not in known_topics])
