"""
models.py — Prediction engines
Three models: Corners, GG (Both Teams Score), Goals Total (Over/Under)
Each returns a score 0-100 and a pick.
Qualifying thresholds: Corners 88+, GG 85+, Goals 80+
"""

import math
from corners_stats import get_corners_data


# ─── Thresholds ───────────────────────────────────────────────────────────────

CORNERS_THRESHOLD = 88
GG_THRESHOLD      = 85
GOALS_THRESHOLD   = 80

SUPPRESSION_MULT  = 0.80   # applied to high-stakes / defensive fixtures


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def predict_fixture(enriched: dict) -> dict | None:
    """
    Run all three models on an enriched fixture dict.
    enriched = fixture + home_stats + away_stats + h2h (from fetcher.py)
    Returns a flat prediction dict ready for DB + Telegram.
    """
    home_stats = enriched.get("home_stats") or {}
    away_stats = enriched.get("away_stats") or {}
    h2h        = enriched.get("h2h") or {}
    league     = enriched.get("league", "")

    # Pull corners from api-sports statistics (cached)
    corners_data = get_corners_data(
        enriched["home_team_id"],
        enriched["away_team_id"],
        enriched.get("league_id", 39),
        enriched.get("season", 2024),
    )
    home_corners_data = {
        "corners_avg":          corners_data.get("home_corners_avg", 5.0),
        "corners_home_avg":     corners_data.get("home_corners_home_avg", 5.0),
        "corners_conceded_avg": corners_data.get("home_corners_conceded", 4.5),
    }
    away_corners_data = {
        "corners_avg":          corners_data.get("away_corners_avg", 4.5),
        "corners_away_avg":     corners_data.get("away_corners_away_avg", 4.5),
        "corners_conceded_avg": corners_data.get("away_corners_conceded", 5.0),
    }

    # Build feature block
    features = _build_features(home_stats, away_stats, h2h,
                                home_corners_data, away_corners_data)

    # Detect suppression context
    suppressed = _is_suppressed(enriched)

    # Run models
    corners_result = _corners_model(features, suppressed)
    gg_result      = _gg_model(features, suppressed)
    goals_result   = _goals_model(features, suppressed)

    return {
        # Identity
        "fixture_id":  enriched["fixture_id"],
        "match_date":  enriched["match_date"],
        "match_time":  enriched.get("match_time", ""),
        "home_team":   enriched["home_team"],
        "away_team":   enriched["away_team"],
        "league":      league,
        "league_id":   enriched.get("league_id"),
        "season":      enriched.get("season", 2024),

        # Features (ML training data)
        **features,

        # Model outputs
        "corners_score": corners_result["score"],
        "corners_pick":  corners_result["pick"],
        "corners_line":  corners_result["line"],
        "gg_score":      gg_result["score"],
        "gg_pick":       gg_result["pick"],
        "goals_score":   goals_result["score"],
        "goals_pick":    goals_result["pick"],
        "goals_line":    goals_result["line"],
    }


# ─── Feature Builder ──────────────────────────────────────────────────────────

def _build_features(home_stats: dict, away_stats: dict, h2h: dict,
                    home_c: dict, away_c: dict) -> dict:

    def sf(d, key, default=0.0):
        try:
            return float(d.get(key) or default)
        except:
            return float(default)

    return {
        "home_goals_scored_avg":    sf(home_stats, "home_goals_scored_avg"),
        "home_goals_conceded_avg":  sf(home_stats, "home_goals_conceded_avg"),
        "away_goals_scored_avg":    sf(away_stats, "away_goals_scored_avg"),
        "away_goals_conceded_avg":  sf(away_stats, "away_goals_conceded_avg"),
        "home_gg_rate":             sf(home_stats, "gg_rate"),
        "away_gg_rate":             sf(away_stats, "gg_rate"),
        "home_over25_rate":         sf(home_stats, "over25_rate"),
        "away_over25_rate":         sf(away_stats, "over25_rate"),
        "home_form_pts":            int(sf(home_stats, "form_pts", 0)),
        "away_form_pts":            int(sf(away_stats, "form_pts", 0)),

        # Corners — FBRef preferred, fallback to league avg proxies
        "home_corners_avg":         sf(home_c, "corners_avg",        5.0),
        "away_corners_avg":         sf(away_c, "corners_avg",        4.5),
        "home_corners_home_avg":    sf(home_c, "corners_home_avg",   5.0),
        "away_corners_away_avg":    sf(away_c, "corners_away_avg",   4.5),
        "home_corners_conceded":    sf(home_c, "corners_conceded_avg", 4.5),
        "away_corners_conceded":    sf(away_c, "corners_conceded_avg", 5.0),

        # H2H
        "h2h_avg_goals":    sf(h2h, "avg_goals"),
        "h2h_gg_rate":      sf(h2h, "gg_rate"),
        "h2h_avg_corners":  sf(h2h, "avg_corners"),
        "h2h_over25_rate":  sf(h2h, "over25_rate"),
    }


