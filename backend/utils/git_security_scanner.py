import os
import re
import subprocess
import sys

# Regex patterns for common secret leaks
SECRET_PATTERNS = [
    (r'(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token|bearer[_-]?token)\s*[:=]\s*["\'][A-Za-z0-9_\-]{16,}["\']', "Hardcoded API Key / Token"),
    (r'-----BEGIN (RSA|EC|OPENSSH|PRIVATE) KEY-----', "Private Key Leak"),
    (r'(?i)AIzaSy[A-Za-z0-9_\-]{33}', "Google API Key"),
    (r'(?i)sk-[A-Za-z0-9]{32,}', "OpenAI/Anthropic API Key"),
    (r'(?i)ghp_[A-Za-z0-9]{36}', "GitHub Personal Access Token")
]

def scan_modified_files() -> int:
    """Scans uncommitted git changes for security leaks and returns modified file count."""
    try:
        status_out = subprocess.check_output(["git", "status", "--porcelain"], text=True)
    except Exception as e:
        print(f"[SECURITY AUDIT] Error running git status: {e}")
        return 0

    lines = [line.strip() for line in status_out.splitlines() if line.strip()]
    modified_files = []

    for line in lines:
        status_code = line[:2]
        filepath = line[3:].strip()
        # Exclude ignored/untracked files like scratch or db
        if not (filepath.endswith(".db") or filepath.startswith("scratch/") or filepath.startswith(".agents/")):
            modified_files.append(filepath)

    print(f"[SECURITY AUDIT] Detected {len(modified_files)} modified file(s) in working tree.")

    # Perform secret scan across modified text files
    found_issues = 0
    for filepath in modified_files:
        if not os.path.isfile(filepath):
            continue
        # Skip binary files
        if filepath.endswith((".zip", ".png", ".jpg", ".ico", ".db")):
            continue

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            for pattern, desc in SECRET_PATTERNS:
                matches = re.findall(pattern, content)
                if matches:
                    print(f"[CRITICAL SECURITY WARNING] Potential secret leak in {filepath}: {desc}")
                    found_issues += 1
        except Exception as e:
            print(f"[SECURITY AUDIT] Could not scan {filepath}: {e}")

    if found_issues > 0:
        print(f"[SECURITY AUDIT FAILED] Found {found_issues} potential security leak(s). Aborting automated publish!")
        sys.exit(1)

    print("[SECURITY AUDIT PASSED] Zero hardcoded secrets or security risks detected.")
    return len(modified_files)

if __name__ == "__main__":
    count = scan_modified_files()
    if count >= 7:
        print(f"[AUTO PUBLISH TRIGGERED] Change count ({count}) >= 7. Ready for secure GitHub publish!")
    else:
        print(f"[AUTO PUBLISH STANDBY] Change count ({count}) < 7 threshold.")
