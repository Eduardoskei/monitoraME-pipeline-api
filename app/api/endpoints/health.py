from fastapi import APIRouter


router = APIRouter(tags=["Sistema"])


@router.get(
    "/health",
    summary="Verifica saude da API",
    response_description="Status operacional da API.",
)
async def health():
    return {"status": "healthy"}
