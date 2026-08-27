from hashlib import sha256
from pathlib import Path


ROOT = Path(
    r"D:\cvnotenough\data\experiments\a1_oracle\paper\OR_submission_expanded"
    r"\submission_20260820"
)
NAMES = [
    "01_main_manuscript_anonymous.docx",
    "02_electronic_companion_anonymous.docx",
    "03_cover_letter.docx",
    "04_replication_archive_anonymous.zip",
    "05_submission_metadata_private.docx",
    "06_independent_OR_package_audit.md",
    "07_path_and_unsat_remediation.md",
]


lines = []
for name in NAMES:
    path = ROOT / name
    digest = sha256(path.read_bytes()).hexdigest().upper()
    lines.append(f"{digest} *{name}")

(ROOT / "FINAL_SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
