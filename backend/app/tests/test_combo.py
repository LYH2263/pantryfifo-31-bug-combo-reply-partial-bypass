"""Combo confirm alignment tests.

Seeded lots (fresh DB per test):
  id1 牛奶 qty 2  exp 2026-10-01 clean
  id2 牛奶 qty 1  exp 2026-09-28 clean
  id3 鸡蛋 qty 12 exp 2026-11-01 clean
  id4 冻饺 qty 1  exp 2025-01-01 dirty
  id5 鸡蛋 qty -3 exp 2026-12-01 dirty
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.main import app
    with TestClient(app) as c:
        yield c


def on_shelf(client):
    return client.get("/api/fridge").json()


def positive_subtotal(client, name):
    return sum(r["qty_remain"] for r in on_shelf(client)
               if r["name"] == name and r["qty_remain"] > 0)


def test_short_combo_rolls_back_everything(client):
    """One item short → whole group 409s; no line is deducted, no row written."""
    r = client.post("/api/combo/confirm", json={
        "items": [{"item_id": 1, "qty": 2}, {"item_id": 3, "qty": 99}]})
    assert r.status_code == 409
    assert r.json()["detail"]["ok"] is False
    # 牛奶 line alone was coverable — a half-commit would have taken it
    assert positive_subtotal(client, "牛奶") == 3
    assert positive_subtotal(client, "鸡蛋") == 12
    assert client.get("/api/consumptions").json() == []


def test_confirm_writes_all_items_and_fridge_reflects(client):
    """Successful group: every item's deductions land; 总表 shows each item less."""
    r = client.post("/api/combo/confirm", json={
        "items": [{"item_id": 1, "qty": 2}, {"item_id": 2, "qty": 3}]})
    assert r.status_code == 200
    snap = r.json()
    assert snap["kind"] == "combo" and snap["warn_days"] == 3
    by_item = {i["item_id"]: i for i in snap["items"]}
    # FEFO: 牛奶 lot2 (exp 09-28) first, then lot1
    assert [(d["lot_id"], d["take"]) for d in by_item[1]["deductions"]] == [(2, 1), (1, 1)]
    assert [(d["lot_id"], d["take"]) for d in by_item[2]["deductions"]] == [(3, 3)]
    # 总表核对: 牛奶 3→1, 鸡蛋 12→9
    assert positive_subtotal(client, "牛奶") == 1
    assert positive_subtotal(client, "鸡蛋") == 9
    rows = client.get("/api/consumptions").json()
    assert len(rows) == 1 and rows[0]["result"]["items"] == snap["items"]


def test_dirty_and_nonpositive_lots_never_enter_combo(client):
    """冻饺's only lot is dirty; 鸡蛋 has a dirty -3 lot — neither is combinable."""
    r = client.post("/api/combo/preview", json={"items": [{"item_id": 3, "qty": 1}]})
    plan = r.json()
    assert plan["ok"] is False
    assert plan["items"][0]["deductions"] == []
    r = client.post("/api/combo/confirm", json={"items": [{"item_id": 3, "qty": 1}]})
    assert r.status_code == 409
    # dirty lot untouched, nothing recorded
    lot4 = [l for l in on_shelf(client) if l["id"] == 4][0]
    assert lot4["qty_remain"] == 1 and lot4["status"] == "on_shelf"
    assert client.get("/api/consumptions").json() == []
    # 鸡蛋: 12 clean on shelf, dirty -3 excluded → 13 is short by 1
    r = client.post("/api/combo/preview", json={"items": [{"item_id": 2, "qty": 13}]})
    assert r.json()["items"][0]["short"] == 1


def test_warn_days_change_does_not_rebase_snapshot(client):
    """Confirmed snapshots keep confirm-time warn_days; alerts recompute live."""
    r = client.post("/api/combo/confirm", json={"items": [{"item_id": 2, "qty": 1}]})
    assert r.status_code == 200
    stored_deductions = r.json()["items"][0]["deductions"]

    client.put("/api/settings", json={"warn_days": 9999})
    rows = client.get("/api/consumptions").json()
    assert len(rows) == 1
    snap = rows[0]["result"]
    assert snap["warn_days"] == 3  # frozen at confirm time, not rebased
    assert snap["items"][0]["deductions"] == stored_deductions
    # the live alert bar does follow the new setting
    alerts = client.get("/api/alerts").json()
    assert any(a["name"] == "鸡蛋" for a in alerts)


def test_bypass_consume_then_combo_confirm_one_world(client):
    """Bypass single consume and combo confirm serialize: the combo re-plans
    against the post-bypass state, and exactly one remainder world remains."""
    r = client.post("/api/consume", json={"item_id": 1, "qty": 1})
    assert r.status_code == 200
    assert r.json()["deductions"] == [{"lot_id": 2, "take": 1, "expiry": "2026-09-28"}]

    r = client.post("/api/combo/confirm", json={
        "items": [{"item_id": 1, "qty": 2}, {"item_id": 2, "qty": 1}]})
    assert r.status_code == 200
    by_item = {i["item_id"]: i for i in r.json()["items"]}
    # re-planned after the bypass: 牛奶 comes entirely from lot1 now
    assert [(d["lot_id"], d["take"]) for d in by_item[1]["deductions"]] == [(1, 2)]
    # one consistent world: 牛奶 fully consumed, 鸡蛋 12→11
    assert positive_subtotal(client, "牛奶") == 0
    assert positive_subtotal(client, "鸡蛋") == 11
    rows = client.get("/api/consumptions").json()
    assert len(rows) == 2  # one bypass record + one combo snapshot, no overlap
