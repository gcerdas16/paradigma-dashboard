"""
Dashboard web para Paradigma — visualización de apuestas y métricas.
Usa Flask + la misma DB PostgreSQL del scanner.
"""

import os
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

app = Flask(__name__, template_folder="templates")

# Costa Rica timezone (UTC-6)
CR_TZ = timezone(timedelta(hours=-6))

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///paradigma.db")
engine = create_engine(DATABASE_URL, echo=False)
Session = sessionmaker(bind=engine)


def get_bets():
    """Obtiene todas las apuestas de la DB."""
    session = Session()
    try:
        result = session.execute(text("""
            SELECT id, created_at, event_id, sport_key, sport_title,
                   commence_time, home_team, away_team, book_key, book_title,
                   market, outcome_name, outcome_point, odds_at_bet,
                   fair_prob, ev_percent, kelly_stake_percent,
                   pinnacle_odds_at_bet, bookmaker_link, stake,
                   bankroll_before, result, pnl, bankroll_after,
                   clv_percent, settled_at, is_paper,
                   COALESCE(bet_type, 'value') as bet_type,
                   arb_group_id, arb_profit_percent,
                   avg_ev_percent, num_books
            FROM bets
            ORDER BY id DESC
        """))
        columns = result.keys()
        return [dict(zip(columns, row)) for row in result.fetchall()]
    except Exception:
        return []
    finally:
        session.close()


def compute_stats(bets):
    """Calcula estadísticas a partir de las apuestas."""
    initial_bankroll = 500.0

    total_bets = len(bets)
    settled = [b for b in bets if b["result"] is not None]
    pending = [b for b in bets if b["result"] is None]
    wins = [b for b in settled if b["result"] == "win"]
    losses = [b for b in settled if b["result"] == "loss"]
    pushes = [b for b in settled if b["result"] == "push"]

    total_staked = sum(b["stake"] for b in bets if b["stake"])
    pending_staked = sum(b["stake"] for b in pending if b["stake"])
    total_pnl = sum(b["pnl"] for b in settled if b["pnl"] is not None)
    total_won = sum(b["pnl"] for b in wins if b["pnl"] is not None)
    total_lost = sum(abs(b["pnl"]) for b in losses if b["pnl"] is not None)

    # Current bankroll
    if settled:
        last_settled = max(settled, key=lambda b: b["id"])
        bankroll = last_settled.get("bankroll_after", initial_bankroll) or initial_bankroll
        for b in pending:
            if b["id"] > last_settled["id"]:
                bankroll -= b["stake"] or 0
    else:
        bankroll = initial_bankroll - pending_staked

    avg_ev = sum(b["ev_percent"] for b in bets if b["ev_percent"]) / total_bets if total_bets else 0
    avg_odds = sum(b["odds_at_bet"] for b in bets if b["odds_at_bet"]) / total_bets if total_bets else 0

    win_rate = len(wins) / len(settled) * 100 if settled else 0
    roi = total_pnl / sum(b["stake"] for b in settled if b["stake"]) * 100 if settled and sum(b["stake"] for b in settled if b["stake"]) > 0 else 0

    clv_bets = [b for b in settled if b["clv_percent"] is not None]
    avg_clv = sum(b["clv_percent"] for b in clv_bets) / len(clv_bets) if clv_bets else None

    # Bets per day
    if bets:
        dates = set()
        for b in bets:
            if b["created_at"]:
                dt = b["created_at"]
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                cr_dt = dt.astimezone(CR_TZ)
                dates.add(cr_dt.date())
        days_active = len(dates) or 1
        bets_per_day = total_bets / days_active
    else:
        bets_per_day = 0
        days_active = 0

    # Bets by sport
    sport_counts = {}
    for b in bets:
        sk = b["sport_key"] or "unknown"
        if "soccer" in sk:
            sport = "Fútbol"
        elif "basketball" in sk:
            sport = "Baloncesto"
        else:
            sport = sk
        sport_counts[sport] = sport_counts.get(sport, 0) + 1

    # Bets by bookmaker
    book_counts = {}
    for b in bets:
        bk = b["book_title"] or b["book_key"] or "unknown"
        book_counts[bk] = book_counts.get(bk, 0) + 1

    return {
        "initial_bankroll": initial_bankroll,
        "bankroll": bankroll,
        "total_bets": total_bets,
        "settled_count": len(settled),
        "pending_count": len(pending),
        "wins": len(wins),
        "losses": len(losses),
        "pushes": len(pushes),
        "win_rate": win_rate,
        "total_staked": total_staked,
        "pending_staked": pending_staked,
        "total_pnl": total_pnl,
        "total_won": total_won,
        "total_lost": total_lost,
        "roi": roi,
        "avg_ev": avg_ev,
        "avg_odds": avg_odds,
        "avg_clv": avg_clv,
        "bets_per_day": bets_per_day,
        "days_active": days_active,
        "sport_counts": sport_counts,
        "book_counts": book_counts,
        "bets_to_validate": max(0, 200 - len(settled)),
    }


