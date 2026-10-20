"""Parent pages (behind the PIN) for the Reward Shop's real-world rewards and for uploaded pictures (task 5b).
Approving or declining a request and every upload check happen here on the server; see app/rewards.py and
app/uploads.py for the rules."""

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app import rewards, rewards_config, uploads
from app.parent import is_parent, require_parent
from app.web import form_fields, get_conn, templates

router = APIRouter(prefix="/parent")

MESSAGES = {
    "approved": "Approved. The Star Shards stay spent.",
    "declined": "Declined. The Star Shards went back to her.",
    "gone": "That request was already answered.",
    "added": "Reward added.",
    "saved": "Reward saved.",
    "retired": "Reward retired. It no longer shows in the shop.",
    "restored": "Reward restored.",
    "uploaded": "Picture saved.",
    "deleted": "Picture deleted.",
    "scene_used": "Scene is now her background.",
}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _login(request: Request) -> RedirectResponse:
    return _redirect(f"/parent/login?next={request.url.path}")


# ---- Real-world rewards --------------------------------------------------------------------------------

def _rewards_page(request, conn, message="", problems=(), status_code=200, form=None):
    return templates.TemplateResponse(request, "parent_rewards.html", {
        "items": rewards.real_items(conn, include_retired=True), "pending": rewards.pending_requests(conn),
        "decided": rewards.recent_decided(conn, 15), "shards_label": rewards_config.SHARDS_LABEL,
        "balance": rewards.available_shards(conn), "held": rewards.held_shards(conn),
        "message": message, "problems": list(problems), "form": form or {}, "is_parent": True,
        "name_max": rewards.REAL_NAME_MAX, "note_max": rewards.REAL_NOTE_MAX,
    }, status_code=status_code)


@router.get("/rewards", response_class=HTMLResponse)
def rewards_page(request: Request, m: str = "", conn=Depends(get_conn)):
    if not is_parent(request):
        return _login(request)
    return _rewards_page(request, conn, MESSAGES.get(m, ""))


@router.post("/rewards/items", dependencies=[Depends(require_parent)])
def add_item(request: Request, form=Depends(form_fields), conn=Depends(get_conn)):
    problems = rewards.add_real_item(conn, form.get("name", ""), form.get("cost", ""), form.get("note", ""))
    if problems:
        return _rewards_page(request, conn, problems=problems, status_code=422, form=form)
    return _redirect("/parent/rewards?m=added")


@router.post("/rewards/items/{item_id}", dependencies=[Depends(require_parent)])
def edit_item(request: Request, item_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    problems = rewards.edit_real_item(conn, item_id, form.get("name", ""), form.get("cost", ""), form.get("note", ""))
    if problems:
        return _rewards_page(request, conn, problems=problems, status_code=422)
    return _redirect("/parent/rewards?m=saved")


@router.post("/rewards/items/{item_id}/retire", dependencies=[Depends(require_parent)])
def retire_item(item_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    restore = form.get("restore") == "1"
    rewards.set_real_retired(conn, item_id, not restore)
    return _redirect(f"/parent/rewards?m={'restored' if restore else 'retired'}")


@router.post("/rewards/requests/{request_id}/approve", dependencies=[Depends(require_parent)])
def approve(request_id: int, conn=Depends(get_conn)):
    return _redirect(f"/parent/rewards?m={rewards.decide_request(conn, request_id, True)}")


@router.post("/rewards/requests/{request_id}/decline", dependencies=[Depends(require_parent)])
def decline(request_id: int, conn=Depends(get_conn)):
    return _redirect(f"/parent/rewards?m={rewards.decide_request(conn, request_id, False)}")


# ---- Pictures ------------------------------------------------------------------------------------------

def _images_page(request, conn, message="", problems=(), status_code=200):
    return templates.TemplateResponse(request, "parent_images.html", {
        "companion": uploads.list_images(conn, "companion"), "scenes": rewards.uploaded_scenes(conn),
        "default_companion": rewards_config.COMPANION_DEFAULT_IMAGE, "max_scenes": uploads.MAX_SCENES,
        "message": message, "problems": list(problems), "is_parent": True,
    }, status_code=status_code)


@router.get("/images", response_class=HTMLResponse)
def images_page(request: Request, m: str = "", conn=Depends(get_conn)):
    if not is_parent(request):
        return _login(request)
    return _images_page(request, conn, MESSAGES.get(m, ""))


@router.post("/images/upload", dependencies=[Depends(require_parent)])
async def upload_image(request: Request, kind: str = Form(""), label: str = Form(""),
                       file: UploadFile | None = File(None), conn=Depends(get_conn)):
    # never reads more than 5 MB + 1 byte into memory; no file chosen gives None or an empty file
    data = await file.read(uploads.MAX_BYTES + 1) if file is not None else b""
    try:
        uploads.save_image(conn, kind, data, label)
    except uploads.UploadError as e:
        return _images_page(request, conn, problems=[str(e)], status_code=422)
    return _redirect("/parent/images?m=uploaded")


@router.post("/images/{image_id}/delete", dependencies=[Depends(require_parent)])
def delete_image(image_id: int, conn=Depends(get_conn)):
    uploads.delete_image(conn, image_id)
    return _redirect("/parent/images?m=deleted")


@router.post("/images/{image_id}/use", dependencies=[Depends(require_parent)])
def use_scene(image_id: int, conn=Depends(get_conn)):
    rewards.equip_upload_scene(conn, image_id)
    return _redirect("/parent/images?m=scene_used")