# ─── Suppression Detector ─────────────────────────────────────────────────────

def _is_suppressed(fixture: dict) -> bool:
    """
    Returns True for high-stakes or known defensive fixtures.
    Applies 0.80x multiplier to all model scores.
    """
    league = fixture.get("league", "").lower()
    home   = fixture.get("home_team", "").lower()
    away   = fixture.get("away_team", "").lower()

    # European knockout rounds are always cautious
    european = ["champions league", "europa league", "conference league"]
    if any(e in league for e in european):
        return True

    # Known defensive derbies
    derby_pairs = [
        {"atletico", "real madrid"},
        {"atletico", "barcelona"},
        {"inter", "juventus"},
        {"milan", "juventus"},
        {"liverpool", "manchester united"},
    ]
    teams = {home, away}
    for pair in derby_pairs:
        if pair.issubset(teams):
            return True

    return False


# ─── Corners Model ────────────────────────────────────────────────────────────

def _corners_model(f: dict, suppressed: bool) -> dict:
    """
    Score = how confidently expected corners total beats the line.
    Uses home/away specific averages + conceded context + H2H.
    Falls back to league proxy values (5.0 / 4.5) if FBRef not yet scraped.
    Threshold: 88+
    """
    # Expected corners per team using venue-specific averages
    home_exp = _wavg([
        (f["home_corners_home_avg"], 0.50),  # venue-specific (priority)
        (f["home_corners_avg"],      0.25),  # overall avg
        (f["away_corners_conceded"], 0.25),  # opponent concedes how many?
    ])

    away_exp = _wavg([
        (f["away_corners_away_avg"], 0.50),
        (f["away_corners_avg"],      0.25),
        (f["home_corners_conceded"], 0.25),
    ])

    expected_total = home_exp + away_exp

    # Blend in H2H corners if available
    if f["h2h_avg_corners"] > 0:
        expected_total = expected_total * 0.70 + f["h2h_avg_corners"] * 0.30

    # Pick line
    line = _corners_line(expected_total)

    # Score based on margin over line
    margin    = expected_total - line
    raw_score = 50 + (margin / line) * 100 if line > 0 else 50
    raw_score = max(0.0, min(100.0, raw_score))

    # Dominance boost — big corners gap between teams
    gap = abs(f["home_corners_avg"] - f["away_corners_avg"])
    if gap >= 2.0:
        raw_score += 4

    if suppressed:
        raw_score *= SUPPRESSION_MULT

    score = round(min(100.0, raw_score), 1)
    pick  = "OVER" if score >= CORNERS_THRESHOLD else ("UNDER" if score < 45 else None)

    return {"score": score, "pick": pick, "line": line}


def _corners_line(expected: float) -> float:
    for line in [7.5, 8.5, 9.5, 10.5, 11.5, 12.5]:
        if expected < line + 0.5:
            return line
    return 12.5


# ─── GG Model ─────────────────────────────────────────────────────────────────

def _gg_model(f: dict, suppressed: bool) -> dict:
    """
    Both Teams Score model.
    Requires double-lock: both teams must independently clear 55% scoring prob.
    Threshold: 85+
    """
    home_score_prob   = _poisson_score_prob(f["home_goals_scored_avg"])
    away_score_prob   = _poisson_score_prob(f["away_goals_scored_avg"])
    home_concede_prob = _poisson_score_prob(f["home_goals_conceded_avg"])
    away_concede_prob = _poisson_score_prob(f["away_goals_conceded_avg"])

    raw_score = (
        f["home_gg_rate"]   * 0.20 +
        f["away_gg_rate"]   * 0.20 +
        home_score_prob     * 0.15 +
        away_score_prob     * 0.15 +
        home_concede_prob   * 0.10 +
        away_concede_prob   * 0.10 +
        f["h2h_gg_rate"]    * 0.10
    ) * 100

    # Double lock — if either team unlikely to score, penalise
    if home_score_prob < 0.55 or away_score_prob < 0.55:
        raw_score *= 0.75

    # Form boost
    if f["home_form_pts"] >= 9 and f["away_form_pts"] >= 9:
        raw_score += 3

    if suppressed:
        raw_score *= SUPPRESSION_MULT

    score = round(min(100.0, max(0.0, raw_score)), 1)
    pick  = "YES" if score >= GG_THRESHOLD else ("NO" if score < 40 else None)

    return {"score": score, "pick": pick}


