import json
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from backend.agents.manager import agent_manager
from backend.providers.router import model_router
from backend.tools.manager import tool_manager
from backend.privilege.manager import privilege_manager
from backend.checkpoints.manager import checkpoint_manager
from backend.database.models import RunModel, ChallengeModel, TargetProfileModel, EvidenceModel, FindingModel
from backend.api.runner import workflow_runner

logger = logging.getLogger("forge.orchestrator")

class AutonomousOrchestrator:
    """Central autonomous investigation loop coordinating agents, tools, evidence, and checkpoints."""

    async def execute_run_step(self, db: Session, run_id: str) -> Dict[str, Any]:
        # 1. Check Kill Switch
        if workflow_runner.is_cancelled(run_id):
            logger.warning(f"Run {run_id} halted by Kill Switch.")
            return {"status": "CANCELLED", "reason": "Kill switch activated"}

        # 2. Load active run & target profile
        run = db.query(RunModel).filter(RunModel.id == run_id).first()
        if not run or run.status in ["PAUSED", "COMPLETED", "CANCELLED"]:
            return {"status": run.status if run else "NOT_FOUND"}

        target = db.query(TargetProfileModel).filter(TargetProfileModel.challenge_id == run.challenge_id).first()
        target_addr = target.current_address if target else "127.0.0.1"

        # 3. Select agent for current phase
        current_agent_name = run.current_agent or "recon"
        agent = agent_manager.get_agent(current_agent_name)

        # 4. Agent plans step & requests capability
        context = {
            "target": target_addr,
            "phase": run.current_phase,
            "run_id": run_id
        }
        agent_msg = await agent.plan_next_step(context)
        capability = agent_msg.capability or "recon"

        # 5. LLM reasoning check via ModelRouter
        prompt = f"Analyze target {target_addr} for phase {run.current_phase}. Requested capability: {capability}"
        llm_response = await model_router.route_request(
            prompt=prompt,
            capability=capability,
            system_instruction=f"You are the FORGE {current_agent_name.upper()} agent."
        )

        if llm_response.is_refusal:
            logger.warning(f"LLM refusal during run {run_id}: {llm_response.refusal_reason}")
            run.status = "WAITING_FOR_USER"
            db.commit()
            return {"status": "WAITING_FOR_USER", "reason": llm_response.refusal_reason}

        # 6. Resolve tool & verify privilege
        tool_res = await tool_manager.execute_capability(capability=capability, target=target_addr)

        # 7. Store evidence if tool produced stdout output
        if tool_res.stdout:
            evidence = EvidenceModel(
                challenge_id=run.challenge_id,
                agent=current_agent_name,
                evidence_type="command_output",
                source=tool_res.tool_name,
                content=tool_res.stdout[:4000],
                confidence=0.9
            )
            db.add(evidence)
            db.commit()

        # 8. Create state machine checkpoint
        checkpoint_manager.create_checkpoint(
            db=db,
            run_id=run_id,
            current_phase=run.current_phase,
            current_agent=current_agent_name,
            last_action=f"executed_{tool_res.tool_name}",
            state_snapshot={"target": target_addr, "tool_status": tool_res.status}
        )

        # Advance state machine
        if run.current_phase == "recon":
            run.current_phase = "web"
            run.current_agent = "web"
        elif run.current_phase == "web":
            run.current_phase = "completed"
            run.status = "COMPLETED"

        db.commit()
        return {
            "status": run.status,
            "agent": current_agent_name,
            "capability": capability,
            "tool_status": tool_res.status,
            "tool_name": tool_res.tool_name
        }

orchestrator_loop = AutonomousOrchestrator()
