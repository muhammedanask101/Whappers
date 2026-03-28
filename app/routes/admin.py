from html import escape
from typing import Any, Dict, List

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db.mongo import get_collection, parse_datetime
from app.services.admin import (
    authenticate_manager,
    create_business_from_form,
    delete_business,
    ensure_manager_seed,
    get_manager_overview,
    get_selected_business_id,
    list_business_summaries,
    update_business_from_form,
)
from app.services.vendor import (
    add_product,
    get_business_by_id,
    get_product_catalog,
    update_product,
)

router = APIRouter(tags=["manager"])


def _require_manager(request: Request) -> str:
    email = request.session.get("manager_email")
    if not email:
        raise PermissionError
    return str(email)


@router.get("/manager/login", response_class=HTMLResponse)
def manager_login_page(request: Request, error: str = "") -> HTMLResponse:
    return HTMLResponse(_render_login(error))


@router.post("/manager/login", response_model=None)
async def manager_login(request: Request, email: str = Form(""), password: str = Form("")):
    ensure_manager_seed()
    manager = authenticate_manager(email, password)
    if not manager:
        return HTMLResponse(_render_login("Invalid manager credentials."), status_code=401)
    request.session["manager_email"] = manager["email"]
    return RedirectResponse(url="/manager", status_code=303)


@router.post("/manager/logout")
def manager_logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/manager/login", status_code=303)


@router.get("/manager", response_class=HTMLResponse, response_model=None)
def manager_dashboard(request: Request, business_id: str = ""):
    try:
        manager_email = _require_manager(request)
    except PermissionError:
        return RedirectResponse(url="/manager/login", status_code=303)

    summaries = list_business_summaries()
    selected_business_id = get_selected_business_id(business_id)
    selected_business = get_business_by_id(selected_business_id)
    products = get_product_catalog(business_id=selected_business_id, limit=100) if selected_business else []
    orders = list(get_collection("orders").find({"business_id": selected_business_id}).sort("created_at", -1).limit(12)) if selected_business else []
    return HTMLResponse(_render_dashboard(manager_email, summaries, selected_business or {}, products, orders))


@router.post("/manager/businesses", response_model=None)
async def create_business_action(request: Request):
    try:
        _require_manager(request)
    except PermissionError:
        return RedirectResponse(url="/manager/login", status_code=303)
    form = {key: str(value) for key, value in (await request.form()).items()}
    try:
        business = create_business_from_form(form)
    except ValueError:
        fallback_business = get_selected_business_id("")
        return RedirectResponse(url=f"/manager?business_id={fallback_business}", status_code=303)
    return RedirectResponse(url=f"/manager?business_id={business['business_id']}", status_code=303)


@router.post("/manager/businesses/{business_id}", response_model=None)
async def update_business_action(request: Request, business_id: str):
    try:
        _require_manager(request)
    except PermissionError:
        return RedirectResponse(url="/manager/login", status_code=303)
    form = {key: str(value) for key, value in (await request.form()).items()}
    update_business_from_form(business_id, form)
    return RedirectResponse(url=f"/manager?business_id={business_id}", status_code=303)


@router.post("/manager/businesses/{business_id}/delete", response_model=None)
async def delete_business_action(request: Request, business_id: str):
    try:
        _require_manager(request)
    except PermissionError:
        return RedirectResponse(url="/manager/login", status_code=303)
    try:
        delete_business(business_id)
    except ValueError:
        return RedirectResponse(url=f"/manager?business_id={business_id}", status_code=303)
    next_business = get_selected_business_id("")
    return RedirectResponse(url=f"/manager?business_id={next_business}", status_code=303)


@router.post("/manager/businesses/{business_id}/products", response_model=None)
async def create_product_action(request: Request, business_id: str):
    try:
        _require_manager(request)
    except PermissionError:
        return RedirectResponse(url="/manager/login", status_code=303)
    form = {key: str(value) for key, value in (await request.form()).items()}
    add_product(
        business_id=business_id,
        name=form.get("name", ""),
        price=float(form.get("price") or 0),
        unit=form.get("unit") or "unit",
        category=form.get("category") or None,
        description=form.get("description", ""),
        tags=[tag.strip() for tag in form.get("tags", "").split(",") if tag.strip()],
        attributes={},
    )
    return RedirectResponse(url=f"/manager?business_id={business_id}", status_code=303)


@router.post("/manager/businesses/{business_id}/products/update", response_model=None)
async def update_product_action(request: Request, business_id: str):
    try:
        _require_manager(request)
    except PermissionError:
        return RedirectResponse(url="/manager/login", status_code=303)
    form = {key: str(value) for key, value in (await request.form()).items()}
    update_product(
        business_id=business_id,
        name=form.get("name", ""),
        price=float(form["price"]) if form.get("price") else None,
        unit=form.get("unit") or None,
        category=form.get("category") or None,
        description=form.get("description") or None,
        active=form.get("active") == "on",
    )
    return RedirectResponse(url=f"/manager?business_id={business_id}", status_code=303)


