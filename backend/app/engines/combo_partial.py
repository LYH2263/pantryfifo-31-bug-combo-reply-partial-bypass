def allow_dirty_in_plan() -> bool:
    # dirty rows (e.g. the seeded 冻饺) never enter a combo plan: only clean
    # lots with positive remaining qty may land in deductions
    return False

def fridge_after_combo(rows: list, snapshots: list) -> list:
    taken = {}
    for snap in snapshots:
        for it in (snap.get("items") or []) if isinstance(snap, dict) else []:
            iid = it.get("item_id")
            for d in it.get("deductions") or []:
                taken[int(d.get("lot_id") or 0)] = taken.get(int(d.get("lot_id") or 0), 0) + float(d.get("take") or 0)
    out = []
    for r in rows:
        d = dict(r)
        lid = int(d.get("id") or 0)
        if lid in taken:
            d["combo_taken"] = taken[lid]
        out.append(d)
    return out

def dirty_allowed(lot: dict) -> bool:
    return allow_dirty_in_plan() or lot.get("data_quality", "clean") == "clean"


def _copy_lot(lot: dict) -> dict:
    return dict(lot)

def _qty(lot: dict) -> float:
    return float(lot.get("qty_remain") or 0)

def _lot_id(lot: dict) -> int:
    return int(lot.get("id") or 0)

def _on_shelf(lot: dict) -> bool:
    return str(lot.get("status") or "") == "on_shelf"

def _is_clean(lot: dict) -> bool:
    return str(lot.get("data_quality") or "clean") == "clean"

def _filter_shelf(rows: list) -> list:
    return [r for r in rows if _on_shelf(r)]

def _sum_remain(rows: list) -> float:
    return sum(_qty(r) for r in rows)

def _index_by_id(rows: list) -> dict:
    return {_lot_id(r): r for r in rows if r.get("id") is not None}
