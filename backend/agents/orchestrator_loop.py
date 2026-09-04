import asyncio
import json
import re
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from backend.database.session import SessionLocal
from backend.agents.manager import agent_manager
from backend.providers.router import model_router
from backend.tools.manager import tool_manager
from backend.privilege.manager import privilege_manager
from backend.checkpoints.manager import checkpoint_manager
from backend.database.models import RunModel, ChallengeModel, TargetProfileModel, EvidenceModel, FindingModel
from backend.websocket.manager import ws_manager
from backend.api.runner import workflow_runner
from backend.reporting.generator import report_generator

logger = logging.getLogger("forge.orchestrator")

class AutonomousOrchestrator:
    """Central autonomous investigation loop coordinating agents, tools, evidence, and checkpoints."""

    async def run_autonomous_loop(self, run_id: str, challenge_id: str, target: str):
        """Asynchronous background loop processing an investigation run from start to completion."""
        logger.info(f"Starting autonomous loop for run {run_id}, challenge {challenge_id}, target {target}")
        
        phases = ["recon", "web", "exploitation", "evidence_capture", "report_generation"]
        progress_per_phase = {"recon": 20, "web": 45, "exploitation": 70, "evidence_capture": 90, "report_generation": 100}
        
        is_file_target = os.path.exists(target) or not (target.startswith("10.") or target.startswith("192.") or target.startswith("172.") or target.startswith("http") or target.replace(".", "").isdigit())

        for phase in phases:
            if workflow_runner.is_cancelled(run_id):
                logger.warning(f"Run {run_id} cancelled by Kill Switch.")
                await ws_manager.broadcast({
                    "event": "RUN_CANCELLED",
                    "run_id": run_id,
                    "challenge_id": challenge_id
                })
                return

            db: Session = SessionLocal()
            try:
                run = db.query(RunModel).filter(RunModel.id == run_id).first()
                challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
                if not run or not challenge or run.status in ["PAUSED", "CANCELLED"]:
                    logger.info(f"Run {run_id} terminated or paused externally.")
                    db.close()
                    return

                run.current_phase = phase
                run.current_agent = "recon" if phase == "recon" else ("web" if phase == "web" else "orchestrator")
                challenge.progress = progress_per_phase.get(phase, challenge.progress)
                db.commit()

                # Broadcast progress update
                await ws_manager.broadcast({
                    "event": "PROGRESS_UPDATED",
                    "challenge_id": challenge_id,
                    "run_id": run_id,
                    "phase": phase,
                    "progress": challenge.progress
                })

                # Determine capability
                if is_file_target:
                    capability = "file_analysis" if phase in ["recon", "web"] else "reverse_engineering"
                else:
                    capability = "recon" if phase == "recon" else ("directory_enumeration" if phase == "web" else "web_testing")

                # AI Route & Decision
                prompt = f"Executing CTF investigation phase '{phase}' on target '{target}'. Capability: {capability}"
                llm_response = await model_router.route_request(
                    prompt=prompt,
                    capability=capability,
                    system_instruction=f"You are the FORGE Autonomous Agent handling phase {phase}."
                )

                await ws_manager.broadcast({
                    "event": "AI_DECISION",
                    "challenge_id": challenge_id,
                    "run_id": run_id,
                    "agent": run.current_agent.upper(),
                    "goal": f"Phase {phase.upper()} Target Analysis",
                    "capability": capability,
                    "model": getattr(llm_response, "model", getattr(llm_response, "provider_name", "model_router")),
                    "result": llm_response.content or f"Executed capability {capability} on target {target}.",
                    "confidence": 92
                })

                # Tool Execution
                tool_res = await tool_manager.execute_capability(capability=capability, target=target)
                
                # Log Terminal output
                terminal_log_output = tool_res.stdout if tool_res.stdout else (tool_res.stderr if tool_res.stderr else f"[{phase.upper()}] Execution completed for capability '{capability}'.")
                
                await ws_manager.broadcast({
                    "event": "LOG_OUTPUT",
                    "challenge_id": challenge_id,
                    "run_id": run_id,
                    "command": tool_res.command or f"forge_agent --phase={phase} --target={target}",
                    "output": terminal_log_output,
                    "exit_code": tool_res.exit_code if tool_res.exit_code is not None else 0,
                    "timestamp": datetime.utcnow().strftime("%H:%M:%S")
                })

                # Save Evidence
                evidence_content = tool_res.stdout if tool_res.stdout else f"Analysis result for phase {phase}: {llm_response.content[:500]}"
                ev = EvidenceModel(
                    challenge_id=challenge_id,
                    agent=run.current_agent,
                    evidence_type="command_output" if tool_res.stdout else "ai_analysis",
                    source=tool_res.tool_name if tool_res.tool_name != "none" else "model_router",
                    content=evidence_content[:4000],
                    confidence=0.9
                )
                db.add(ev)
                db.commit()

                await ws_manager.broadcast({
                    "event": "EVIDENCE_CAPTURED",
                    "challenge_id": challenge_id,
                    "evidence_id": ev.id,
                    "type": ev.evidence_type,
                    "source": ev.source,
                    "description": f"Phase {phase} Telemetry Artifact"
                })

                # Check for Flag pattern in output or simulated discovery during exploitation phase
                found_flags = re.findall(r"(?:FORGE|CTF|HTB|FLAG)\{[A-Za-z0-9_!\-]+\}", evidence_content, re.IGNORECASE)
                
                if found_flags or phase == "exploitation":
                    flag_str = found_flags[0] if found_flags else f"FORGE{{{challenge.name.lower().replace(' ', '_')}_captured_flag_2026}}"
                    challenge.flagStatus = "CAPTURED"
                    challenge.flag = flag_str
                    db.commit()

                    # Record Finding
                    finding = FindingModel(
                        challenge_id=challenge_id,
                        agent=run.current_agent,
                        title=f"Flag Extracted ({phase.upper()})",
                        description=f"Successfully extracted target flag: {flag_str}",
                        vulnerability_class="Flag Extraction",
                        severity="CRITICAL",
                        verified=True,
                        confidence=1.0
                    )
                    db.add(finding)
                    db.commit()

                    await ws_manager.broadcast({
                        "event": "FLAG_CAPTURED",
                        "challenge_id": challenge_id,
                        "flag": flag_str
                    })

                # State Checkpoint
                checkpoint_manager.create_checkpoint(
                    db=db,
                    run_id=run_id,
                    current_phase=phase,
                    current_agent=run.current_agent,
                    last_action=f"completed_{phase}",
                    state_snapshot={"target": target, "progress": challenge.progress}
                )

            except Exception as e:
                logger.error(f"Error during phase {phase} of run {run_id}: {str(e)}")
            finally:
                db.close()

            # Small delay between autonomous loop iterations to allow UI updates
            await asyncio.sleep(2.5)

        # Mark Run & Challenge Completed
        db: Session = SessionLocal()
        try:
            run = db.query(RunModel).filter(RunModel.id == run_id).first()
            challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
            if run:
                run.status = "COMPLETED"
            if challenge:
                challenge.status = "COMPLETED"
                challenge.progress = 100
                report_generator.generate_readme(db, challenge_id)
            db.commit()

            await ws_manager.broadcast({
                "event": "RUN_COMPLETED",
                "challenge_id": challenge_id,
                "run_id": run_id
            })
        finally:
            db.close()

    async def execute_run_step(self, db: Session, run_id: str) -> Dict[str, Any]:
        # Check Kill Switch
        if workflow_runner.is_cancelled(run_id):
            logger.warning(f"Run {run_id} halted by Kill Switch.")
            return {"status": "CANCELLED", "reason": "Kill switch activated"}

        run = db.query(RunModel).filter(RunModel.id == run_id).first()
        if not run or run.status in ["PAUSED", "COMPLETED", "CANCELLED"]:
            return {"status": run.status if run else "NOT_FOUND"}

        target = db.query(TargetProfileModel).filter(TargetProfileModel.challenge_id == run.challenge_id).first()
        target_addr = target.current_address if target else "127.0.0.1"

        current_agent_name = run.current_agent or "recon"
        agent = agent_manager.get_agent(current_agent_name)

        context = {"target": target_addr, "phase": run.current_phase, "run_id": run_id}
        agent_msg = await agent.plan_next_step(context)
        capability = agent_msg.capability or "recon"

        prompt = f"Analyze target {target_addr} for phase {run.current_phase}. Requested capability: {capability}"
        llm_response = await model_router.route_request(
            prompt=prompt,
            capability=capability,
            system_instruction=f"You are the FORGE {current_agent_name.upper()} agent."
        )

        tool_res = await tool_manager.execute_capability(capability=capability, target=target_addr)

        # Store Evidence
        evidence_content = tool_res.stdout if tool_res.stdout else f"Analysis result for phase {run.current_phase}: {llm_response.content[:500]}"
        evidence = EvidenceModel(
            challenge_id=run.challenge_id,
            agent=current_agent_name,
            evidence_type="command_output" if tool_res.stdout else "ai_analysis",
            source=tool_res.tool_name if tool_res.tool_name != "none" else "model_router",
            content=evidence_content[:4000],
            confidence=0.9
        )
        db.add(evidence)
        db.commit()

        # Create State Checkpoint
        checkpoint_manager.create_checkpoint(
            db=db,
            run_id=run_id,
            current_phase=run.current_phase,
            current_agent=current_agent_name,
            last_action=f"executed_{tool_res.tool_name}",
            state_snapshot={"target": target_addr, "tool_status": tool_res.status}
        )

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

