"""Student pages for the rewards (docs/STUDENT_UI_SPEC.md section 8): the Reward Shop and the full region map.
All checks (rank, balance, already owned) happen on the server in app/rewards.py."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app import rewards, rewards_config, uploads
from app.parent import is_parent
from app.web import form_fields, get_conn, templates

router = APIRouter()

MESSAGES = {
    "bought": "Bought! Press “Use this” to wear it.",
    "owned": "You already have that one.",
    "locked": "That opens at a higher rank.",
    "short": f"Not enough {rewards_config.SHARDS_LABEL} yet.",
    "unknown": "That item isn't in the shop.",
    "requested": "Asked! Your Shards are held until a parent says yes or no.",
    "retired": "That reward isn't available any more.",
}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@router.get("/shop", response_class=HTMLResponse)
def shop(request: Request, m: str = "", conn=Depends(get_conn)):
    rewards.award_pending(conn)
    return templates.TemplateResponse(request, "shop.html", {
        "items": rewards.shop_items(conn), "rank": rewards.rank_info(conn), "message": MESSAGES.get(m, ""),
        "is_parent": is_parent(request),
        "real_items": rewards.real_shop(conn), "pending": rewards.pending_requests(conn),
        "decided": rewards.recent_decided(conn), "held": rewards.held_shards(conn),
        "my_scenes": rewards.uploaded_scenes(conn),
    })


@router.post("/shop/request")
def request_real(form=Depends(form_fields), conn=Depends(get_conn)):
    return _redirect(f"/shop?m={rewards.request_real(conn, form.get('item', ''))}")


@router.post("/shop/use-scene")
def use_scene(form=Depends(form_fields), conn=Depends(get_conn)):
    image_id = form.get("image", "")
    if image_id.isdigit():
        rewards.equip_upload_scene(conn, int(image_id))
    return _redirect("/shop")


@router.get("/uploads/{name}")
def uploaded_picture(name: str):
    """A picture the parent uploaded (only names this app generated are served; see app/uploads.py)."""
    found = uploads.file_for(name)
    if found is None:
        raise HTTPException(status_code=404, detail="Picture not found")
    path, media_type = found
    return FileResponse(path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff",
                                                              "Cache-Control": "private, max-age=3600"})


@router.post("/shop/buy")
def buy(form=Depends(form_fields), conn=Depends(get_conn)):
    return _redirect(f"/shop?m={rewards.buy(conn, form.get('item', ''))}")


@router.post("/shop/equip")
def equip(form=Depends(form_fields), conn=Depends(get_conn)):
    rewards.equip(conn, form.get("item", ""))
    return _redirect("/shop")


@router.post("/shop/unequip")
def unequip(form=Depends(form_fields), conn=Depends(get_conn)):
    if form.get("slot") in rewards_config.SLOTS:   # also takes off an uploaded scene (same "scene" slot)
        rewards.unequip(conn, form["slot"])
    return _redirect("/shop")


@router.get("/map", response_class=HTMLResponse)
def region_map(request: Request, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "map.html", {
        "regions": rewards.map_view(conn), "is_parent": is_parent(request),
    })
