from fastapi import APIRouter
from app.api.auth import router as auth_router
from app.api.brand import router as brand_router
from app.api.demo_assets import router as demo_assets_router
from app.api.experiments import router as experiments_router
from app.api.runs import router as runs_router
from app.api.saved_comparisons import router as saved_comparisons_router
from app.api.simulations import router as simulations_router
from app.api.paper_portfolio import router as paper_portfolio_router
from app.api.model_research import router as model_research_router
from app.api.model_research import memory_router as personal_memory_router
from app.api.model_research import card_router as model_card_router
from app.api.model_research_export import router as model_research_export_router
from app.api.pilot import router as pilot_router
from app.api.research_drafts import router as research_drafts_router
from app.api.api_keys import router as api_keys_router
from app.api.execution import router as execution_router
from app.api.billing import router as billing_router
from app.api.inference import router as inference_router
from app.api.training import router as training_router
from app.api.kill_switches import router as kill_switches_router
from app.api.instruments import router as instruments_router
from app.api.ml_training import router as ml_training_router
from app.api.execution_agent import router as execution_agent_router
from app.api.watchlists import router as watchlists_router
from app.api.user_layouts import router as user_layouts_router
from app.api.ai_assistant import router as ai_assistant_router
from app.api.human_decisions import router as human_decisions_router
from app.api.datasets import router as datasets_router

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(brand_router)
v1_router.include_router(demo_assets_router)
v1_router.include_router(instruments_router)
v1_router.include_router(ml_training_router)
v1_router.include_router(experiments_router)
v1_router.include_router(runs_router)
v1_router.include_router(saved_comparisons_router)
v1_router.include_router(simulations_router)
v1_router.include_router(paper_portfolio_router)
v1_router.include_router(model_research_router)
v1_router.include_router(personal_memory_router)
v1_router.include_router(model_card_router)
v1_router.include_router(model_research_export_router)
v1_router.include_router(pilot_router)
v1_router.include_router(research_drafts_router)
v1_router.include_router(execution_router)
v1_router.include_router(api_keys_router)
v1_router.include_router(billing_router)
v1_router.include_router(inference_router)
v1_router.include_router(training_router)
v1_router.include_router(kill_switches_router)
v1_router.include_router(execution_agent_router)
v1_router.include_router(watchlists_router)
v1_router.include_router(user_layouts_router)
v1_router.include_router(ai_assistant_router)
v1_router.include_router(human_decisions_router)
v1_router.include_router(datasets_router)

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(v1_router)

__all__ = ["api_router"]
