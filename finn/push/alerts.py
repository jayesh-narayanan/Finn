"""Price/volume alert rules. Mirrors web/js/rules.js, which re-runs these on the device
whenever the investor changes a threshold (so calibration is instant)."""
from __future__ import annotations


def evaluate(symbol: str, p: dict, rules: dict, days: int = 1) -> list[dict]:
    """Alerts for the last `days` sessions."""
    out = []
    d, c, v = p["d"], p["c"], p["v"]
    lb = int(rules.get("volume_lookback", 20))
    for i in range(max(1, len(d) - days), len(d)):
        move = (c[i] / c[i - 1] - 1) * 100
        if abs(move) >= rules["price_move_pct"]:
            out.append({"symbol": symbol, "date": d[i], "kind": "price", "value": round(move, 2),
                        "text": f"{'Up' if move > 0 else 'Down'} {abs(move):.1f}% on the day"})
        window = [x for x in v[max(0, i - lb):i] if x]
        if len(window) >= 5 and v[i]:
            avg = sum(window) / len(window)
            mult = v[i] / avg if avg else 0
            if mult >= rules["volume_multiple"]:
                out.append({"symbol": symbol, "date": d[i], "kind": "volume", "value": round(mult, 2),
                            "text": f"Volume {mult:.1f}× its {lb}-day average"})
    return out