# ─── Goals Model ──────────────────────────────────────────────────────────────

def _goals_model(f: dict, suppressed: bool) -> dict:
    """
    Goals Total Over/Under model.
    Projects expected goals using attack vs defence + H2H blend.
    Threshold: 80+
    """
    h2h_half = f["h2h_avg_goals"] / 2 if f["h2h_avg_goals"] > 0 else 0.0

    home_xg = _wavg([
        (f["home_goals_scored_avg"],  0.45),
        (f["away_goals_conceded_avg"], 0.35),
        (h2h_half,                    0.20),
    ])

    away_xg = _wavg([
        (f["away_goals_scored_avg"],  0.45),
        (f["home_goals_conceded_avg"], 0.35),
        (h2h_half,                    0.20),
    ])

    expected_total = home_xg + away_xg
    line           = _goals_line(expected_total)

    margin    = expected_total - line
    raw_score = 50 + (margin / max(line, 1)) * 80
    raw_score = max(0.0, min(100.0, raw_score))

    # H2H over25 adjustment
    if f["h2h_over25_rate"] >= 0.60 and line <= 2.5:
        raw_score += 5
    elif f["h2h_over25_rate"] <= 0.35 and line >= 2.5:
        raw_score -= 5

    if suppressed:
        raw_score *= SUPPRESSION_MULT

    score = round(min(100.0, max(0.0, raw_score)), 1)
    pick  = "OVER" if score >= GOALS_THRESHOLD else ("UNDER" if score < 35 else None)

    return {"score": score, "pick": pick, "line": line}


def _goals_line(expected: float) -> float:
    for line in [1.5, 2.5, 3.5]:
        if expected < line + 0.3:
            return line
    return 3.5


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _wavg(pairs: list) -> float:
    """Weighted average from [(value, weight), ...] pairs."""
    total_w = sum(w for _, w in pairs)
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in pairs) / total_w


def _poisson_score_prob(goals_avg: float) -> float:
    """P(team scores >= 1) using Poisson: 1 - e^(-lambda)."""
    if goals_avg <= 0:
        return 0.0
    return round(1 - math.exp(-goals_avg), 3)


# ─── CLI Test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Simulate an enriched fixture — no DB needed
    test = {
        "fixture_id":  99999,
        "match_date":  "2025-05-10",
        "match_time":  "15:00",
        "home_team":   "Arsenal",
        "away_team":   "Chelsea",
        "league":      "Premier League",
        "league_id":   39,
        "season":      2024,
        "home_stats": {
            "home_goals_scored_avg":   1.8,
            "home_goals_conceded_avg": 0.9,
            "gg_rate":                 0.65,
            "over25_rate":             0.70,
            "form_pts":                12,
        },
        "away_stats": {
            "away_goals_scored_avg":   1.4,
            "away_goals_conceded_avg": 1.2,
            "gg_rate":                 0.60,
            "over25_rate":             0.62,
            "form_pts":                9,
        },
        "h2h": {
            "avg_goals":   2.8,
            "gg_rate":     0.70,
            "avg_corners": 10.5,
            "over25_rate": 0.75,
        },
    }

    # Patch get_corners_data for test
    import corners_stats
    corners_stats.get_corners_data = lambda h, a, l, s=2024: {
        "home_corners_avg":      5.8,
        "home_corners_home_avg": 6.5,
        "home_corners_conceded": 4.0,
        "away_corners_avg":      4.2,
        "away_corners_away_avg": 3.6,
        "away_corners_conceded": 5.2,
        "h2h_avg_corners":       10.5,
    }

    result = predict_fixture(test)

    print("\n" + "=" * 45)
    print(f"⚽  {result['home_team']} vs {result['away_team']}")
    print(f"🏆  {result['league']}")
    print("=" * 45)
    print(f"🔵 Corners : {result['corners_pick']} {result['corners_line']}  [score: {result['corners_score']}]")
    print(f"🟢 GG      : {result['gg_pick']}             [score: {result['gg_score']}]")
    print(f"🟡 Goals   : {result['goals_pick']} {result['goals_line']}       [score: {result['goals_score']}]")
    print()
    qualify = []
    if (result["corners_score"] or 0) >= CORNERS_THRESHOLD: qualify.append("Corners ✅")
    if (result["gg_score"]      or 0) >= GG_THRESHOLD:      qualify.append("GG ✅")
    if (result["goals_score"]   or 0) >= GOALS_THRESHOLD:   qualify.append("Goals ✅")
    print(f"Qualifying: {', '.join(qualify) if qualify else 'None — below thresholds'}")
