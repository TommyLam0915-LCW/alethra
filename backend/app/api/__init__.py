from fastapi import APIRouter
from app.api.auth import router as auth_router
from app.api.brand import router as brand_router
from app.api.demo_assets import router as demo_assets_router
from app.api.experiments import router as experiments_router
from app.api.runs import router as runs_router
from app.api.simulations import router as simulations_router
from app.api.paper_portfolio import router as paper_portfolio_router
from app.api.model_research import router as model_research_router
from app.api.model_research import memory_router as personal_memory_router
from app.api.model_research import card_router as model_card_router
from app.api.model_research_export import router as model_research_export_router
from app.api.pilot import router as pilot_router

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(brand_router)
v1_router.include_router(demo_assets_router)
v1_router.include_router(experiments_router)
v1_router.include_router(runs_router)
v1_router.include_router(simulations_router)
v1_router.include_router(paper_portfolio_router)
v1_router.include_router(model_research_router)
v1_router.include_router(personal_memory_router)
v1_router.include_router(model_card_router)
v1_router.include_router(model_research_export_router)
v1_router.include_router(pilot_router)

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(v1_router)

__all__ = ["api_router"]
