"""Student pages for the rewards (docs/STUDENT_UI_SPEC.md section 8): the Reward Shop and the full region map.
All checks (rank, balance, already owned) happen on the server in app/rewards.py."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import rewards, rewards_config
from app.parent import is_parent
from app.web import form_fields, get_conn, templates

router = APIRouter()

MESSAGES = {
    "bought": "Bought! Press “Use this” to wear it.",
    "owned": "You already have that one.",
    "locked": "That opens at a higher rank.",
    "short": f"Not enough {rewards_config.SHARDS_LABEL} yet.",
    "unknown": "That item isn't in the shop.",
}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@router.get("/shop", response_class=HTMLResponse)
def shop(request: Request, m: str = "", conn=Depends(get_conn)):
    rewards.award_pending(conn)
    return templates.TemplateResponse(request, "shop.html", {
        "items": rewards.shop_items(conn), "rank": rewards.rank_info(conn), "message": MESSAGES.get(m, ""),
        "is_parent": is_parent(request),
    })


@router.post("/shop/buy")
def buy(form=Depends(form_fields), conn=Depends(get_conn)):
    return _redirect(f"/shop?m={rewards.buy(conn, form.get('item', ''))}")


@router.post("/shop/equip")
def equip(form=Depends(form_fields), conn=Depends(get_conn)):
    rewards.equip(conn, form.get("item", ""))
    return _redirect("/shop")


@router.post("/shop/unequip")
def unequip(form=Depends(form_fields), conn=Depends(get_conn)):
    if form.get("slot") in rewards_config.SLOTS:
        rewards.unequip(conn, form["slot"])
    return _redirect("/shop")


@router.get("/map", response_class=HTMLResponse)
def region_map(request: Request, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "map.html", {
        "regions": rewards.map_view(conn), "is_parent": is_parent(request),
    })
