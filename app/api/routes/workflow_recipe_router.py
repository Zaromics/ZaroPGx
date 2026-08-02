from fastapi import APIRouter, HTTPException, status

from app.services.workflow_registry import get_recipe, list_recipes

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


def _to_response(recipe) -> dict:
    return {
        "workflow_type": recipe.workflow_type,
        "display_name": recipe.display_name,
        "description": recipe.description,
        "option_fields": list(recipe.option_fields),
        "step_templates": [
            {
                "step_name": t.step_name,
                "container_name": t.container_name,
                "when": t.when,
            }
            for t in recipe.step_templates
        ],
    }


@router.get("/")
@router.get("")
async def list_workflow_recipes():
    return [_to_response(r) for r in list_recipes()]


@router.get("/{workflow_type}")
async def get_workflow_recipe(workflow_type: str):
    recipe = get_recipe(workflow_type)
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown workflow_type"
        )
    return _to_response(recipe)