def _render_login(error: str = "") -> str:
    error_html = f"<p class='error'>{escape(error)}</p>" if error else ""
    return f"""
<!doctype html>
<html>
<head>
  <title>Manager Login</title>
  <style>
    body {{ font-family: Georgia, serif; background: linear-gradient(135deg, #f0e4d7, #d9ecff); min-height: 100vh; display: grid; place-items: center; margin: 0; }}
    .card {{ width: min(420px, 92vw); background: rgba(255,255,255,.92); padding: 32px; border-radius: 20px; box-shadow: 0 18px 50px rgba(0,0,0,.12); }}
    input {{ width: 100%; padding: 12px; margin: 8px 0 16px; border: 1px solid #c7b9aa; border-radius: 12px; }}
    button {{ width: 100%; padding: 12px; border: 0; border-radius: 12px; background: #22333b; color: white; }}
    .error {{ color: #982b1c; }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/manager/login">
    <h1>Manager Access</h1>
    <p>Private dashboard for all client businesses.</p>
    {error_html}
    <label>Email</label>
    <input name="email" type="email" required />
    <label>Password</label>
    <input name="password" type="password" required />
    <button type="submit">Sign In</button>
  </form>
</body>
</html>
"""


def _render_dashboard(manager_email: str, businesses: List[Dict[str, Any]], selected: Dict[str, Any], products: List[Dict[str, Any]], orders: List[Dict[str, Any]]) -> str:
    active_business_count = sum(1 for business in businesses if business.get("active", True))
    business_cards = "".join(
        f"<a class='biz-card' href='/manager?business_id={escape(b['business_id'])}'><strong>{escape(b['name'])}</strong><span>{escape(b['whatsapp_number'] or 'No channel')}</span><span>{b['product_count']} products | {b['pending_order_count']} pending</span></a>"
        for b in businesses
    )
    product_rows = "".join(
        f"""
        <form class='table-row' method='post' action='/manager/businesses/{escape(selected.get("business_id", ""))}/products/update'>
          <input type='hidden' name='name' value='{escape(product["name"])}' />
          <input name='price' value='{product["price"]}' />
          <input name='unit' value='{escape(product["unit"])}' />
          <input name='category' value='{escape(product["category"])}' />
          <input name='description' value='{escape(product.get("description", ""))}' />
          <label><input type='checkbox' name='active' {'checked' if product.get('active', True) else ''}/> active</label>
          <button type='submit'>Save</button>
        </form>
        """
        for product in products
    )
    order_rows = "".join(
        f"<tr><td>{escape(str(order.get('order_id', '')))}</td><td>{escape(str(order.get('customer_phone', '')))}</td><td>{escape(str(order.get('status', '')))}</td><td>Rs {float(order.get('total', 0)):.2f}</td><td>{escape(_fmt_dt(order.get('created_at')))}</td></tr>"
        for order in orders
    )
    selected_id = escape(selected.get("business_id", ""))
    delete_button = ""
    if selected and selected.get("business_id") and active_business_count > 1:
        delete_button = f"""
        <form method="post" action="/manager/businesses/{selected_id}/delete" onsubmit="return confirm('Delete this business?');">
          <button type="submit" style="background:#9a3412;">Delete Business</button>
        </form>
        """
    return f"""
<!doctype html>
<html>
<head>
  <title>Manager Dashboard</title>
  <style>
    :root {{ --ink:#1d2930; --panel:#fffaf2; --accent:#9a3412; --line:#d5c6b6; --bg:#f4efe8; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family: 'Segoe UI', sans-serif; color:var(--ink); background:
      radial-gradient(circle at top left, rgba(214,143,72,.22), transparent 28%),
      radial-gradient(circle at top right, rgba(67,135,184,.18), transparent 24%),
      var(--bg); }}
    .shell {{ max-width: 1360px; margin: 0 auto; padding: 24px; }}
    .topbar, .panel {{ background: rgba(255,250,242,.92); border:1px solid var(--line); border-radius:20px; }}
    .topbar {{ display:flex; justify-content:space-between; align-items:center; padding:18px 22px; margin-bottom:20px; }}
    .grid {{ display:grid; grid-template-columns: 330px 1fr; gap:20px; }}
    .panel {{ padding:20px; }}
    .biz-list {{ display:grid; gap:12px; margin:16px 0 24px; }}
    .biz-card {{ display:grid; gap:4px; padding:14px; border:1px solid var(--line); border-radius:16px; color:inherit; text-decoration:none; background:white; }}
    .stats {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; margin-bottom:20px; }}
    .stat {{ background:white; border:1px solid var(--line); border-radius:16px; padding:14px; }}
    form {{ display:grid; gap:10px; }}
    input, textarea, select {{ width:100%; padding:10px 12px; border-radius:12px; border:1px solid var(--line); background:white; }}
    button {{ border:0; border-radius:12px; padding:11px 14px; background:var(--ink); color:white; cursor:pointer; }}
    .muted {{ color:#5a6b73; }}
    .two {{ display:grid; grid-template-columns: repeat(2, 1fr); gap:12px; }}
    .table-row {{ display:grid; grid-template-columns: 1fr 1fr 1fr 2fr auto auto; gap:10px; align-items:center; margin-bottom:10px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ text-align:left; padding:10px; border-bottom:1px solid var(--line); font-size:14px; }}
    @media (max-width: 980px) {{ .grid {{ grid-template-columns: 1fr; }} .stats, .two, .table-row {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div>
        <h1 style="margin:0;">WhatsApp Manager Dashboard</h1>
        <div class="muted">Signed in as {escape(manager_email)}</div>
      </div>
      <form method="post" action="/manager/logout"><button type="submit">Logout</button></form>
    </div>
    <div class="grid">
      <aside class="panel">
        <h2>Businesses</h2>
        <div class="biz-list">{business_cards}</div>
        <h3>Add Business</h3>
        <form method="post" action="/manager/businesses">
          <input name="name" placeholder="Business name" required />
          <div class="two">
            <input name="slug" placeholder="Slug" />
            <input name="business_type" placeholder="Type" value="retail" />
          </div>
          <input name="whatsapp_number" placeholder="Twilio WhatsApp number" required />
          <input name="vendor_phone" placeholder="Vendor phone" required />
          <input name="contact_phone" placeholder="Contact phone" />
          <textarea name="description" placeholder="Description"></textarea>
          <button type="submit">Create Business</button>
        </form>
      </aside>
      <main>
        <section class="stats">
          {''.join(f"<div class='stat'><strong>{escape(b['name'])}</strong><div>{b['order_count']} orders</div><div>{b['product_count']} products</div></div>" for b in businesses[:4])}
        </section>
        <section class="panel" style="margin-bottom:20px;">
          <h2>{escape(selected.get("name", "Select a business"))}</h2>
          <div class="muted">{escape(selected.get("whatsapp_number", ""))}</div>
          <form method="post" action="/manager/businesses/{selected_id}">
            <div class="two">
              <input name="name" value="{escape(selected.get("name", ""))}" placeholder="Business name" />
              <input name="slug" value="{escape(selected.get("slug", ""))}" placeholder="Slug" />
            </div>
            <div class="two">
              <input name="whatsapp_number" value="{escape(selected.get("whatsapp_number", ""))}" placeholder="WhatsApp number" />
              <input name="vendor_phone" value="{escape(selected.get("vendor_phone", ""))}" placeholder="Vendor phone" />
            </div>
            <div class="two">
              <input name="contact_phone" value="{escape(selected.get("contact_phone", ""))}" placeholder="Contact phone" />
              <input name="business_type" value="{escape(selected.get("business_type", ""))}" placeholder="Business type" />
            </div>
            <textarea name="description" placeholder="Description">{escape(selected.get("description", ""))}</textarea>
            <div class="two">
              <textarea name="address" placeholder="Address">{escape(selected.get("address", ""))}</textarea>
              <textarea name="hours" placeholder="Hours">{escape(selected.get("hours", ""))}</textarea>
            </div>
            <div class="two">
              <textarea name="delivery_info" placeholder="Delivery info">{escape(selected.get("delivery_info", ""))}</textarea>
              <textarea name="policies" placeholder="Policies">{escape(selected.get("policies", ""))}</textarea>
            </div>
            <textarea name="support_details" placeholder="Support details">{escape(selected.get("support_details", ""))}</textarea>
            <label><input type="checkbox" name="active" {'checked' if selected.get('active', True) else ''}/> Active</label>
            <button type="submit">Save Business Settings</button>
          </form>
          {delete_button}
        </section>
        <section class="panel" style="margin-bottom:20px;">
          <h2>Products</h2>
          <form method="post" action="/manager/businesses/{selected_id}/products" class="two" style="margin-bottom:16px;">
            <input name="name" placeholder="Product name" required />
            <input name="price" type="number" step="0.01" placeholder="Price" required />
            <input name="unit" placeholder="Unit" value="unit" />
            <input name="category" placeholder="Category" />
            <input name="tags" placeholder="tags,comma,separated" />
            <input name="description" placeholder="Description" />
            <button type="submit">Add Product</button>
          </form>
          <div>{product_rows or "<p class='muted'>No products yet.</p>"}</div>
        </section>
        <section class="panel">
          <h2>Recent Orders</h2>
          <table>
            <thead><tr><th>Order</th><th>Customer</th><th>Status</th><th>Total</th><th>Created</th></tr></thead>
            <tbody>{order_rows or "<tr><td colspan='5' class='muted'>No orders yet.</td></tr>"}</tbody>
          </table>
        </section>
      </main>
    </div>
  </div>
</body>
</html>
"""


def _fmt_dt(value: Any) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M UTC") if parsed else str(value or "")