def format_bet_for_display(bet):
    """Formatea un bet para mostrar en el dashboard."""
    dt = bet["created_at"]
    if dt:
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cr_dt = dt.astimezone(CR_TZ)
        bet["created_at_cr"] = cr_dt.strftime("%d/%m %H:%M")
        bet["created_at_date"] = cr_dt.strftime("%Y-%m-%d")
    else:
        bet["created_at_cr"] = "—"
        bet["created_at_date"] = "—"

    # Fecha/hora del evento (commence_time)
    ct = bet.get("commence_time")
    if ct:
        try:
            if isinstance(ct, str):
                ct_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            else:
                ct_dt = ct
            if ct_dt.tzinfo is None:
                ct_dt = ct_dt.replace(tzinfo=timezone.utc)
            cr_event = ct_dt.astimezone(CR_TZ)
            bet["event_date_cr"] = cr_event.strftime("%d/%m %H:%M")
            diff = ct_dt - datetime.now(timezone.utc)
            if diff.total_seconds() > 0:
                hours = int(diff.total_seconds() // 3600)
                mins = int((diff.total_seconds() % 3600) // 60)
                if hours >= 24:
                    days = hours // 24
                    bet["event_eta"] = f"en {days}d {hours % 24}h"
                else:
                    bet["event_eta"] = f"en {hours}h{mins:02d}m"
            else:
                bet["event_eta"] = "jugado"
        except Exception:
            bet["event_date_cr"] = "—"
            bet["event_eta"] = "—"
    else:
        bet["event_date_cr"] = "—"
        bet["event_eta"] = "—"

    sk = bet["sport_key"] or ""
    if "soccer" in sk:
        bet["sport_type"] = "Fútbol"
    elif "basketball" in sk:
        bet["sport_type"] = "Baloncesto"
    else:
        bet["sport_type"] = "Otro"

    if bet["outcome_point"] is not None:
        bet["outcome_display"] = f"{bet['outcome_name']} {bet['outcome_point']}"
    else:
        bet["outcome_display"] = bet["outcome_name"]

    r = bet["result"]
    if r == "win":
        bet["result_display"] = "Ganada"
        bet["result_class"] = "win"
    elif r == "loss":
        bet["result_display"] = "Perdida"
        bet["result_class"] = "loss"
    elif r == "push":
        bet["result_display"] = "Push"
        bet["result_class"] = "push"
    else:
        bet["result_display"] = "Pendiente"
        bet["result_class"] = "pending"

    pnl = bet["pnl"]
    if pnl is not None:
        bet["pnl_display"] = f"{'+'  if pnl >= 0 else ''}{pnl:.2f}"
    else:
        bet["pnl_display"] = "—"

    # Ganancia potencial: stake * (odds - 1)
    odds = bet.get("odds_at_bet") or 0
    stake = bet.get("stake") or 0
    bet["potential_win"] = round(stake * (odds - 1), 2) if odds > 1 else 0

    return bet


def group_bets_by_event_date(bets):
    """Agrupa apuestas pendientes por fecha del evento."""
    from collections import OrderedDict
    groups = OrderedDict()
    now_cr = datetime.now(timezone.utc).astimezone(CR_TZ)
    today = now_cr.date()

    DAYS_ES = ["Lunes", "Martes", "Mi\u00e9rcoles", "Jueves", "Viernes", "S\u00e1bado", "Domingo"]
    MONTHS_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

    for bet in bets:
        ct = bet.get("commence_time")
        if ct:
            try:
                if isinstance(ct, str):
                    ct_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                else:
                    ct_dt = ct
                if ct_dt.tzinfo is None:
                    ct_dt = ct_dt.replace(tzinfo=timezone.utc)
                event_date = ct_dt.astimezone(CR_TZ).date()
            except Exception:
                event_date = today
        else:
            event_date = today

        if event_date not in groups:
            diff_days = (event_date - today).days
            if diff_days == 0:
                label = "Hoy"
                dot_class = "dot-today"
            elif diff_days == 1:
                label = "Ma\u00f1ana"
                dot_class = "dot-today"
            elif diff_days <= 7:
                label = DAYS_ES[event_date.weekday()]
                dot_class = "dot-week"
            else:
                label = DAYS_ES[event_date.weekday()]
                dot_class = "dot-later"

            date_str = f"{event_date.day} {MONTHS_ES[event_date.month]}"
            groups[event_date] = {
                "label": label,
                "date_str": date_str,
                "dot_class": dot_class,
                "bets": [],
                "total_stake": 0,
            }

        groups[event_date]["bets"].append(bet)
        groups[event_date]["total_stake"] += bet.get("stake") or 0

    # Round totals
    for g in groups.values():
        g["total_stake"] = round(g["total_stake"], 2)
        g["count"] = len(g["bets"])

    return list(dict(sorted(groups.items())).values())


@app.route("/")
def index():
    bets = get_bets()
    stats = compute_stats(bets)
    display_bets = [format_bet_for_display(b) for b in bets]

    # Separar por tipo: value vs arb
    value_bets = [b for b in display_bets if b.get("bet_type", "value") == "value"]
    arb_bets = [b for b in display_bets if b.get("bet_type") == "arb"]

    pending_bets = [b for b in value_bets if b["result"] is None]
    settled_bets = [b for b in value_bets if b["result"] is not None]
    pending_groups = group_bets_by_event_date(pending_bets)

    # Arb: agrupar por arb_group_id
    arb_groups = {}
    for b in arb_bets:
        gid = b.get("arb_group_id") or b["id"]
        if gid not in arb_groups:
            arb_groups[gid] = {
                "group_id": gid,
                "event": f"{b['home_team']} vs {b['away_team']}",
                "market": b["market"],
                "profit_percent": b.get("arb_profit_percent") or 0,
                "legs": [],
                "total_stake": 0,
                "result": None,
                "total_pnl": 0,
            }
        arb_groups[gid]["legs"].append(b)
        arb_groups[gid]["total_stake"] += b.get("stake") or 0
        if b["result"] is not None:
            arb_groups[gid]["result"] = b["result"]
            arb_groups[gid]["total_pnl"] += b.get("pnl") or 0

    # Stats por tipo
    value_stats = compute_stats(value_bets)
    arb_stats = {
        "total": len(arb_groups),
        "total_staked": round(sum(g["total_stake"] for g in arb_groups.values()), 2),
        "total_pnl": round(sum(g["total_pnl"] for g in arb_groups.values()), 2),
        "avg_profit": round(
            sum(g["profit_percent"] for g in arb_groups.values()) / len(arb_groups), 2
        ) if arb_groups else 0,
    }

    now_cr = datetime.now(timezone.utc).astimezone(CR_TZ)
    return render_template(
        "dashboard.html",
        stats=stats,
        value_stats=value_stats,
        arb_stats=arb_stats,
        bets=display_bets,
        pending_groups=pending_groups,
        settled_bets=settled_bets,
        arb_groups=list(arb_groups.values()),
        now=now_cr.strftime("%d/%m/%Y %H:%M"),
        active_tab="value",
    )


@app.route("/arbitrage")
def arbitrage():
    bets = get_bets()
    display_bets = [format_bet_for_display(b) for b in bets]
    arb_bets = [b for b in display_bets if b.get("bet_type") == "arb"]

    arb_groups = {}
    for b in arb_bets:
        gid = b.get("arb_group_id") or b["id"]
        if gid not in arb_groups:
            arb_groups[gid] = {
                "group_id": gid,
                "event": f"{b['home_team']} vs {b['away_team']}",
                "market": b["market"],
                "profit_percent": b.get("arb_profit_percent") or 0,
                "legs": [],
                "total_stake": 0,
                "result": None,
                "total_pnl": 0,
            }
        arb_groups[gid]["legs"].append(b)
        arb_groups[gid]["total_stake"] += b.get("stake") or 0
        if b["result"] is not None:
            arb_groups[gid]["result"] = b["result"]
            arb_groups[gid]["total_pnl"] += b.get("pnl") or 0

    arb_stats = {
        "total": len(arb_groups),
        "total_staked": round(sum(g["total_stake"] for g in arb_groups.values()), 2),
        "total_pnl": round(sum(g["total_pnl"] for g in arb_groups.values()), 2),
        "avg_profit": round(
            sum(g["profit_percent"] for g in arb_groups.values()) / len(arb_groups), 2
        ) if arb_groups else 0,
    }

    exposure = get_exposure_data()
    now_cr = datetime.now(timezone.utc).astimezone(CR_TZ)
    return render_template(
        "arbitrage.html",
        arb_groups=list(arb_groups.values()),
        arb_stats=arb_stats,
        exposure=exposure,
        now=now_cr.strftime("%d/%m/%Y %H:%M"),
        active_tab="arb",
    )


@app.route("/exposure")
def exposure_page():
    exposure = get_exposure_data()
    now_cr = datetime.now(timezone.utc).astimezone(CR_TZ)
    return render_template(
        "exposure.html",
        exposure=exposure,
        now=now_cr.strftime("%d/%m/%Y %H:%M"),
        active_tab="exposure",
    )


@app.route("/api")
def api_tokens_page():
    api_info = get_api_info()
    now_cr = datetime.now(timezone.utc).astimezone(CR_TZ)
    return render_template(
        "api_tokens.html",
        api_info=api_info,
        now=now_cr.strftime("%d/%m/%Y %H:%M"),
        active_tab="api",
    )


@app.route("/events")
def events_page():
    events_by_sport, total_events, last_scout_time = get_events_data()
    now_cr = datetime.now(timezone.utc).astimezone(CR_TZ)
    return render_template(
        "events.html",
        events_by_sport=events_by_sport,
        total_events=total_events,
        last_scout_time=last_scout_time,
        now=now_cr.strftime("%d/%m/%Y %H:%M"),
        active_tab="events",
    )


def get_exposure_data():
    """Calcula datos de exposición separados por estrategia."""
    session = Session()
    try:
        initial = 500.0
        try:
            all_bets_q = session.execute(text("""
                SELECT id, created_at, stake, result, pnl,
                       COALESCE(bet_type, 'value') as bet_type,
                       home_team, away_team, outcome_name, book_title
                FROM bets ORDER BY id DESC
            """))
        except Exception:
            # Table doesn't exist yet
            return _empty_exposure(initial)
        columns = all_bets_q.keys()
        all_bets = [dict(zip(columns, row)) for row in all_bets_q.fetchall()]

        settled = [b for b in all_bets if b["result"] is not None]
        realized_pnl = sum(b["pnl"] or 0 for b in settled)
        realized_bankroll = initial + realized_pnl
        loss_pct = (1 - realized_bankroll / initial) * 100 if initial > 0 else 0

        bankroll = realized_bankroll
        pending = [b for b in all_bets if b["result"] is None]
        for b in pending:
            bankroll -= b["stake"] or 0

        today = datetime.now(timezone.utc).date()
        today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)

        def calc_exposure(bets_list, bet_type_filter=None):
            filtered = bets_list
            if bet_type_filter:
                filtered = [b for b in filtered if b["bet_type"] == bet_type_filter]
            daily = sum(b["stake"] or 0 for b in filtered
                        if b["created_at"] and (
                            b["created_at"] if hasattr(b["created_at"], 'date') else
                            datetime.fromisoformat(str(b["created_at"]))
                        ).date() >= today)
            total_open = sum(b["stake"] or 0 for b in filtered if b["result"] is None)
            return round(daily, 2), round(total_open, 2)

        daily_value, total_value = calc_exposure(all_bets, "value")
        daily_arb, total_arb = calc_exposure(all_bets, "arb")

        max_daily_value = max(bankroll, 1) * 0.10
        max_total_value = max(bankroll, 1) * 0.30
        max_total_arb = max(bankroll, 1) * 0.30

        today_bets = []
        for b in all_bets:
            dt = b["created_at"]
            if dt:
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                if hasattr(dt, 'date') and dt.date() >= today:
                    today_bets.append({
                        "id": b["id"],
                        "event": f"{b['home_team']} vs {b['away_team']}",
                        "bet_type": b["bet_type"],
                        "stake": round(b["stake"] or 0, 2),
                        "outcome": b["outcome_name"],
                        "book": b["book_title"],
                    })

        return {
            "value": {
                "daily_used": daily_value,
                "daily_limit": round(max_daily_value, 2),
                "daily_pct": round(daily_value / max_daily_value * 100, 1) if max_daily_value > 0 else 0,
                "total_used": total_value,
                "total_limit": round(max_total_value, 2),
                "total_pct": round(total_value / max_total_value * 100, 1) if max_total_value > 0 else 0,
            },
            "arb": {
                "daily_used": daily_arb,
                "daily_limit": None,
                "daily_pct": None,
                "total_used": total_arb,
                "total_limit": round(max_total_arb, 2),
                "total_pct": round(total_arb / max_total_arb * 100, 1) if max_total_arb > 0 else 0,
            },
            "stop_loss": {
                "realized_pnl": round(realized_pnl, 2),
                "realized_bankroll": round(realized_bankroll, 2),
                "loss_pct": round(max(loss_pct, 0), 1),
                "threshold": 15.0,
                "is_active": loss_pct >= 15.0,
            },
            "today_bets": today_bets,
            "bankroll": round(max(bankroll, 0), 2),
        }
    finally:
        session.close()


def _empty_exposure(initial=500.0):
    """Retorna datos de exposición vacíos (cuando no hay DB)."""
    limit_daily = initial * 0.10
    limit_total = initial * 0.30
    return {
        "value": {
            "daily_used": 0, "daily_limit": round(limit_daily, 2), "daily_pct": 0,
            "total_used": 0, "total_limit": round(limit_total, 2), "total_pct": 0,
        },
        "arb": {
            "daily_used": 0, "daily_limit": None, "daily_pct": None,
            "total_used": 0, "total_limit": round(limit_total, 2), "total_pct": 0,
        },
        "stop_loss": {
            "realized_pnl": 0, "realized_bankroll": initial,
            "loss_pct": 0, "threshold": 15.0, "is_active": False,
        },
        "today_bets": [],
        "bankroll": initial,
    }


def get_api_info():
    """Retorna info de consumo de API."""
    scan_interval = 45
    sports_count = 7
    markets_count = 3
    cost_per_scan = sports_count * markets_count + 4  # odds + scores
    scans_per_day = int(24 * 60 / scan_interval)
    daily_cost = cost_per_scan * scans_per_day

    # Try to read API usage from the scanner's last known state
    remaining = None
    used = None
    try:
        result = Session().execute(text(
            "SELECT COUNT(*) as total FROM bets"
        ))
        total_bets = result.scalar() or 0
        # Estimate: each bet required at least 1 scan cycle
        # More accurate tracking would need a dedicated table
        used_estimate = total_bets * 2  # rough estimate
    except Exception:
        pass

    # Check env for any cached API usage
    remaining_str = os.getenv("ODDS_API_REMAINING")
    used_str = os.getenv("ODDS_API_USED")
    if remaining_str:
        remaining = int(remaining_str)
    if used_str:
        used = int(used_str)

    days_remaining = None
    if remaining and daily_cost > 0:
        days_remaining = round(remaining / daily_cost)

    return {
        "remaining": remaining,
        "used": used,
        "cost_per_scan": cost_per_scan,
        "daily_cost": daily_cost,
        "scans_per_day": scans_per_day,
        "scan_interval": scan_interval,
        "days_remaining": days_remaining,
    }


def get_events_data():
    """Retorna eventos disponibles del último scouting."""
    import json
    from collections import OrderedDict

    events_file = os.path.join(os.path.dirname(__file__), "..", "paradigma", "last_scout.json")
    events_by_sport = OrderedDict()
    total_events = 0
    last_scout_time = None

    # Get pending event IDs to mark which ones have bets
    pending_event_ids = set()
    try:
        session = Session()
        result = session.execute(text("SELECT DISTINCT event_id FROM bets WHERE result IS NULL"))
        pending_event_ids = {row[0] for row in result.fetchall()}
        session.close()
    except Exception:
        pass

    try:
        if os.path.exists(events_file):
            with open(events_file, "r") as f:
                data = json.load(f)
            last_scout_time = data.get("timestamp", None)
            if last_scout_time:
                try:
                    dt = datetime.fromisoformat(last_scout_time.replace("Z", "+00:00"))
                    last_scout_time = dt.astimezone(CR_TZ).strftime("%d/%m %H:%M CR")
                except Exception:
                    pass

            for sport_key, sport_events in data.get("sports", {}).items():
                sport_title = sport_events[0].get("sport_title", sport_key) if sport_events else sport_key
                formatted_events = []
                seen_ids = set()
                for ev in sport_events:
                    eid = ev.get("id")
                    if eid in seen_ids:
                        continue
                    seen_ids.add(eid)

                    ct = ev.get("commence_time", "")
                    ct_display = "—"
                    ct_full = "—"
                    try:
                        ct_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                        cr_dt = ct_dt.astimezone(CR_TZ)
                        ct_display = cr_dt.strftime("%d/%m %H:%M")
                        ct_full = cr_dt.strftime("%d/%m/%Y %H:%M")
                    except Exception:
                        pass

                    formatted_events.append({
                        "id": eid,
                        "home_team": ev.get("home_team", "?"),
                        "away_team": ev.get("away_team", "?"),
                        "commence_time_cr": ct_display,
                        "commence_time_full": ct_full,
                        "has_bet": eid in pending_event_ids,
                    })

                if formatted_events:
                    events_by_sport[sport_key] = {
                        "title": sport_title,
                        "events": formatted_events,
                    }
                    total_events += len(formatted_events)
    except Exception:
        pass

    return events_by_sport, total_events, last_scout_time


@app.route("/api/stats")
def api_stats():
    bets = get_bets()
    stats = compute_stats(bets)
    return jsonify(stats)


@app.route("/api/bets")
def api_bets():
    bets = get_bets()
    display_bets = [format_bet_for_display(b) for b in bets]
    return jsonify(display_bets)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
