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
                   clv_percent, settled_at, is_paper
            FROM bets
            ORDER BY id DESC
        """))
        columns = result.keys()
        return [dict(zip(columns, row)) for row in result.fetchall()]
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

    pending_bets = [b for b in display_bets if b["result"] is None]
    settled_bets = [b for b in display_bets if b["result"] is not None]
    pending_groups = group_bets_by_event_date(pending_bets)

    now_cr = datetime.now(timezone.utc).astimezone(CR_TZ)
    return render_template(
        "dashboard.html",
        stats=stats,
        bets=display_bets,
        pending_groups=pending_groups,
        settled_bets=settled_bets,
        now=now_cr.strftime("%d/%m/%Y %H:%M"),
    )


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
