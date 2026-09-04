import os
from typing import Dict, Any
from sqlalchemy.orm import Session
from backend.database.models import ChallengeModel, TargetProfileModel, EvidenceModel

class ReportGenerator:
    """Generates professional Markdown challenge writeups and README reports."""

    def generate_readme(
        self,
        db: Session,
        challenge_id: str,
        output_dir: str = "reports"
    ) -> str:
        challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
        if not challenge:
            return ""

        target = db.query(TargetProfileModel).filter(TargetProfileModel.challenge_id == challenge_id).first()
        evidence_list = db.query(EvidenceModel).filter(EvidenceModel.challenge_id == challenge_id).all()

        target_str = target.current_address if target else "Unknown Target"

        platform = getattr(challenge, "platform_name", "") or "FORGE CTF Framework"
        markdown_content = f"""# CTF Challenge Writeup — {challenge.name}

## Challenge Overview
- **Platform / Competition**: {platform}
- **Category**: {challenge.category.upper()}
- **Target**: `{target_str}`
- **Status**: `{challenge.status}`
- **Captured Flag**: `{challenge.flag or "Pending / In Progress"}`

## Initial Reconnaissance & Investigation Strategy
The FORGE Autonomous Framework initiated automated target profiling and capability-driven analysis on `{target_str}`.

## Evidence & Key Findings
"""
        if not evidence_list:
            markdown_content += "\n*No evidence collected yet.*\n"
        else:
            for ev in evidence_list:
                markdown_content += f"""
### Finding [{ev.agent.upper()}] — {ev.evidence_type}
- **Source**: `{ev.source}`
- **Confidence**: `{ev.confidence * 100}%`
```text
{ev.content}
```
"""

        markdown_content += """
## Verification & Lessons Learned
- Attack paths validated via controlled tool executions.
- Findings recorded in FORGE state vector database.
"""

        os.makedirs(output_dir, exist_ok=True)
        report_path = os.path.join(output_dir, f"README_{challenge_id}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return report_path

report_generator = ReportGenerator()
