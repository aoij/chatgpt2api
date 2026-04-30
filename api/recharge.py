from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from services.recharge_service import recharge_service


class RechargeOrderCreateRequest(BaseModel):
    amount: int | str
    pay_type: str
    token_name: str = ""


def _request_base_url(request: Request) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/recharge/options")
    async def get_recharge_options():
        return recharge_service.options()

    @router.post("/api/recharge/orders")
    async def create_recharge_order(body: RechargeOrderCreateRequest, request: Request):
        try:
            return recharge_service.create_order(
                amount=body.amount,
                pay_type=body.pay_type,
                token_name=body.token_name,
                public_base_url=_request_base_url(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc

    @router.get("/api/recharge/orders/{out_trade_no}")
    async def get_recharge_order(out_trade_no: str):
        order = recharge_service.get_order(out_trade_no)
        if order is None:
            raise HTTPException(status_code=404, detail={"error": "order not found"})
        return order

    @router.api_route("/api/recharge/notify", methods=["GET", "POST"])
    async def recharge_notify(request: Request):
        if request.method.upper() == "POST":
            try:
                form = await request.form()
                params = {key: str(value) for key, value in form.multi_items()}
            except Exception:
                params = {}
        else:
            params = dict(request.query_params)
        if not params:
            params = dict(request.query_params)
        try:
            recharge_service.handle_paid_notify(params)
        except Exception as exc:
            print(f"[recharge] notify failed: {exc}")
            return PlainTextResponse("fail")
        return PlainTextResponse("success")

    @router.get("/api/recharge/return")
    async def recharge_return(out_trade_no: str = ""):
        suffix = f"?recharge_order={out_trade_no}" if out_trade_no else ""
        return RedirectResponse(url=f"/login{suffix}", status_code=302)

    return router
