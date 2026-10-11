from fastapi import APIRouter
from app.lib.brand import get_brand

router = APIRouter(tags=["brand"])


@router.get("/brand")
def get_brand_info():
    brand = get_brand()
    return {
        "name": brand.name,
        "tagline": brand.tagline,
        "tagline_zh": brand.tagline_zh,
        "product_descriptor": brand.product_descriptor,
        "product_descriptor_zh": brand.product_descriptor_zh,
        "disclaimer": brand.disclaimer,
        "disclaimer_zh": brand.disclaimer_zh,
    }
